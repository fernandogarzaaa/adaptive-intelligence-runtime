"""Event fabric semantic audit.

The hash chain proves integrity, not semantic correctness. These tests
audit the semantics the chain does NOT cover:

- ordering guarantees (what `sequence` means; per-ledger vs per-run)
- causation_id / correlation_id validity
- duplicate event handling
- schema_version handling on the read/replay path
- concurrent append under contention (store level and emit path level)
- reducer determinism: same log -> same state, twice independently
- fuzz: duplicated / reordered / missing events, unknown schema
  versions, malformed payloads, repeated replay
"""

from __future__ import annotations

import asyncio
import sqlite3
import threading

import pytest

import air.api.app as app_module
from air.agents.runtime import AgentRuntime
from air.config import AirConfig
from air.events.fabric import Event, EventStore
from air.persistence.db import Database, find_migrations_dir
from air.world.state import WorldStateStore, reduce_events


def _env(tmp_path):
    config = AirConfig(data_dir=tmp_path)
    db = Database(tmp_path / "air.db")
    db.migrate(find_migrations_dir())
    rt = AgentRuntime(config, db)
    return rt, db


def _seed_run_row(db, run_id: str) -> None:
    # events.run_id has a FK to runs(id); raw store-level tests must
    # seed the parent row, mirroring what create_run does.
    db.conn.execute(
        "INSERT OR IGNORE INTO runs (id, goal, runtime_version, created_at)"
        " VALUES (?, ?, '0.1.0', '2026-01-01T00:00:00+00:00')",
        (run_id, "semantic audit"))
    db.conn.commit()


def _append(rt, db, n: int, run_id: str = "r1",
            typ: str = "test.evt") -> list[str]:
    _seed_run_row(db, run_id)

    async def main():
        ids = []
        for i in range(n):
            ev = await rt.store.append(Event(type=typ, run_id=run_id,
                                             payload={"n": i}))
            ids.append(ev.event_id)
        return ids
    return asyncio.run(main())


# ------------------------------------------------------------ ordering

def test_sequence_is_monotonic_per_ledger(tmp_path):
    """`sequence` (rowid) is monotonic per ledger, not per run. Per-run
    order is rowid order filtered by run_id."""
    rt, db = _env(tmp_path)
    _append(rt, db, 3, run_id="r1")
    _append(rt, db, 2, run_id="r2")
    _append(rt, db, 2, run_id="r1")
    rows = db.conn.execute(
        "SELECT rowid, run_id FROM events ORDER BY rowid").fetchall()
    rowids = [r[0] for r in rows]
    assert rowids == sorted(rowids), "ledger order must be monotonic"
    assert len(set(rowids)) == len(rowids), "rowids must be unique"
    r1_rows = [r[0] for r in rows if r[1] == "r1"]
    assert r1_rows == sorted(r1_rows), "per-run order is ledger order filtered"
    ok, bad = rt.store.verify_chain()
    assert ok and bad is None


# ------------------------------------------------------------ duplicates

def test_duplicate_event_id_is_rejected_loudly(tmp_path):
    """Appending the same event_id twice must fail loudly (IntegrityError),
    leave the chain valid, and not corrupt subsequent appends."""
    rt, db = _env(tmp_path)
    _seed_run_row(db, "r1")
    fixed = "evt_" + "a" * 12

    async def main():
        await rt.store.append(Event(event_id=fixed, type="test.one",
                                    run_id="r1"))
        with pytest.raises(sqlite3.IntegrityError):
            await rt.store.append(Event(event_id=fixed, type="test.two",
                                        run_id="r1"))
        # The store must still accept new events after the rejected dup.
        await rt.store.append(Event(type="test.three", run_id="r1"))

    asyncio.run(main())
    ok, bad = rt.store.verify_chain()
    assert ok and bad is None, f"chain broken at {bad}"
    types = [e["type"] for e in rt.store.list(run_id="r1")]
    assert types == ["test.one", "test.three"], types


# ------------------------------------------------------------ concurrency

def test_concurrent_appends_keep_chain_linear(tmp_path):
    """8 threads x 25 appends through EventStore.append: the lock must
    prevent hash-chain interleaving; the result is one linear chain."""
    rt, db = _env(tmp_path)
    _seed_run_row(db, "r1")
    errors: list[BaseException] = []

    def worker(k: int):
        try:
            async def main():
                for i in range(25):
                    await rt.store.append(
                        Event(type="test.concurrent", run_id="r1",
                              payload={"thread": k, "i": i}))
            asyncio.run(main())
        except BaseException as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(k,)) for k in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors[:3]
    n = db.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    assert n == 200, n
    ok, bad = rt.store.verify_chain()
    assert ok and bad is None, f"chain interleaved at {bad}"


