"""Authoritative SQLite project format and current production schema."""

import sqlite3

FORMAT_ID = "lexbundler-project"
CURRENT_SCHEMA_VERSION = 1


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

