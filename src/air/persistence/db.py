"""Local-first persistence: SQLite + WAL, migrations, append-only event log."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA synchronous=NORMAL")

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
