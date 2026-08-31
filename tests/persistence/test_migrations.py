import sqlite3

import pytest

from lexbundler.domain.errors import (
    InvalidMigrationStateError,
    ProjectMigrationError,
)
from lexbundler.persistence.sqlite.migrations import Migration, run_migrations


def connection_at_version(version: int = 1) -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:", isolation_level=None)
    connection.execute("CREATE TABLE schema_state (version INTEGER NOT NULL)")
    connection.execute("INSERT INTO schema_state VALUES (?)", (version,))
    return connection


def write_version(connection: sqlite3.Connection, version: int) -> None:
    connection.execute("UPDATE schema_state SET version = ?", (version,))


def test_migrations_run_sequentially_and_update_version() -> None:
    connection = connection_at_version()
    order: list[int] = []

    def migrate_to_2(database: sqlite3.Connection) -> None:
        order.append(2)
        database.execute("CREATE TABLE version_two (value TEXT)")

    def migrate_to_3(database: sqlite3.Connection) -> None:
        order.append(3)
        database.execute("CREATE TABLE version_three (value TEXT)")

    run_migrations(
        connection,
        current_version=1,
        target_version=3,
        migrations=(Migration(2, migrate_to_2), Migration(3, migrate_to_3)),
        write_version=write_version,
    )

    assert order == [2, 3]
    assert connection.execute("SELECT version FROM schema_state").fetchone()[0] == 3
    assert connection.execute(
        "SELECT count(*) FROM sqlite_master WHERE name IN (?, ?)",
        ("version_two", "version_three"),
    ).fetchone()[0] == 2
    connection.close()


def test_failed_migration_rolls_back_operation_and_version() -> None:
    connection = connection_at_version()
    connection.execute("CREATE TABLE changes (value TEXT)")

    def failing_migration(database: sqlite3.Connection) -> None:
        database.execute("INSERT INTO changes VALUES ('not committed')")
        raise RuntimeError("synthetic failure")

    with pytest.raises(ProjectMigrationError) as error:
        run_migrations(
            connection,
            current_version=1,
            target_version=2,
            migrations=(Migration(2, failing_migration),),
            write_version=write_version,
        )

    assert isinstance(error.value.__cause__, RuntimeError)
    assert connection.execute("SELECT count(*) FROM changes").fetchone()[0] == 0
    assert connection.execute("SELECT version FROM schema_state").fetchone()[0] == 1
    connection.close()


@pytest.mark.parametrize(
    ("current", "target", "migrations"),
    [
        (0, 1, ()),
        (2, 1, ()),
        (1, 3, (Migration(3, lambda connection: None),)),
        (
            1,
            3,
            (
                Migration(3, lambda connection: None),
                Migration(2, lambda connection: None),
            ),
        ),
    ],
)
def test_invalid_migration_state_is_rejected(
    current: int, target: int, migrations: tuple[Migration, ...]
) -> None:
    connection = connection_at_version(max(current, 1))

    with pytest.raises(InvalidMigrationStateError):
        run_migrations(
            connection,
            current_version=current,
            target_version=target,
            migrations=migrations,
            write_version=write_version,
        )

    connection.close()

