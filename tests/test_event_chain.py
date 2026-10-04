"""Event fabric: append-only hash chain, tamper detection."""

import asyncio
import json
from pathlib import Path

from air.events.fabric import Event, EventBus, EventStore
from air.persistence.db import Database


def _migrations() -> Path:
    return Path(__file__).resolve().parents[1] / "migrations"


def _store(tmp_path):
    db = Database(tmp_path / "t.db")
    db.migrate(_migrations())
    db.conn.execute(
        "INSERT INTO runs (id, goal, status, runtime_version, created_at)"
        " VALUES ('r1', 'test', 'CREATED', '0.1.0', '2026-01-01T00:00:00+00:00')"
    )
    db.conn.commit()
    return EventStore(db.conn), db


def test_append_and_verify_chain(tmp_path):
    store, _ = _store(tmp_path)

    async def main():
        for i in range(5):
            await store.append(Event(type=f"test.{i}", run_id="r1",
                                     payload={"n": i}))

    asyncio.run(main())
    ok, bad = store.verify_chain()
    assert ok and bad is None


def test_tamper_is_detected(tmp_path):
    store, db = _store(tmp_path)

    async def main():
        return await store.append(Event(type="run.created", run_id="r1",
                                        payload={"goal": "x"}))

    ev = asyncio.run(main())
    db.conn.execute("UPDATE events SET payload=? WHERE event_id=?",
                    (json.dumps({"goal": "HACKED"}), ev.event_id))
    db.conn.commit()
    ok, bad = store.verify_chain()
    assert not ok
    assert bad == ev.event_id


def test_bus_delivers_to_subscribers():
    bus = EventBus()
    seen = []

    async def handler(e):
        seen.append(e.type)

    bus.subscribe("run.created", handler)

    async def main():
        await bus.publish(Event(type="run.created"))
        await bus.publish(Event(type="other"))

    asyncio.run(main())
    assert seen == ["run.created"]
