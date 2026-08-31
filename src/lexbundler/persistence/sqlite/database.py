"""SQLite connection configuration shared by backend-specific stores."""

import sqlite3
from pathlib import Path


def connect(path: Path, *, existing: bool = False) -> sqlite3.Connection:
    if existing:
        database = f"{path.resolve().as_uri()}?mode=rw"
        connection = sqlite3.connect(database, uri=True, isolation_level=None)
    else:
        connection = sqlite3.connect(path, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection

