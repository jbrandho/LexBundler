import sqlite3
from pathlib import Path
from uuid import UUID

import pytest

from lexbundler.domain.errors import ProjectMigrationError
from lexbundler.persistence.sqlite.migrations import Migration
from lexbundler.persistence.sqlite.project_store import SQLiteProjectStoreFactory
from lexbundler.persistence.sqlite.schema import (
    CURRENT_SCHEMA_VERSION,
    FORMAT_ID,
)

V2_TABLES = {
    "processing_run",
    "corpus_source",
    "source_unit",
    "asset",
    "asset_location",
    "asset_binding",
}


def create_real_v1_project(path: Path) -> dict[str, object]:
    expected = {
        "project_uuid": "69f6e99f-d46e-4bb5-bf5a-ec4869019ad8",
        "name": "Existing Corpus",
        "primary_language_tag": "ja",
        "primary_language_name": "Japanese",
        "created_at": "2026-08-30T10:15:32Z",
        "updated_at": "2026-08-30T10:15:32Z",
    }
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE lexbundler_schema (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                format_id TEXT NOT NULL,
                schema_version INTEGER NOT NULL CHECK (schema_version >= 1)
            )
            """
        )
        connection.execute(
            "INSERT INTO lexbundler_schema VALUES (1, ?, 1)", (FORMAT_ID,)
        )
        connection.execute(
            """
            CREATE TABLE project (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                project_uuid TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL CHECK (length(trim(name)) > 0),
                primary_language_tag TEXT,
                primary_language_name TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO project (
                singleton, project_uuid, name, primary_language_tag,
                primary_language_name, created_at, updated_at
            ) VALUES (1, ?, ?, ?, ?, ?, ?)
            """,
            tuple(expected.values()),
        )
    return expected


def test_current_schema_version_is_two() -> None:
    assert CURRENT_SCHEMA_VERSION == 2


def test_new_project_is_created_directly_with_complete_v2_schema(
    tmp_path: Path,
) -> None:
    from datetime import UTC, datetime
    from uuid import uuid4

    from lexbundler.domain.project import ProjectMetadata

    path = tmp_path / "new.lexbundler"
    now = datetime(2026, 8, 31, 9, 0, tzinfo=UTC)
    SQLiteProjectStoreFactory().create(
        path,
        ProjectMetadata(uuid4(), "New", None, None, now, now),
    ).close()

    with sqlite3.connect(path) as connection:
        version = connection.execute(
            "SELECT schema_version FROM lexbundler_schema"
        ).fetchone()[0]
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert version == 2
    assert V2_TABLES <= tables


def test_real_v1_project_migrates_without_changing_metadata(tmp_path: Path) -> None:
    path = tmp_path / "existing.lexbundler"
    expected = create_real_v1_project(path)

    store = SQLiteProjectStoreFactory().open(path)
    actual = store.metadata
    store.close()

    assert actual.project_uuid == UUID(expected["project_uuid"])
    assert actual.name == expected["name"]
    assert actual.primary_language_tag == expected["primary_language_tag"]
    assert actual.primary_language_name == expected["primary_language_name"]
    assert actual.created_at.isoformat().replace("+00:00", "Z") == expected["created_at"]

    with sqlite3.connect(path) as connection:
        version = connection.execute(
            "SELECT schema_version FROM lexbundler_schema"
        ).fetchone()[0]
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        counts = {
            table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in V2_TABLES
        }

    assert version == 2
    assert V2_TABLES <= tables
    assert counts == {table: 0 for table in V2_TABLES}


def test_reopening_v2_does_not_change_database(tmp_path: Path) -> None:
    path = tmp_path / "existing.lexbundler"
    create_real_v1_project(path)
    factory = SQLiteProjectStoreFactory()
    factory.open(path).close()
    after_migration = path.read_bytes()

    factory.open(path).close()

    assert path.read_bytes() == after_migration


def test_production_migration_failure_leaves_v1_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "existing.lexbundler"
    create_real_v1_project(path)

    def fail_after_ddl(connection: sqlite3.Connection) -> None:
        connection.execute("CREATE TABLE should_roll_back (id INTEGER)")
        raise RuntimeError("synthetic production migration failure")

    monkeypatch.setattr(
        "lexbundler.persistence.sqlite.project_store.MIGRATIONS",
        (Migration(2, fail_after_ddl),),
    )

    with pytest.raises(ProjectMigrationError):
        SQLiteProjectStoreFactory().open(path)

    with sqlite3.connect(path) as connection:
        version = connection.execute(
            "SELECT schema_version FROM lexbundler_schema"
        ).fetchone()[0]
        rolled_back_table = connection.execute(
            "SELECT count(*) FROM sqlite_master WHERE name = 'should_roll_back'"
        ).fetchone()[0]
    assert version == 1
    assert rolled_back_table == 0
