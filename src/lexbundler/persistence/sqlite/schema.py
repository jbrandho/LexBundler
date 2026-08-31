"""Authoritative SQLite project format and current production schema."""

import sqlite3

FORMAT_ID = "lexbundler-project"
CURRENT_SCHEMA_VERSION = 2


def create_current_schema(connection: sqlite3.Connection) -> None:
    """Create the current schema inside the caller's transaction."""
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
        """
        INSERT INTO lexbundler_schema (singleton, format_id, schema_version)
        VALUES (1, ?, ?)
        """,
        (FORMAT_ID, CURRENT_SCHEMA_VERSION),
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
    create_corpus_schema_v2(connection)


def create_corpus_schema_v2(connection: sqlite3.Connection) -> None:
    """Add the generic source, asset, and provenance tables introduced in v2."""
    connection.execute(
        """
        CREATE TABLE processing_run (
            id INTEGER PRIMARY KEY,
            run_uuid TEXT NOT NULL UNIQUE,
            process_type TEXT NOT NULL CHECK (length(trim(process_type)) > 0),
            tool_name TEXT,
            tool_version TEXT,
            parameters_json TEXT NOT NULL,
            status TEXT NOT NULL CHECK (
                status IN ('running', 'succeeded', 'failed', 'cancelled')
            ),
            started_at TEXT NOT NULL,
            completed_at TEXT,
            CHECK (
                (status = 'running' AND completed_at IS NULL)
                OR (status <> 'running' AND completed_at IS NOT NULL)
            )
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE corpus_source (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL CHECK (length(trim(name)) > 0),
            source_kind TEXT,
            language_tag TEXT,
            external_id TEXT,
            metadata_json TEXT NOT NULL,
            created_by_run_id INTEGER REFERENCES processing_run(id),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE source_unit (
            id INTEGER PRIMARY KEY,
            source_id INTEGER NOT NULL REFERENCES corpus_source(id) ON DELETE CASCADE,
            parent_id INTEGER,
            kind TEXT NOT NULL CHECK (length(trim(kind)) > 0),
            label TEXT NOT NULL CHECK (length(trim(label)) > 0),
            sequence INTEGER,
            external_id TEXT,
            metadata_json TEXT NOT NULL,
            created_by_run_id INTEGER REFERENCES processing_run(id),
            confidence REAL CHECK (
                confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0)
            ),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (source_id, id),
            FOREIGN KEY (source_id, parent_id)
                REFERENCES source_unit(source_id, id) ON DELETE CASCADE,
            CHECK (parent_id IS NULL OR parent_id <> id)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE asset (
            id INTEGER PRIMARY KEY,
            sha256 TEXT NOT NULL UNIQUE CHECK (
                length(sha256) = 64 AND sha256 NOT GLOB '*[^0-9a-f]*'
            ),
            byte_size INTEGER NOT NULL CHECK (byte_size >= 0),
            asset_kind TEXT,
            mime_type TEXT,
            created_by_run_id INTEGER REFERENCES processing_run(id),
            created_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE asset_location (
            id INTEGER PRIMARY KEY,
            asset_id INTEGER NOT NULL REFERENCES asset(id) ON DELETE CASCADE,
            location_kind TEXT NOT NULL CHECK (length(trim(location_kind)) > 0),
            location TEXT NOT NULL CHECK (length(location) > 0),
            created_by_run_id INTEGER REFERENCES processing_run(id),
            observed_at TEXT NOT NULL,
            UNIQUE (asset_id, location_kind, location)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE asset_binding (
            id INTEGER PRIMARY KEY,
            source_id INTEGER NOT NULL REFERENCES corpus_source(id) ON DELETE CASCADE,
            source_unit_id INTEGER,
            asset_id INTEGER NOT NULL REFERENCES asset(id),
            role TEXT,
            assignment_method TEXT,
            confidence REAL CHECK (
                confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0)
            ),
            processing_run_id INTEGER REFERENCES processing_run(id),
            metadata_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (source_id, source_unit_id)
                REFERENCES source_unit(source_id, id) ON DELETE CASCADE
        )
        """
    )
    connection.execute(
        "CREATE INDEX source_unit_parent_idx ON source_unit(source_id, parent_id)"
    )
    connection.execute(
        "CREATE INDEX asset_location_asset_idx ON asset_location(asset_id)"
    )
    connection.execute(
        "CREATE INDEX asset_binding_source_idx ON asset_binding(source_id)"
    )
    connection.execute(
        "CREATE INDEX asset_binding_asset_idx ON asset_binding(asset_id)"
    )
