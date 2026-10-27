"""Thread-safe SQLite wrapper with forward-only schema migrations."""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from tidy.utils.logging import get_logger

log = get_logger(__name__)

# Each entry upgrades the schema by one version. Never edit a released entry;
# append a new one instead.
MIGRATIONS: list[str] = [
    # v1 — initial schema
    """
    CREATE TABLE settings (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );

    CREATE TABLE operations (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at  TEXT NOT NULL,
        finished_at TEXT,
        root        TEXT NOT NULL,
        status      TEXT NOT NULL,
        total       INTEGER NOT NULL DEFAULT 0,
        note        TEXT
    );

    CREATE TABLE moves (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        operation_id INTEGER NOT NULL REFERENCES operations(id) ON DELETE CASCADE,
        seq          INTEGER NOT NULL,
        kind         TEXT NOT NULL,
        source       TEXT NOT NULL,
        destination  TEXT NOT NULL,
        size         INTEGER NOT NULL,
        mtime_ns     INTEGER NOT NULL,
        category     TEXT,
        status       TEXT NOT NULL,
        error        TEXT,
        temp_path    TEXT,
        updated_at   TEXT NOT NULL
    );
    CREATE INDEX idx_moves_operation ON moves(operation_id, seq);
    CREATE INDEX idx_moves_status ON moves(status);

    CREATE TABLE created_dirs (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        operation_id INTEGER NOT NULL REFERENCES operations(id) ON DELETE CASCADE,
        path         TEXT NOT NULL
    );

    CREATE TABLE hash_cache (
        path     TEXT PRIMARY KEY,
        size     INTEGER NOT NULL,
        mtime_ns INTEGER NOT NULL,
        partial  TEXT,
        full     TEXT
    );

    CREATE TABLE ai_cache (
        key        TEXT PRIMARY KEY,
        response   TEXT NOT NULL,
        created_at TEXT NOT NULL
    );

    CREATE TABLE user_rules (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        pattern    TEXT NOT NULL,
        category   TEXT NOT NULL,
        created_at TEXT NOT NULL,
        hits       INTEGER NOT NULL DEFAULT 0,
        UNIQUE(pattern)
    );
    """,
    # v2 — where an undo restored an item (may differ from source on collision)
    """
    ALTER TABLE moves ADD COLUMN undo_path TEXT
    """,
]


class Database:
    """A single SQLite connection guarded by a re-entrant lock.

    SQLite serialises writers anyway; one shared connection keeps the GUI
    thread and background workers consistent without connection juggling.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path) if str(path) != ":memory:" else path
        if isinstance(self.path, Path):
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(self.path), check_same_thread=False, isolation_level=None, timeout=30
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        if self.path != ":memory:":
            self._conn.execute("PRAGMA journal_mode = WAL")
            # FULL makes each committed journal entry durable before we touch files.
            self._conn.execute("PRAGMA synchronous = FULL")
        self._migrate()

    # ------------------------------------------------------------------ #
    def _migrate(self) -> None:
        with self._lock:
            version = self._conn.execute("PRAGMA user_version").fetchone()[0]
            for index in range(version, len(MIGRATIONS)):
                log.info("Applying database migration", extra={"ctx": {"to": index + 1}})
                self._conn.execute("BEGIN")
                try:
                    for statement in _split_sql(MIGRATIONS[index]):
                        self._conn.execute(statement)
                    self._conn.execute(f"PRAGMA user_version = {index + 1}")
                    self._conn.execute("COMMIT")
                except Exception:
                    self._conn.execute("ROLLBACK")
                    raise

    @property
    def schema_version(self) -> int:
        with self._lock:
            return int(self._conn.execute("PRAGMA user_version").fetchone()[0])

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Run statements atomically; commits on success, rolls back on error."""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")

    def execute(self, sql: str, params: tuple[Any, ...] | dict[str, Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, params)

    def query(self, sql: str, params: tuple[Any, ...] | dict[str, Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def query_one(
        self, sql: str, params: tuple[Any, ...] | dict[str, Any] = ()
    ) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    # -- settings key/value store --------------------------------------- #
    def get_setting(self, key: str, default: Any = None) -> Any:
        row = self.query_one("SELECT value FROM settings WHERE key = ?", (key,))
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except json.JSONDecodeError:
            log.warning("Corrupt setting ignored", extra={"ctx": {"key": key}})
            return default

    def set_setting(self, key: str, value: Any) -> None:
        self.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value)),
        )

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def _split_sql(script: str) -> list[str]:
    """Split a migration script into statements (no semicolons inside literals)."""
    return [s.strip() for s in script.split(";") if s.strip()]
