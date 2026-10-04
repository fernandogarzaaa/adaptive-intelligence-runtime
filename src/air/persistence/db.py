"""Local-first persistence: SQLite + WAL, migrations, append-only event log."""

from __future__ import annotations

import functools
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path


def find_migrations_dir() -> Path:
    """Locate the SQL migrations directory.

    Migrations ship with the package (``air/persistence/migrations``).
    As a fallback for a source checkout, walk up from this file to a
    ``migrations`` directory. Never silently return an empty directory:
    every caller depends on these tables existing.
    """
    packaged = Path(__file__).resolve().parent / "migrations"
    if packaged.is_dir() and any(packaged.glob("*.sql")):
        return packaged
    for parent in Path(__file__).resolve().parents:
        cand = parent / "migrations"
        if cand.is_dir() and any(cand.glob("*.sql")):
            return cand
    raise RuntimeError("AIR migrations directory not found")


class _BufferedCursor:
    """A fully-materialized result set.

    Rows are consumed from the real cursor while the connection lock is
    held, so iteration/fetching after the lock is released can never see
    a statement reset by another thread's execute on the same connection.
    """

    def __init__(self, rows: list, lastrowid=None, rowcount: int = -1) -> None:
        self._rows = list(rows)
        self._pos = 0
        self.lastrowid = lastrowid
        self.rowcount = rowcount

    def fetchone(self):
        if self._pos >= len(self._rows):
            return None
        row = self._rows[self._pos]
        self._pos += 1
        return row

    def fetchall(self):
        rows = self._rows[self._pos:]
        self._pos = len(self._rows)
        return rows

    def fetchmany(self, size: int | None = None):
        n = size if size is not None else 1
        rows = self._rows[self._pos:self._pos + n]
        self._pos += len(rows)
        return rows

    def __iter__(self):
        remaining = self._rows[self._pos:]
        self._pos = len(self._rows)
        return iter(remaining)

    def close(self) -> None:
        self._pos = len(self._rows)


class _ThreadSafeConnection:
    """Serialize all access to one sqlite3 connection across threads.

    The API serves sync handlers from a worker threadpool while async
    handlers and background tasks run on the main loop. Every statement
    runs under a reentrant lock AND its rows are fully buffered before
    the lock is released: pysqlite invalidates an in-flight cursor when
    another statement executes on the same connection, so a cursor must
    never outlive the execute call's critical section.

    Multi-statement atomic sections (e.g. the event ledger append) take
    the lock for the whole section via ``with db.conn:``.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._lock = threading.RLock()

    def execute(self, sql: str, params=()):
        with self._lock:
            cur = self._conn.execute(sql, params)
            rows = cur.fetchall()
            return _BufferedCursor(rows, cur.lastrowid, cur.rowcount)

    def executemany(self, sql: str, seq):
        with self._lock:
            cur = self._conn.executemany(sql, seq)
            return _BufferedCursor([], cur.lastrowid, cur.rowcount)

    def executescript(self, sql: str):
        with self._lock:
            return self._conn.executescript(sql)

    def __getattr__(self, name: str):
        attr = getattr(self._conn, name)
        if not callable(attr):
            return attr

        @functools.wraps(attr)
        def _guarded(*args, **kwargs):
            with self._lock:
                return attr(*args, **kwargs)

        return _guarded

    def __enter__(self):  # noqa: PYI034 - explicit: special-method lookup
        self._lock.acquire()  # bypasses __getattr__, so define directly
        return self._conn.__enter__()

    def __exit__(self, *exc):
        try:
            return self._conn.__exit__(*exc)
        finally:
            self._lock.release()

    def commit(self) -> None:
        """Commit the current transaction. Explicit (not via __getattr__)
        so crash-consistency tests can patch this seam to simulate a
        process crash before the commit point."""
        with self._lock:
            self._conn.commit()

    def rollback(self) -> None:
        """Roll back the current transaction: what a process crash does to
        uncommitted work."""
        with self._lock:
            self._conn.rollback()


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        raw = sqlite3.connect(str(path), check_same_thread=False)
        raw.execute("PRAGMA journal_mode=WAL")
        raw.execute("PRAGMA foreign_keys=ON")
        raw.execute("PRAGMA synchronous=NORMAL")
        self.conn = _ThreadSafeConnection(raw)

    def applied_migrations(self) -> set[str]:
        try:
            rows = self.conn.execute("SELECT version FROM schema_migrations").fetchall()
            return {r[0] for r in rows}
        except sqlite3.OperationalError:
            return set()

    def migrate(self, migrations_dir: Path) -> list[str]:
        applied: list[str] = []
        files = sorted(migrations_dir.glob("*.sql"))
        for f in files:
            version = f.stem
            if version in self.applied_migrations():
                continue
            sql = f.read_text(encoding="utf-8")
            with self.conn:
                self.conn.executescript(sql)
                self.conn.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                    (version, datetime.now(timezone.utc).isoformat()),
                )
            applied.append(version)
        return applied

    def close(self) -> None:
        self.conn.close()
