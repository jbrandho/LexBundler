"""Small transactional migration runner for SQLite project schemas."""

import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from lexbundler.domain.errors import (
    InvalidMigrationStateError,
    ProjectMigrationError,
)

MigrationOperation = Callable[[sqlite3.Connection], None]
VersionWriter = Callable[[sqlite3.Connection, int], None]


@dataclass(frozen=True, slots=True)
class Migration:
    """One migration whose target is the next ordered schema version."""

    target_version: int
    apply: MigrationOperation


def run_migrations(
    connection: sqlite3.Connection,
    *,
    current_version: int,
    target_version: int,
    migrations: Sequence[Migration],
    write_version: VersionWriter,
) -> None:
    """Run each required migration in its own explicit transaction."""
    if current_version < 1 or target_version < 1 or current_version > target_version:
        raise InvalidMigrationStateError(
            f"Invalid migration range {current_version} -> {target_version}."
        )

    versions = [migration.target_version for migration in migrations]
    if versions != sorted(versions) or len(versions) != len(set(versions)):
        raise InvalidMigrationStateError(
            "Migration versions must be unique and ordered increasingly."
        )

    migration_by_version = {
        migration.target_version: migration for migration in migrations
    }
    for version in range(current_version + 1, target_version + 1):
        migration = migration_by_version.get(version)
        if migration is None:
            raise InvalidMigrationStateError(
                f"No migration is registered for schema version {version}."
            )

        try:
            connection.execute("BEGIN IMMEDIATE")
            migration.apply(connection)
            write_version(connection, version)
            connection.execute("COMMIT")
        except Exception as error:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise ProjectMigrationError(
                f"Migration to schema version {version} failed."
            ) from error