def test_concurrent_emit_path_keeps_chain_linear(tmp_path):
    """Same contention, but through AgentRuntime.emit (the real emit path:
    store.append + bus publish). Each thread runs its own event loop."""
    rt, db = _env(tmp_path)
    _seed_run_row(db, "r1")
    errors: list[BaseException] = []

    def worker(k: int):
        try:
            async def main():
                for i in range(25):
                    await rt.emit("test.emit.concurrent", run_id="r1",
                                  payload={"thread": k, "i": i})
            asyncio.run(main())
        except BaseException as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(k,)) for k in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors[:3]
    n = db.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    assert n == 200, n
    ok, bad = rt.store.verify_chain()
    assert ok and bad is None, f"chain interleaved at {bad}"


# ------------------------------------------------------------ reducer determinism

def _realistic_log(rt) -> list[dict]:
    """Drive a realistic run through the real runtime and return its
    event log as plain dicts (the reducer's input shape)."""

    async def main():
        run_id = await rt.create_run("audit the event fabric")
        await rt.emit("run.started", run_id=run_id)
        planner = await rt.create_agent(run_id, "planner", "decompose work")
        worker = await rt.create_agent(run_id, "worker", "do work",
                                       parent_id=planner.id,
                                       granted=["READ"])
        await rt.emit("agent.started", run_id=run_id, agent_id=planner.id)
        await rt.emit("agent.started", run_id=run_id, agent_id=worker.id)
        await rt.send_message(run_id, planner.id, worker.id, "parent_child",
                              "task", {"objective": "probe"})
        # A real tool call through the execution gateway.
        from air.tools import ToolCallState
        rec = await rt.call_tool(worker.id, "fs.read", {"path": "nope.txt"})
        assert rec.state in (ToolCallState.COMMITTED, ToolCallState.FAILED,
                             ToolCallState.DENIED)
        await rt.emit("spawn.requested", run_id=run_id,
                      agent_id=planner.id,
                      payload={"role": "researcher", "reason": "need context"})
        await rt.emit("spawn.denied", run_id=run_id, agent_id=planner.id,
                      payload={"role": "researcher", "reason": "budget"})
        await rt.emit("agent.completed", run_id=run_id, agent_id=worker.id)
        await rt.emit("run.completed", run_id=run_id,
                      payload={"summary": "done"})
        return run_id

    run_id = asyncio.run(main())
    return rt.store.list(run_id=run_id, limit=100_000)


def test_reducer_is_deterministic(tmp_path):
    """Same event log -> same state, twice, independently. Also agrees
    with the WorldStateStore build path over the same rows."""
    rt, db = _env(tmp_path)
    events = _realistic_log(rt)
    assert len(events) > 10, "log should be non-trivial"
    a = reduce_events(events)
    b = reduce_events([dict(e) for e in events])  # independent copy
    assert a == b, "reducer must be a pure function of the log"
    run_id = events[0]["run_id"]
    c = WorldStateStore(db.conn, rt.store).build(run_id)
    assert a == c, "store build path must agree with direct reduce"


def test_repeated_replay_is_idempotent(tmp_path):
    rt, _ = _env(tmp_path)
    events = _realistic_log(rt)
    states = [reduce_events(events) for _ in range(3)]
    assert states[0] == states[1] == states[2]


# ------------------------------------------------------------ schema versions

def test_reducer_rejects_unknown_schema_version(tmp_path):
    """An event with an unknown schema_version must fail loudly on the
    replay path, never be silently interpreted with v1 semantics."""
    rt, _ = _env(tmp_path)
    events = _realistic_log(rt)
    events[3]["schema_version"] = 999
    with pytest.raises(ValueError, match="schema_version"):
        reduce_events(events)


def test_unknown_schema_version_survives_transport(tmp_path):
    """The store and list path are transport: they must preserve an
    unknown schema_version verbatim so the rejection happens at the
    semantic layer, not by silent coercion."""
    rt, db = _env(tmp_path)
    _seed_run_row(db, "r1")

    async def main():
        await rt.store.append(Event(type="test.future", run_id="r1",
                                    payload={}, schema_version=999))

    asyncio.run(main())
    rows = rt.store.list(run_id="r1")
    assert rows[0]["schema_version"] == 999
    ok, _ = rt.store.verify_chain()
    assert ok, "integrity is independent of schema version"


# ------------------------------------------------------------ fuzz

