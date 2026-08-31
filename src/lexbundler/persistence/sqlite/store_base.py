"""Shared connection ownership for focused SQLite project stores."""

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from lexbundler.domain.errors import (
    CorpusIntegrityError,
    CorpusStorageError,
)
from lexbundler.persistence.sqlite.database import connect


class SQLiteStoreBase:
    """Own a project path and create short-lived backend connections."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._closed = False

    def close(self) -> None:
        self._closed = True

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
                "The operation violates project data integrity."
            ) from error
        except ValueError as error:
            if connection is not None and connection.in_transaction:
                connection.execute("ROLLBACK")
            raise CorpusIntegrityError("Stored project data is malformed.") from error
        except sqlite3.Error as error:
            if connection is not None and connection.in_transaction:
                connection.execute("ROLLBACK")
            raise CorpusStorageError("The project database operation failed.") from error
        except Exception:
            if connection is not None and connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            if connection is not None:
                connection.close()

