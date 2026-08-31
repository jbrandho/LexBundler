import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from lexbundler.domain.errors import (
    InvalidProjectError,
    ProjectAlreadyExistsError,
    ProjectNotFoundError,
    UnsupportedSchemaVersionError,
)
from lexbundler.domain.project import ProjectMetadata
from lexbundler.persistence.sqlite.project_store import (
    SQLiteProjectStoreFactory,
    _connect,
)
from lexbundler.persistence.sqlite.schema import CURRENT_SCHEMA_VERSION, FORMAT_ID


def metadata() -> ProjectMetadata:
    now = datetime(2026, 8, 30, 10, 15, 32, tzinfo=UTC)
    return ProjectMetadata(
        project_uuid=uuid4(),
        name="Mandarin Corpus",
        primary_language_tag="zh-Hans",
        primary_language_name="Mandarin Chinese",
        created_at=now,
        updated_at=now,
    )


def test_create_writes_valid_sqlite_format_schema_and_metadata(
    tmp_path: Path,
) -> None:
    path = tmp_path / "Mandarin.lexbundler"
    expected = metadata()
    store = SQLiteProjectStoreFactory().create(path, expected)
    store.close()

    with path.open("rb") as project_file:
        assert project_file.read(16) == b"SQLite format 3\x00"
    with sqlite3.connect(path) as connection:
        schema = connection.execute(
            "SELECT format_id, schema_version FROM lexbundler_schema"
        ).fetchone()
        project = connection.execute(
            """SELECT project_uuid, name, primary_language_tag,
                      primary_language_name, created_at, updated_at
               FROM project"""
        ).fetchone()

    assert schema == (FORMAT_ID, CURRENT_SCHEMA_VERSION)
    assert project == (
        str(expected.project_uuid),
        expected.name,
        expected.primary_language_tag,
        expected.primary_language_name,
        "2026-08-30T10:15:32Z",
        "2026-08-30T10:15:32Z",
    )


def test_existing_destination_is_never_overwritten(tmp_path: Path) -> None:
    path = tmp_path / "existing.lexbundler"
    original = b"keep this exact content"
    path.write_bytes(original)

    with pytest.raises(ProjectAlreadyExistsError):
        SQLiteProjectStoreFactory().create(path, metadata())

    assert path.read_bytes() == original


def test_missing_project_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ProjectNotFoundError):
        SQLiteProjectStoreFactory().open(tmp_path / "missing.lexbundler")


@pytest.mark.parametrize("contents", [b"not sqlite", b"SQLite format 3\x00broken"])
def test_non_sqlite_or_truncated_input_is_rejected(
    tmp_path: Path, contents: bytes
) -> None:
    path = tmp_path / "bad.lexbundler"
    path.write_bytes(contents)

    with pytest.raises(InvalidProjectError):
        SQLiteProjectStoreFactory().open(path)


def test_arbitrary_sqlite_database_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "ordinary.lexbundler"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE unrelated (value TEXT)")

    with pytest.raises(InvalidProjectError):
        SQLiteProjectStoreFactory().open(path)


def test_newer_schema_is_rejected_without_modifying_file(tmp_path: Path) -> None:
    path = tmp_path / "future.lexbundler"
    factory = SQLiteProjectStoreFactory()
    factory.create(path, metadata()).close()
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE lexbundler_schema SET schema_version = ?",
            (CURRENT_SCHEMA_VERSION + 1,),
        )
    before = path.read_bytes()

    with pytest.raises(UnsupportedSchemaVersionError) as error:
        factory.open(path)

    assert error.value.found == CURRENT_SCHEMA_VERSION + 1
    assert path.read_bytes() == before


def test_backend_connections_enable_foreign_keys(tmp_path: Path) -> None:
    path = tmp_path / "connection.sqlite"
    with _connect(path) as connection:
        enabled = connection.execute("PRAGMA foreign_keys").fetchone()[0]

    assert enabled == 1