def test_fuzz_duplicated_events(tmp_path):
    """Duplicated events are absorbed by the reducer but double-count
    counters: the reducer has no dedup. Documented, not hidden."""
    rt, _ = _env(tmp_path)
    events = _realistic_log(rt)
    before = reduce_events(events)
    doubled = events + [dict(e) for e in events]
    after = reduce_events(doubled)
    # No crash, no exception: absorbed.
    assert after["spawns"]["requested"] == 2 * before["spawns"]["requested"]
    assert after["messages"] == 2 * before["messages"]
    # ...but the doubled state contradicts the ledger truth (one request).
    assert after != before


def test_fuzz_illegal_reorder_changes_state_silently(tmp_path):
    """The reducer trusts input order blindly: replaying an illegally
    reordered log yields a different state with no complaint. Replay
    inputs must therefore arrive in ledger (rowid) order."""
    rt, _ = _env(tmp_path)
    events = _realistic_log(rt)
    ordered = reduce_events(events)
    assert ordered["run"]["status"] == "COMPLETED"
    # Move run.completed before run.started: illegal reordering.
    reordered = [e for e in events if e["type"] != "run.completed"]
    completed = next(e for e in events if e["type"] == "run.completed")
    created_idx = next(i for i, e in enumerate(reordered)
                       if e["type"] == "run.created")
    reordered.insert(created_idx + 1, completed)
    replayed = reduce_events(reordered)
    assert replayed["run"]["status"] == "RUNNING", (
        "last-write-wins on status: reordered replay diverges silently")
    assert replayed != ordered


def test_fuzz_missing_events(tmp_path):
    """A log missing run.created still reduces without crashing, from
    the default-initialized state. Gaps are absorbed, not validated."""
    rt, _ = _env(tmp_path)
    events = _realistic_log(rt)
    gapped = [e for e in events if e["type"] != "run.created"]
    state = reduce_events(gapped)
    assert state["run"]["status"] == "COMPLETED"  # from run.completed alone
    assert state["run"].get("goal") is None, "goal came from the missing event"


def test_fuzz_malformed_payload_rejected_loudly(tmp_path):
    """A non-dict payload must raise, not produce a half-built state."""
    rt, _ = _env(tmp_path)
    events = _realistic_log(rt)
    events[0]["payload"] = "not-a-dict"
    with pytest.raises(AttributeError):
        reduce_events(events)


def test_fuzz_missing_type_rejected_loudly(tmp_path):
    rt, _ = _env(tmp_path)
    events = _realistic_log(rt)
    del events[2]["type"]
    with pytest.raises(KeyError):
        reduce_events(events)


# ------------------------------------------------------------ causation audit

def test_causation_ids_either_resolve_or_are_declared_roots(tmp_path):
    """Every event is either a declared root (causation_id None) or its
    causation_id resolves to an existing event in the ledger. Same for
    correlation_id."""
    rt, _ = _env(tmp_path)
    events = _realistic_log(rt)
    by_id = {e["event_id"] for e in events}
    assert by_id, "need a non-empty log"
    for e in events:
        caus = e["causation_id"]
        assert caus is None or caus in by_id, (
            f"{e['event_id']} ({e['type']}): dangling causation_id {caus}")
        corr = e["correlation_id"]
        assert corr is None or corr in by_id, (
            f"{e['event_id']} ({e['type']}): dangling correlation_id {corr}")
    non_null = sum(1 for e in events if e["causation_id"] is not None)
    # AUDIT NOTE (2026-10-04): the causal graph is currently flat. No
    # emitter in src/air sets causation_id or correlation_id, so every
    # event is a declared root and non_null == 0. Populating the causal
    # model is design work; this test pins the structural rule so a
    # future emitter cannot introduce dangling references.
    assert non_null == 0, (
        f"causal links appeared ({non_null}); update this audit expectation "
        "deliberately, keeping the resolve-or-root rule")


def test_no_emitter_fabricates_causation(tmp_path):
    """Defense of the audit above at the source level: no emit call site
    may pass a causation_id that is not an event_id it legitimately
    observed. Currently none pass any, which the audit test pins."""
    import pathlib
    import re
    src = pathlib.Path("src/air")
    hits = []
    for p in src.rglob("*.py"):
        for i, line in enumerate(p.read_text().splitlines(), 1):
            m = re.search(r"causation_id\s*=\s*([^,\)]+)", line)
            if m and m.group(1).strip() not in ("None", "causation_id"):
                # A concrete value that is not the emit() pass-through:
                # must be an event_id the emitter legitimately observed.
                hits.append(f"{p}:{i}: {line.strip()}")
    real = [h for h in hits if "test" not in h]
    assert not real, f"emitters setting causation_id: {real}"
