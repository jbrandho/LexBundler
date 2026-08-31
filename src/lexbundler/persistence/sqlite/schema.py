"""Authoritative SQLite project format and current production schema."""

import sqlite3

FORMAT_ID = "lexbundler-project"
CURRENT_SCHEMA_VERSION = 3


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
    create_text_segment_schema_v3(connection)


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


def create_text_segment_schema_v3(connection: sqlite3.Connection) -> None:
    """Add immutable text representations and analytical segmentation tables."""
    connection.execute(
        """
        CREATE TABLE text_representation (
            id INTEGER PRIMARY KEY,
            source_id INTEGER NOT NULL REFERENCES corpus_source(id) ON DELETE CASCADE,
            source_unit_id INTEGER,
            representation_kind TEXT NOT NULL
                CHECK (length(trim(representation_kind)) > 0),
            language_tag TEXT,
            content TEXT NOT NULL,
            source_asset_id INTEGER REFERENCES asset(id),
            derived_from_id INTEGER,
            created_by_run_id INTEGER REFERENCES processing_run(id),
            metadata_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE (source_id, id),
            FOREIGN KEY (source_id, source_unit_id)
                REFERENCES source_unit(source_id, id) ON DELETE CASCADE,
            FOREIGN KEY (source_id, derived_from_id)
                REFERENCES text_representation(source_id, id),
            CHECK (derived_from_id IS NULL OR derived_from_id <> id)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE segment_layer (
            id INTEGER PRIMARY KEY,
            source_id INTEGER NOT NULL REFERENCES corpus_source(id) ON DELETE CASCADE,
            source_unit_id INTEGER,
            name TEXT NOT NULL CHECK (length(trim(name)) > 0),
            layer_kind TEXT NOT NULL CHECK (length(trim(layer_kind)) > 0),
            language_tag TEXT,
            created_by_run_id INTEGER REFERENCES processing_run(id),
            metadata_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE (source_id, id),
            FOREIGN KEY (source_id, source_unit_id)
                REFERENCES source_unit(source_id, id) ON DELETE CASCADE
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE segment (
            id INTEGER PRIMARY KEY,
            layer_id INTEGER NOT NULL REFERENCES segment_layer(id) ON DELETE CASCADE,
            parent_id INTEGER,
            kind TEXT NOT NULL CHECK (length(trim(kind)) > 0),
            label TEXT,
            sequence INTEGER,
            external_id TEXT,
            confidence REAL CHECK (
                confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0)
            ),
            created_by_run_id INTEGER REFERENCES processing_run(id),
            metadata_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE (layer_id, id),
            FOREIGN KEY (layer_id, parent_id)
                REFERENCES segment(layer_id, id) ON DELETE CASCADE,
            CHECK (parent_id IS NULL OR parent_id <> id)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE segment_text_span (
            id INTEGER PRIMARY KEY,
            segment_id INTEGER NOT NULL REFERENCES segment(id) ON DELETE CASCADE,
            text_representation_id INTEGER NOT NULL
                REFERENCES text_representation(id),
            start_offset INTEGER NOT NULL CHECK (
                typeof(start_offset) = 'integer' AND start_offset >= 0
            ),
            end_offset INTEGER NOT NULL CHECK (
                typeof(end_offset) = 'integer' AND end_offset > start_offset
            ),
            role TEXT,
            confidence REAL CHECK (
                confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0)
            ),
            created_by_run_id INTEGER REFERENCES processing_run(id),
            metadata_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE segment_media_span (
            id INTEGER PRIMARY KEY,
            segment_id INTEGER NOT NULL REFERENCES segment(id) ON DELETE CASCADE,
            asset_id INTEGER NOT NULL REFERENCES asset(id),
            start_ms INTEGER NOT NULL CHECK (
                typeof(start_ms) = 'integer' AND start_ms >= 0
            ),
            end_ms INTEGER NOT NULL CHECK (
                typeof(end_ms) = 'integer' AND end_ms > start_ms
            ),
            role TEXT,
            confidence REAL CHECK (
                confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0)
            ),
            created_by_run_id INTEGER REFERENCES processing_run(id),
            metadata_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE speaker (
            id INTEGER PRIMARY KEY,
            source_id INTEGER NOT NULL REFERENCES corpus_source(id) ON DELETE CASCADE,
            name TEXT NOT NULL CHECK (length(trim(name)) > 0),
            external_id TEXT,
            created_by_run_id INTEGER REFERENCES processing_run(id),
            metadata_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE segment_speaker (
            id INTEGER PRIMARY KEY,
            segment_id INTEGER NOT NULL REFERENCES segment(id) ON DELETE CASCADE,
            speaker_id INTEGER NOT NULL REFERENCES speaker(id),
            role TEXT,
            confidence REAL CHECK (
                confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0)
            ),
            created_by_run_id INTEGER REFERENCES processing_run(id),
            metadata_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """CREATE INDEX text_representation_source_idx
           ON text_representation(source_id, source_unit_id)"""
    )
    connection.execute(
        """CREATE INDEX text_representation_asset_idx
           ON text_representation(source_asset_id)"""
    )
    connection.execute(
        """CREATE INDEX segment_layer_source_idx
           ON segment_layer(source_id, source_unit_id)"""
    )
    connection.execute(
        "CREATE INDEX segment_parent_idx ON segment(layer_id, parent_id)"
    )
    connection.execute(
        "CREATE INDEX segment_text_span_segment_idx ON segment_text_span(segment_id)"
    )
    connection.execute(
        """CREATE INDEX segment_text_span_representation_idx
           ON segment_text_span(text_representation_id, start_offset)"""
    )
    connection.execute(
        "CREATE INDEX segment_media_span_segment_idx ON segment_media_span(segment_id)"
    )
    connection.execute(
        """CREATE INDEX segment_media_span_asset_idx
           ON segment_media_span(asset_id, start_ms)"""
    )
    connection.execute("CREATE INDEX speaker_source_idx ON speaker(source_id)")
    connection.execute(
        """CREATE INDEX segment_speaker_segment_idx
           ON segment_speaker(segment_id, speaker_id)"""
    )
