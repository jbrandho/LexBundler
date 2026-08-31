import sqlite3
from pathlib import Path

import pytest

from lexbundler.application.project_service import ProjectService
from lexbundler.domain.errors import ProjectMigrationError
from lexbundler.persistence.sqlite.migrations import Migration
from lexbundler.persistence.sqlite.project_store import SQLiteProjectStoreFactory
from lexbundler.persistence.sqlite.schema import (
    FORMAT_ID,
    create_corpus_schema_v2,
)

V2_DATA_TABLES = (
    "processing_run",
    "corpus_source",
    "source_unit",
    "asset",
    "asset_location",
    "asset_binding",
)
V3_TABLES = {
    "text_representation",
    "segment_layer",
    "segment",
    "segment_text_span",
    "segment_media_span",
    "speaker",
    "segment_speaker",
}


def create_populated_v2_project(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
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
            "INSERT INTO lexbundler_schema VALUES (1, ?, 2)", (FORMAT_ID,)
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
            """INSERT INTO project VALUES
               (1, 'ee6df180-d193-4e20-819f-b66e9b2e3c47', 'Populated Corpus',
                'ar', 'Arabic', '2026-08-31T08:00:00Z', '2026-08-31T08:00:00Z')"""
        )
        create_corpus_schema_v2(connection)
        connection.execute(
            """INSERT INTO processing_run VALUES
               (11, 'ebdf3595-97c2-46ac-88c5-2f026b214dad', 'asset_import',
                'LexBundler', '0.1.0', '{"recursive":false}', 'succeeded',
                '2026-08-31T08:01:00Z', '2026-08-31T08:02:00Z')"""
        )
        connection.execute(
            """INSERT INTO corpus_source VALUES
               (21, 'Archive', 'collection', 'ar', 'archive-1', '{"edition":1}',
                11, '2026-08-31T08:01:00Z', '2026-08-31T08:01:00Z')"""
        )
        connection.execute(
            """INSERT INTO source_unit VALUES
               (31, 21, NULL, 'section', 'Part One', 1, 'part-1', '{}', 11,
                0.75, '2026-08-31T08:01:00Z', '2026-08-31T08:01:00Z')"""
        )
        connection.execute(
            """INSERT INTO asset VALUES
               (41, 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
                12, 'document', 'application/pdf', 11, '2026-08-31T08:01:00Z')"""
        )
        connection.execute(
            """INSERT INTO asset_location VALUES
               (51, 41, 'filesystem', '/synthetic/archive.pdf', 11,
                '2026-08-31T08:01:00Z')"""
        )
        connection.execute(
            """INSERT INTO asset_binding VALUES
               (61, 21, 31, 41, 'source_document', 'importer', 0.9, 11, '{}',
                '2026-08-31T08:01:00Z')"""
        )


def table_rows(path: Path, tables: tuple[str, ...]) -> dict[str, list[tuple]]:
    with sqlite3.connect(path) as connection:
        return {
            table: connection.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
            for table in tables
        }


def test_populated_real_v2_migrates_to_v3_without_data_changes(
    tmp_path: Path,
) -> None:
    path = tmp_path / "populated.lexbundler"
    create_populated_v2_project(path)
    before_project = table_rows(path, ("project",))
    before_corpus = table_rows(path, V2_DATA_TABLES)

    SQLiteProjectStoreFactory().open(path).close()

    assert table_rows(path, ("project",)) == before_project
    assert table_rows(path, V2_DATA_TABLES) == before_corpus
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
            for table in V3_TABLES
        }
    assert version == 3
    assert V3_TABLES <= tables
    assert counts == {table: 0 for table in V3_TABLES}


def test_new_project_contains_v3_tables_without_running_migrations(
    tmp_path: Path,
) -> None:
    path = tmp_path / "new.lexbundler"
    service = ProjectService(SQLiteProjectStoreFactory())
    service.create_project(path, name="New v3")
    service.close_project()

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
    assert version == 3
    assert V3_TABLES <= tables


def test_v3_migration_failure_rolls_back_ddl_and_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "rollback.lexbundler"
    create_populated_v2_project(path)
    before = table_rows(path, V2_DATA_TABLES)

    def fail(connection: sqlite3.Connection) -> None:
        connection.execute("CREATE TABLE partial_v3 (id INTEGER)")
        raise RuntimeError("synthetic v3 failure")

    monkeypatch.setattr(
        "lexbundler.persistence.sqlite.project_store.MIGRATIONS",
        (Migration(3, fail),),
    )
    with pytest.raises(ProjectMigrationError):
        SQLiteProjectStoreFactory().open(path)

    assert table_rows(path, V2_DATA_TABLES) == before
    with sqlite3.connect(path) as connection:
        version = connection.execute(
            "SELECT schema_version FROM lexbundler_schema"
        ).fetchone()[0]
        partial = connection.execute(
            "SELECT count(*) FROM sqlite_master WHERE name = 'partial_v3'"
        ).fetchone()[0]
    assert version == 2
    assert partial == 0


def test_reopening_v3_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "current.lexbundler"
    create_populated_v2_project(path)
    factory = SQLiteProjectStoreFactory()
    factory.open(path).close()
    migrated = path.read_bytes()

    factory.open(path).close()

    assert path.read_bytes() == migrated
