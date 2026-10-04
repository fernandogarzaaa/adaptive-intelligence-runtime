"""Internal event fabric.

Every meaningful state transition in the runtime is an event. Events are:
  1. broadcast on the in-process EventBus for live subscribers (UI, websocket),
  2. appended to the EventStore (SQLite), hash-chained for tamper detection.

Hash chain: entry_n.hash = sha256(canonical(event_n) || entry_{n-1}.hash).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from collections import defaultdict
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

from pydantic import BaseModel, Field

SCHEMA_VERSION = 1


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical(obj: object) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def event_hash(payload_canonical: str, prev_hash: str) -> str:
    return hashlib.sha256((payload_canonical + prev_hash).encode("utf-8")).hexdigest()


class Event(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: str = Field(default_factory=utcnow)
    run_id: str | None = None
    agent_id: str | None = None
    type: str
    payload: dict = Field(default_factory=dict)
    causation_id: str | None = None
    correlation_id: str | None = None
    schema_version: int = SCHEMA_VERSION


class EventBus:
    """In-process async pub/sub. Subscribers get every event they subscribed to."""

    def __init__(self) -> None:
        self._subs: dict[str, list[Callable[[Event], Awaitable[None]]]] = defaultdict(list)
        self._wildcards: list[Callable[[Event], Awaitable[None]]] = []

    def subscribe(self, event_type: str, fn: Callable[[Event], Awaitable[None]]) -> None:
        if event_type == "*":
            self._wildcards.append(fn)
        else:
            self._subs[event_type].append(fn)

    async def publish(self, event: Event) -> None:
        for fn in list(self._subs.get(event.type, [])) + list(self._wildcards):
            await fn(event)


class EventStore:
    """Append-only, hash-chained event log backed by SQLite."""

    GENESIS_HASH = "0" * 64

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        # Threading lock, not asyncio: appends arrive from the main loop
        # (async handlers, background agent tasks) and from worker threads
        # (sync API handlers). The read-compute-insert sequence must be
        # atomic across all of them or the hash chain interleaves.
        self._lock = threading.Lock()

    def _last_hash(self) -> str:
        row = self._conn.execute("SELECT hash FROM events ORDER BY rowid DESC LIMIT 1").fetchone()
        return row[0] if row else self.GENESIS_HASH

    async def append(self, event: Event) -> Event:
        with self._lock:
            prev = self._last_hash()
            body = {
                "event_id": event.event_id,
                "timestamp": event.timestamp,
                "run_id": event.run_id,
                "agent_id": event.agent_id,
                "type": event.type,
                "payload": event.payload,
                "causation_id": event.causation_id,
                "correlation_id": event.correlation_id,
                "schema_version": event.schema_version,
            }
            h = event_hash(canonical(body), prev)
            self._conn.execute(
                """INSERT INTO events
                   (event_id, timestamp, run_id, agent_id, type, payload,
                    causation_id, correlation_id, schema_version, prev_hash, hash)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event.event_id, event.timestamp, event.run_id, event.agent_id,
                    event.type, json.dumps(event.payload), event.causation_id,
                    event.correlation_id, event.schema_version, prev, h,
                ),
            )
            self._conn.commit()
            return event

    def verify_chain(self) -> tuple[bool, str | None]:
        """Returns (ok, first_bad_event_id). Recomputes every link."""
        rows = self._conn.execute(
            "SELECT event_id, timestamp, run_id, agent_id, type, payload, causation_id,"
            " correlation_id, schema_version, prev_hash, hash FROM events ORDER BY rowid"
        ).fetchall()
        prev = self.GENESIS_HASH
        for r in rows:
            (eid, ts, run_id, agent_id, typ, payload, caus, corr, sv, prev_hash, h) = r
            if prev_hash != prev:
                return False, eid
            body = {
                "event_id": eid, "timestamp": ts, "run_id": run_id, "agent_id": agent_id,
                "type": typ, "payload": json.loads(payload), "causation_id": caus,
                "correlation_id": corr, "schema_version": sv,
            }
            if event_hash(canonical(body), prev) != h:
                return False, eid
            prev = h
        return True, None

    def list(self, run_id: str | None = None, event_type: str | None = None,
             limit: int = 1000) -> list[dict]:
        q = ("SELECT event_id, timestamp, run_id, agent_id, type, payload, causation_id,"
             " correlation_id, schema_version FROM events")
        clauses, params = [], []
        if run_id:
            clauses.append("run_id = ?")
            params.append(run_id)
        if event_type:
            clauses.append("type = ?")
            params.append(event_type)
        if clauses:
            q += " WHERE " + " AND ".join(clauses)
        q += " ORDER BY rowid LIMIT ?"
        params.append(limit)
        rows = self._conn.execute(q, params).fetchall()
        out = []
        for r in rows:
            out.append({
                "event_id": r[0], "timestamp": r[1], "run_id": r[2], "agent_id": r[3],
                "type": r[4], "payload": json.loads(r[5]), "causation_id": r[6],
                "correlation_id": r[7], "schema_version": r[8],
            })
        return out
