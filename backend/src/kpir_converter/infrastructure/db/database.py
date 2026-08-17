"""SQLite access layer (WAL mode) and forward-only migrations."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path

__all__ = ["Database", "migrations_dir"]


def migrations_dir() -> Path:
    return Path(__file__).resolve().parents[4] / "migrations"


class Database:
    """Thin SQLite wrapper.

    One connection per thread. WAL lets the API read while the worker writes.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()

    # ------------------------------------------------------------------ connection
    @property
    def connection(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(
                self.path,
                timeout=30.0,
                isolation_level=None,  # explicit transaction control
                check_same_thread=False,
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=30000")
            self._local.conn = conn
        return conn

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Short, explicit transaction. Never wrap a whole document in one."""
        conn = self.connection
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")

    # ------------------------------------------------------------------ helpers
    def execute(self, sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
        return self.connection.execute(sql, params)

    def query_all(self, sql: str, params: tuple | dict = ()) -> list[sqlite3.Row]:
        return list(self.connection.execute(sql, params).fetchall())

    def query_one(self, sql: str, params: tuple | dict = ()) -> sqlite3.Row | None:
        return self.connection.execute(sql, params).fetchone()

    def query_scalar(self, sql: str, params: tuple | dict = ()) -> object:
        row = self.query_one(sql, params)
        return None if row is None else row[0]

    # ------------------------------------------------------------------ migrations
    def migrate(self, directory: Path | None = None) -> list[str]:
        directory = directory or migrations_dir()
        conn = self.connection
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " name TEXT PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT (datetime('now')))"
        )
        applied = {row["name"] for row in conn.execute("SELECT name FROM schema_migrations")}
        newly: list[str] = []
        for file in sorted(directory.glob("*.sql")):
            if file.name in applied:
                continue
            sql = file.read_text(encoding="utf-8")
            # ``executescript`` implicitly commits any open transaction, so the
            # script manages its own atomicity and the bookkeeping row is written
            # immediately afterwards.
            try:
                conn.executescript(f"BEGIN;\n{sql}\nCOMMIT;")
            except BaseException:
                with suppress(sqlite3.OperationalError):
                    conn.execute("ROLLBACK")
                raise
            conn.execute("INSERT INTO schema_migrations(name) VALUES (?)", (file.name,))
            newly.append(file.name)
        return newly
