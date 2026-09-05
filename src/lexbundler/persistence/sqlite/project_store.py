"""SQLite-backed LexBundler project persistence."""

import os
import tempfile
import sqlite3
from datetime import datetime
from pathlib import Path
from uuid import UUID

from lexbundler.domain.errors import (
    InvalidMigrationStateError,
    InvalidProjectError,
    ProjectAlreadyExistsError,
    ProjectError,
    ProjectNotFoundError,
    UnsupportedSchemaVersionError,
)
from lexbundler.domain.project import ProjectMetadata
from lexbundler.persistence.sqlite.corpus_store import SQLiteCorpusStore
from lexbundler.persistence.sqlite.database import connect as _connect
from lexbundler.persistence.sqlite.migrations import Migration, run_migrations
from lexbundler.persistence.sqlite.serialization import (
    format_utc as _format_utc,
    parse_utc as _parse_utc,
)
from lexbundler.persistence.sqlite.schema import (
    CURRENT_SCHEMA_VERSION,
    FORMAT_ID,
    create_corpus_schema_v2,
    create_current_schema,
    create_text_segment_schema_v3,
)
from lexbundler.persistence.sqlite.text_segment_store import SQLiteTextSegmentStore
from lexbundler.persistence.sqlite.resource_ingestion_store import (
    SQLiteResourceIngestionStore,
)


def _migrate_to_v2(connection: sqlite3.Connection) -> None:
    create_corpus_schema_v2(connection)


def _migrate_to_v3(connection: sqlite3.Connection) -> None:
    create_text_segment_schema_v3(connection)


MIGRATIONS: tuple[Migration, ...] = (
    Migration(2, _migrate_to_v2),
    Migration(3, _migrate_to_v3),
)


class SQLiteProjectStore(
    SQLiteCorpusStore, SQLiteTextSegmentStore, SQLiteResourceIngestionStore
):
    """An opened SQLite project using short-lived operation connections."""

    def __init__(self, path: Path, metadata: ProjectMetadata) -> None:
        super().__init__(path)
        self._path = path
        self._metadata = metadata

    @property
    def metadata(self) -> ProjectMetadata:
        return self._metadata

    def close(self) -> None:
        """Release store resources.

        Connections are operation-scoped, so there is no persistent handle to close.
        """
        super().close()


class SQLiteProjectStoreFactory:
    """Create, validate, migrate, and open SQLite project files."""

    def create(
        self, destination: Path, metadata: ProjectMetadata
    ) -> SQLiteProjectStore:
        path = Path(destination)
        if path.exists():
            raise ProjectAlreadyExistsError(f"A file already exists at {path}.")
        if not path.parent.is_dir():
            raise InvalidProjectError(
                f"The destination directory does not exist: {path.parent}"
            )

        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, delete=False
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)

            with _connect(temporary_path) as connection:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    create_current_schema(connection)
                    _insert_project(connection, metadata)
                    connection.execute("COMMIT")
                except Exception:
                    if connection.in_transaction:
                        connection.execute("ROLLBACK")
                    raise

            validated_metadata = self._load(temporary_path, allow_migrations=False)
            try:
                os.link(temporary_path, path)
            except FileExistsError as error:
                raise ProjectAlreadyExistsError(
                    f"A file already exists at {path}."
                ) from error
            return SQLiteProjectStore(path, validated_metadata)
        except ProjectError:
            raise
        except (OSError, sqlite3.Error, ValueError) as error:
            raise InvalidProjectError(f"Could not create project at {path}.") from error
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    def open(self, location: Path) -> SQLiteProjectStore:
        path = Path(location)
        if not path.exists() or not path.is_file():
            raise ProjectNotFoundError(f"Project file was not found: {path}")
        metadata = self._load(path, allow_migrations=True)
        return SQLiteProjectStore(path, metadata)

    def _load(self, path: Path, *, allow_migrations: bool) -> ProjectMetadata:
        try:
            with _connect(path, existing=True) as connection:
                format_id, schema_version = _read_schema_identity(connection)
                if format_id != FORMAT_ID:
                    raise InvalidProjectError(
                        "The database is not a LexBundler project."
                    )
                if schema_version > CURRENT_SCHEMA_VERSION:
                    raise UnsupportedSchemaVersionError(
                        schema_version, CURRENT_SCHEMA_VERSION
                    )
                if schema_version < CURRENT_SCHEMA_VERSION:
                    if not allow_migrations:
                        raise InvalidMigrationStateError(
                            "A newly created project has an obsolete schema."
                        )
                    run_migrations(
                        connection,
                        current_version=schema_version,
                        target_version=CURRENT_SCHEMA_VERSION,
                        migrations=MIGRATIONS,
                        write_version=_write_schema_version,
                    )
                return _read_project(connection)
        except ProjectError:
            raise
        except (sqlite3.Error, ValueError) as error:
            raise InvalidProjectError(
                f"The file is not a valid LexBundler project: {path}"
            ) from error


def _insert_project(
    connection: sqlite3.Connection, metadata: ProjectMetadata
) -> None:
    connection.execute(
        """
        INSERT INTO project (
            singleton,
            project_uuid,
            name,
            primary_language_tag,
            primary_language_name,
            created_at,
            updated_at
        ) VALUES (1, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(metadata.project_uuid),
            metadata.name,
            metadata.primary_language_tag,
            metadata.primary_language_name,
            _format_utc(metadata.created_at),
            _format_utc(metadata.updated_at),
        ),
    )


def _read_schema_identity(connection: sqlite3.Connection) -> tuple[str, int]:
    rows = connection.execute(
        "SELECT format_id, schema_version FROM lexbundler_schema"
    ).fetchall()
    if len(rows) != 1:
        raise InvalidProjectError(
            "The LexBundler schema metadata must contain exactly one row."
        )
    format_id = rows[0]["format_id"]
    schema_version = rows[0]["schema_version"]
    if not isinstance(format_id, str) or not isinstance(schema_version, int):
        raise InvalidProjectError("The LexBundler schema metadata is malformed.")
    return format_id, schema_version


def _write_schema_version(connection: sqlite3.Connection, version: int) -> None:
    cursor = connection.execute(
        "UPDATE lexbundler_schema SET schema_version = ? WHERE singleton = 1",
        (version,),
    )
    if cursor.rowcount != 1:
        raise InvalidMigrationStateError(
            "The authoritative schema-version row is missing."
        )


def _read_project(connection: sqlite3.Connection) -> ProjectMetadata:
    rows = connection.execute(
        """
        SELECT project_uuid, name, primary_language_tag, primary_language_name,
               created_at, updated_at
        FROM project
        """
    ).fetchall()
    if len(rows) != 1:
        raise InvalidProjectError(
            "A LexBundler project must contain exactly one project metadata row."
        )
    row = rows[0]
    try:
        return ProjectMetadata(
            project_uuid=UUID(row["project_uuid"]),
            name=row["name"],
            primary_language_tag=row["primary_language_tag"],
            primary_language_name=row["primary_language_name"],
            created_at=_parse_utc(row["created_at"]),
            updated_at=_parse_utc(row["updated_at"]),
        )
    except (TypeError, ValueError) as error:
        raise InvalidProjectError("Project metadata is malformed.") from error
