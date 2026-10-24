"""SQLite connection ownership, transactional migrations and nested transactions."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from importlib.resources import files
from pathlib import Path
from types import TracebackType


def _statements(sql: str) -> Iterator[str]:
    """Separate SQLite statements without splitting trigger bodies or quoted semicolons."""
    pending: list[str] = []
    for character in sql:
        pending.append(character)
        if character == ";" and sqlite3.complete_statement(statement := "".join(pending)):
            yield statement
            pending.clear()
    if remainder := "".join(pending).strip():
        # Let SQLite validate an unterminated statement; errors must roll back the script.
        yield remainder


def apply_migration(connection: sqlite3.Connection, sql: str, *, version: int) -> None:
    """Lock, recheck the version, and apply trusted SQL atomically if still needed.

    Use execute rather than executescript, whose implicit commit would release
    the lock between checking the version and executing the migration.
    """
    if connection.in_transaction:
        raise ValueError("migrations require an idle connection")
    if type(version) is not int or version < 1:
        raise ValueError("migration version must be a positive integer")
    connection.execute("BEGIN IMMEDIATE")
    try:
        current = connection.execute("PRAGMA user_version").fetchone()[0]
        if current > version:
            raise ValueError(f"unsupported database version: {current}")
        if current < version:
            for statement in _statements(sql):
                connection.execute(statement)
            connection.execute(f"PRAGMA user_version = {version}")
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


class Database:
    """One local SQLite connection; callers must close it or use a context manager."""

    def __init__(self, path: Path) -> None:
        self.connection = sqlite3.connect(path, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self._savepoint = 0
        try:
            self.connection.execute("PRAGMA foreign_keys = ON")
            self._migrate()
        except BaseException:
            self.close()
            raise

    def _migrate(self) -> None:
        sql = files("robo_minerador.state").joinpath("migrations/001_initial.sql")
        apply_migration(self.connection, sql.read_text(encoding="utf-8"), version=1)

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Commit on success; roll back on any exception, including nested calls."""
        nested = self.connection.in_transaction
        self._savepoint += 1
        name = f"state_{self._savepoint}"
        self.connection.execute(f"SAVEPOINT {name}" if nested else "BEGIN IMMEDIATE")
        try:
            yield self.connection
            self.connection.execute(f"RELEASE SAVEPOINT {name}" if nested else "COMMIT")
        except BaseException:
            if nested:
                self.connection.execute(f"ROLLBACK TO SAVEPOINT {name}")
                self.connection.execute(f"RELEASE SAVEPOINT {name}")
            else:
                self.connection.rollback()
            raise

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
