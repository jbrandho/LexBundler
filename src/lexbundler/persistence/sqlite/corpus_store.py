"""SQLite implementation of generic corpus source and evidence operations."""

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator
from uuid import UUID

from lexbundler.domain.corpus import (
    Asset,
    AssetBinding,
    AssetLocation,
    CorpusSource,
    JsonObject,
    ProcessingRun,
    SourceUnit,
)
from lexbundler.domain.errors import (
    CorpusEntityNotFoundError,
    CorpusIntegrityError,
    CorpusStorageError,
)
from lexbundler.persistence.sqlite.database import connect
from lexbundler.persistence.sqlite.serialization import (
    dump_json,
    format_utc,
    load_json,
    parse_utc,
)


class SQLiteCorpusStore:
    """Corpus operations backed by short-lived SQLite connections."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._closed = False

    def close(self) -> None:
        self._closed = True

    def create_source(
        self,
        *,
        name: str,
        source_kind: str | None,
        language_tag: str | None,
        external_id: str | None,
        metadata: JsonObject,
        created_by_run_id: int | None,
        created_at: datetime,
    ) -> CorpusSource:
        timestamp = format_utc(created_at)
        with self._connection(write=True) as connection:
            cursor = connection.execute(
                """
                INSERT INTO corpus_source (
                    name, source_kind, language_tag, external_id, metadata_json,
                    created_by_run_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    name,
                    source_kind,
                    language_tag,
                    external_id,
                    dump_json(metadata),
                    created_by_run_id,
                    timestamp,
                    timestamp,
                ),
            )
            return _source_from_row(_row_by_id(connection, "corpus_source", cursor.lastrowid))

    def get_source(self, source_id: int) -> CorpusSource:
        with self._connection() as connection:
            return _source_from_row(_row_by_id(connection, "corpus_source", source_id))

    def list_sources(self) -> list[CorpusSource]:
        with self._connection() as connection:
            rows = connection.execute("SELECT * FROM corpus_source ORDER BY id").fetchall()
            return [_source_from_row(row) for row in rows]

    def create_source_unit(
        self,
        *,
        source_id: int,
        parent_id: int | None,
        kind: str,
        label: str,
        sequence: int | None,
        external_id: str | None,
        metadata: JsonObject,
        created_by_run_id: int | None,
        confidence: float | None,
        created_at: datetime,
    ) -> SourceUnit:
        timestamp = format_utc(created_at)
        with self._connection(write=True) as connection:
            cursor = connection.execute(
                """
                INSERT INTO source_unit (
                    source_id, parent_id, kind, label, sequence, external_id,
                    metadata_json, created_by_run_id, confidence, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source_id,
                    parent_id,
                    kind,
                    label,
                    sequence,
                    external_id,
                    dump_json(metadata),
                    created_by_run_id,
                    confidence,
                    timestamp,
                    timestamp,
                ),
            )
            return _unit_from_row(_row_by_id(connection, "source_unit", cursor.lastrowid))

    def get_source_unit(self, unit_id: int) -> SourceUnit:
        with self._connection() as connection:
            return _unit_from_row(_row_by_id(connection, "source_unit", unit_id))

    def list_source_units(self, source_id: int) -> list[SourceUnit]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM source_unit
                WHERE source_id = ?
                ORDER BY CASE WHEN sequence IS NULL THEN 1 ELSE 0 END, sequence, id
                """,
                (source_id,),
            ).fetchall()
            return [_unit_from_row(row) for row in rows]

    def register_asset(
        self,
        *,
        sha256: str,
        byte_size: int,
        asset_kind: str | None,
        mime_type: str | None,
        location_kind: str,
        location: str,
        created_by_run_id: int | None,
        observed_at: datetime,
    ) -> Asset:
        timestamp = format_utc(observed_at)
        with self._connection(write=True) as connection:
            row = connection.execute(
                "SELECT * FROM asset WHERE sha256 = ?", (sha256,)
            ).fetchone()
            if row is None:
                cursor = connection.execute(
                    """
                    INSERT INTO asset (
                        sha256, byte_size, asset_kind, mime_type,
                        created_by_run_id, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        sha256,
                        byte_size,
                        asset_kind,
                        mime_type,
                        created_by_run_id,
                        timestamp,
                    ),
                )
                row = _row_by_id(connection, "asset", cursor.lastrowid)
            elif row["byte_size"] != byte_size:
                raise CorpusIntegrityError(
                    "An existing SHA-256 asset has an inconsistent byte size."
                )

            connection.execute(
                """
                INSERT INTO asset_location (
                    asset_id, location_kind, location, created_by_run_id, observed_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(asset_id, location_kind, location) DO NOTHING
                """,
                (row["id"], location_kind, location, created_by_run_id, timestamp),
            )
            return _asset_from_row(row)

    def get_asset(self, asset_id: int) -> Asset:
        with self._connection() as connection:
            return _asset_from_row(_row_by_id(connection, "asset", asset_id))

    def find_asset_by_sha256(self, sha256: str) -> Asset | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM asset WHERE sha256 = ?", (sha256,)
            ).fetchone()
            return None if row is None else _asset_from_row(row)

    def list_asset_locations(self, asset_id: int) -> list[AssetLocation]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM asset_location WHERE asset_id = ? ORDER BY id",
                (asset_id,),
            ).fetchall()
            return [_location_from_row(row) for row in rows]

    def create_asset_binding(
        self,
        *,
        source_id: int,
        source_unit_id: int | None,
        asset_id: int,
        role: str | None,
        assignment_method: str | None,
        confidence: float | None,
        processing_run_id: int | None,
        metadata: JsonObject,
        created_at: datetime,
    ) -> AssetBinding:
        with self._connection(write=True) as connection:
            cursor = connection.execute(
                """
                INSERT INTO asset_binding (
                    source_id, source_unit_id, asset_id, role, assignment_method,
                    confidence, processing_run_id, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source_id,
                    source_unit_id,
                    asset_id,
                    role,
                    assignment_method,
                    confidence,
                    processing_run_id,
                    dump_json(metadata),
                    format_utc(created_at),
                ),
            )
            return _binding_from_row(
                _row_by_id(connection, "asset_binding", cursor.lastrowid)
            )

    def list_asset_bindings(self, source_id: int) -> list[AssetBinding]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM asset_binding WHERE source_id = ? ORDER BY id",
                (source_id,),
            ).fetchall()
            return [_binding_from_row(row) for row in rows]

    def start_processing_run(
        self,
        *,
        run_uuid: UUID,
        process_type: str,
        tool_name: str | None,
        tool_version: str | None,
        parameters: JsonObject,
        started_at: datetime,
    ) -> ProcessingRun:
        with self._connection(write=True) as connection:
            cursor = connection.execute(
                """
                INSERT INTO processing_run (
                    run_uuid, process_type, tool_name, tool_version,
                    parameters_json, status, started_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, 'running', ?, NULL)
                """,
                (
                    str(run_uuid),
                    process_type,
                    tool_name,
                    tool_version,
                    dump_json(parameters),
                    format_utc(started_at),
                ),
            )
            return _run_from_row(
                _row_by_id(connection, "processing_run", cursor.lastrowid)
            )

    def finish_processing_run(
        self, run_id: int, *, status: str, completed_at: datetime
    ) -> ProcessingRun:
        with self._connection(write=True) as connection:
            cursor = connection.execute(
                """
                UPDATE processing_run
                SET status = ?, completed_at = ?
                WHERE id = ?
                """,
                (status, format_utc(completed_at), run_id),
            )
            if cursor.rowcount != 1:
                raise CorpusEntityNotFoundError(
                    f"Processing run {run_id} does not exist."
                )
            return _run_from_row(_row_by_id(connection, "processing_run", run_id))

    def get_processing_run(self, run_id: int) -> ProcessingRun:
        with self._connection() as connection:
            return _run_from_row(_row_by_id(connection, "processing_run", run_id))

    @contextmanager
    def _connection(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        if self._closed:
            raise CorpusStorageError("The project store is closed.")
        connection: sqlite3.Connection | None = None
        try:
            connection = connect(self._path, existing=True)
            if write:
                connection.execute("BEGIN IMMEDIATE")
            yield connection
            if write:
                connection.execute("COMMIT")
        except CorpusIntegrityError:
            if connection is not None and connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        except sqlite3.IntegrityError as error:
            if connection is not None and connection.in_transaction:
                connection.execute("ROLLBACK")
            raise CorpusIntegrityError(
                "The corpus operation violates project data integrity."
            ) from error
        except ValueError as error:
            if connection is not None and connection.in_transaction:
                connection.execute("ROLLBACK")
            raise CorpusIntegrityError(
                "Stored corpus data is malformed."
            ) from error
        except sqlite3.Error as error:
            if connection is not None and connection.in_transaction:
                connection.execute("ROLLBACK")
            raise CorpusStorageError("The corpus database operation failed.") from error
        except Exception:
            if connection is not None and connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            if connection is not None:
                connection.close()


def _row_by_id(
    connection: sqlite3.Connection, table: str, entity_id: object
) -> sqlite3.Row:
    # Table names are internal constants supplied only by this module.
    row = connection.execute(
        f"SELECT * FROM {table} WHERE id = ?", (entity_id,)
    ).fetchone()
    if row is None:
        raise CorpusEntityNotFoundError(f"{table} record {entity_id} does not exist.")
    return row


def _source_from_row(row: sqlite3.Row) -> CorpusSource:
    return CorpusSource(
        id=row["id"],
        name=row["name"],
        source_kind=row["source_kind"],
        language_tag=row["language_tag"],
        external_id=row["external_id"],
        metadata=load_json(row["metadata_json"]),
        created_by_run_id=row["created_by_run_id"],
        created_at=parse_utc(row["created_at"]),
        updated_at=parse_utc(row["updated_at"]),
    )


def _unit_from_row(row: sqlite3.Row) -> SourceUnit:
    return SourceUnit(
        id=row["id"],
        source_id=row["source_id"],
        parent_id=row["parent_id"],
        kind=row["kind"],
        label=row["label"],
        sequence=row["sequence"],
        external_id=row["external_id"],
        metadata=load_json(row["metadata_json"]),
        created_by_run_id=row["created_by_run_id"],
        confidence=row["confidence"],
        created_at=parse_utc(row["created_at"]),
        updated_at=parse_utc(row["updated_at"]),
    )


def _asset_from_row(row: sqlite3.Row) -> Asset:
    return Asset(
        id=row["id"],
        sha256=row["sha256"],
        byte_size=row["byte_size"],
        asset_kind=row["asset_kind"],
        mime_type=row["mime_type"],
        created_by_run_id=row["created_by_run_id"],
        created_at=parse_utc(row["created_at"]),
    )


def _location_from_row(row: sqlite3.Row) -> AssetLocation:
    return AssetLocation(
        id=row["id"],
        asset_id=row["asset_id"],
        location_kind=row["location_kind"],
        location=row["location"],
        created_by_run_id=row["created_by_run_id"],
        observed_at=parse_utc(row["observed_at"]),
    )


def _binding_from_row(row: sqlite3.Row) -> AssetBinding:
    return AssetBinding(
        id=row["id"],
        source_id=row["source_id"],
        source_unit_id=row["source_unit_id"],
        asset_id=row["asset_id"],
        role=row["role"],
        assignment_method=row["assignment_method"],
        confidence=row["confidence"],
        processing_run_id=row["processing_run_id"],
        metadata=load_json(row["metadata_json"]),
        created_at=parse_utc(row["created_at"]),
    )


def _run_from_row(row: sqlite3.Row) -> ProcessingRun:
    return ProcessingRun(
        id=row["id"],
        run_uuid=UUID(row["run_uuid"]),
        process_type=row["process_type"],
        tool_name=row["tool_name"],
        tool_version=row["tool_version"],
        parameters=load_json(row["parameters_json"]),
        status=row["status"],
        started_at=parse_utc(row["started_at"]),
        completed_at=(
            None if row["completed_at"] is None else parse_utc(row["completed_at"])
        ),
    )
