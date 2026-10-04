"""Causation / correlation semantics (workstream G).

The structural rule lives in tests/test_event_semantics.py
(resolve-or-root, no fabricated causation). These tests prove the
positive behavior: real causal chains threaded through a full run,
honest null roots with documented reasons, dangling-ID rejection,
and the correlation rule.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from air.agents.runtime import AgentRuntime
from air.capabilities.pipeline import CapabilityPipeline, CapabilityEffect
from air.config import AirConfig
from air.events.fabric import Event, EventStore
from air.learning.policies import PolicyStore
from air.persistence.db import Database, find_migrations_dir


def _env(tmp_path):
    config = AirConfig(data_dir=tmp_path)
    db = Database(tmp_path / "air.db")
    db.migrate(find_migrations_dir())
    rt = AgentRuntime(config, db)
    return rt, db


def _seed_run_row(db, run_id: str) -> None:
    db.conn.execute(
        "INSERT OR IGNORE INTO runs (id, goal, runtime_version, created_at)"
        " VALUES (?, ?, '0.1.0', '2026-01-01T00:00:00+00:00')",
        (run_id, "causation audit"))
    db.conn.commit()


def _events_by_type(db, run_id=None):
    sql = ("SELECT event_id, type, causation_id, correlation_id, payload"
           " FROM events")
    params: list = []
    if run_id is not None:
        sql += " WHERE run_id=?"
        params.append(run_id)
    sql += " ORDER BY rowid"
    out = {}
    for eid, typ, caus, corr, payload in db.conn.execute(sql, params):
        out.setdefault(typ, []).append(
            {"event_id": eid, "causation_id": caus,
             "correlation_id": corr, "payload": json.loads(payload)})
    return out


def _one(events, typ):
    assert typ in events, f"missing event type {typ}: {sorted(events)}"
    assert len(events[typ]) == 1, f"expected one {typ}, got {len(events[typ])}"
    return events[typ][0]


async def _completed_tool_run(tmp_path):
    """A real run: one specialist that calls fs.read, then completes."""
    rt, db = _env(tmp_path)
    (tmp_path / "probe.txt").write_text("causation probe")

    async def tool_user(agent, runtime):
        rec = await runtime.call_tool(agent.id, "fs.read",
                                      {"path": "probe.txt"})
        assert str(rec.state) == "COMMITTED", rec.state
        return {"ok": True, "tokens": 10, "cost_usd": 0.0}

    from air.allocation.allocator import Strategy
    rt.register_behavior("specialist", tool_user)
    run_id = await rt.create_run("causation chain target",
                                 strategy=Strategy.SINGLE_AGENT)
    await rt.start_run(run_id)
    for _ in range(400):
        row = db.conn.execute(
            "SELECT status FROM runs WHERE id=?", (run_id,)).fetchone()
        if row[0] == "COMPLETED":
            break
        await asyncio.sleep(0.05)
    assert row[0] == "COMPLETED", f"run did not complete: {row}"
    return rt, db, run_id


# ------------------------------------------------------- the full-run chain

def test_full_run_causation_chain(tmp_path):
    """created -> started -> agent.created -> agent.started ->
    tool.requested -> tool.validated -> tool.authorized -> tool.dispatched
    -> tool.completed -> agent.completed -> run.completed ->
    experience.created, each naming its real predecessor."""
    rt, db, run_id = asyncio.run(_completed_tool_run(tmp_path))
    events = _events_by_type(db, run_id)

    created = _one(events, "run.created")
    started = _one(events, "run.started")
    agent_created = _one(events, "agent.created")
    agent_started = _one(events, "agent.started")
    requested = _one(events, "tool.requested")
    validated = _one(events, "tool.validated")
    authorized = _one(events, "tool.authorized")
    dispatched = _one(events, "tool.dispatched")
    completed = _one(events, "tool.completed")
    agent_done = _one(events, "agent.completed")
    run_done = _one(events, "run.completed")
    experience = _one(events, "experience.created")

    assert created["causation_id"] is None  # declared root
    assert started["causation_id"] == created["event_id"]
    # Initial population is created directly (no spawn.requested):
    # the nearest honest ancestor is run.started.
    assert agent_created["causation_id"] == started["event_id"]
    assert agent_started["causation_id"] == agent_created["event_id"]
    assert requested["causation_id"] == agent_started["event_id"]
    assert validated["causation_id"] == requested["event_id"]
    assert authorized["causation_id"] == validated["event_id"]
    assert dispatched["causation_id"] == authorized["event_id"]
    assert completed["causation_id"] == dispatched["event_id"]
    assert agent_done["causation_id"] == agent_started["event_id"]
    assert run_done["causation_id"] == agent_done["event_id"]
    assert experience["causation_id"] == run_done["event_id"]

    # Every non-root causation_id in the run resolves to a ledger event.
    by_id = {r[0] for r in db.conn.execute("SELECT event_id FROM events")}
    for typ, evs in events.items():
        for e in evs:
            assert e["causation_id"] is None or e["causation_id"] in by_id, (
                f"{typ}: dangling {e['causation_id']}")
            # Correlation rule: run-scoped events belong to the run.
            assert e["correlation_id"] == run_id, (
                f"{typ}: correlation {e['correlation_id']!r} != run {run_id}")


def test_evaluation_assurance_chain(tmp_path):
    """evaluation.completed is caused by experience.created;
    assurance.completed is caused by evaluation.completed."""
    from air.api.services import AssuranceService, EvalService
    rt, db, run_id = asyncio.run(_completed_tool_run(tmp_path))

    async def main():
        ev = await EvalService(db.conn, lambda: rt).run(run_id)
        ar = await AssuranceService(db.conn, lambda: rt).run(
            ev["evaluation_id"])
        return ev, ar

    ev, ar = asyncio.run(main())
    events = _events_by_type(db, run_id)
    evaluation = _one(events, "evaluation.completed")
    experience = _one(events, "experience.created")
    assert evaluation["causation_id"] == experience["event_id"]
    assert evaluation["correlation_id"] == run_id

    assured = db.conn.execute(
        "SELECT event_id, causation_id, correlation_id, run_id FROM events"
        " WHERE type='assurance.completed'"
        " AND json_extract(payload, '$.assurance_id')=?",
        (ar["assurance_id"],)).fetchone()
    assert assured is not None, "assurance.completed was not emitted"
    _, caus, corr, arid = assured
    assert caus == evaluation["event_id"], (
        "assurance must chain to the evaluation it derives from")
    assert arid == run_id and corr == run_id


# ------------------------------------------------------------ honest roots

def test_honest_roots(tmp_path):
    """Emitters whose cause is not an event declare causation_id None,
    with the reason documented at the emit site."""
    async def main():
        rt, db = _env(tmp_path)
        from air.allocation.allocator import Strategy

        async def idle(agent, runtime):
            return {"ok": True, "tokens": 1, "cost_usd": 0.0}

        rt.register_behavior("specialist", idle)
        run_id = await rt.create_run("root audit",
                                     strategy=Strategy.SINGLE_AGENT)
        await rt.start_run(run_id)
        agents = db.conn.execute(
            "SELECT id FROM agents WHERE root_run_id=?", (run_id,)).fetchall()
        parent_id = agents[0][0]
        # A mid-run spawn: the spawning DECISION is not an event.
        child, decision = await rt.spawn_agent(
            parent_id, "gather context", "researcher",
            capabilities=["READ"])
        assert child is not None, f"spawn denied: {decision}"
        # Operator actions: pause, terminate. Not events, no parents.
        await rt.pause_run(run_id)
        await rt.terminate_agent(child.id, subtree=False,
                                 reason="root audit")
        return rt, db, run_id

    rt, db, run_id = asyncio.run(main())
    events = _events_by_type(db, run_id)

    assert _one(events, "run.created")["causation_id"] is None
    requested = [e for e in events.get("spawn.requested", [])]
    assert requested, "expected a spawn.requested event"
    assert all(e["causation_id"] is None for e in requested), (
        "spawn.requested must be a declared root")
    paused = _one(events, "run.paused")
    assert paused["causation_id"] is None
    terminated = [e for e in events.get("agent.terminated", [])]
    assert terminated and all(e["causation_id"] is None for e in terminated)


def test_policy_and_capability_roots(tmp_path):
    """policy.proposed, policy.rollback_requested and
    capability.proposed are declared roots: they derive from offline
    analysis / operator intent, not from a ledger event."""
    async def main():
        rt, db = _env(tmp_path)

        async def emit(type, payload=None, causation_id=None):
            await rt.emit(type, payload=payload or {},
                          causation_id=causation_id,
                          correlation_id=(payload or {}).get("policy"))

        store = PolicyStore(db.conn, emit=emit, store=rt.store)
        store.ensure("root-policy", {"spawn_threshold": 0.05})
        await store.propose("root-policy", {"spawn_threshold": 0.2},
                            reason="offline analysis")
        await store.promote(
            "root-policy", "2",
            evaluation={"verdict": "SUPPORTED", "id": "e1"},
            assurance={"evaluator_verdict": "SOUND",
                       "system_verdict": "SUPPORTED", "id": "a1"})
        run_id = await rt.create_run("t", agent_budget=1)
        await store.request_rollback(
            "root-policy", reason="operator intent",
            evidence={}, requested_by="operator")

        async def cap_emit(type, capability_id=None, payload=None,
                           causation_id=None):
            await rt.emit(type,
                          payload={"capability_id": capability_id,
                                   **(payload or {})},
                          causation_id=causation_id,
                          correlation_id=capability_id)

        pipeline = CapabilityPipeline(db.conn, emit=cap_emit)
        await pipeline.propose("root-cap", "a root capability")
        return db

    db = asyncio.run(main())
    rows = {r[0]: (r[1], r[2]) for r in db.conn.execute(
        "SELECT type, causation_id, correlation_id FROM events"
        " WHERE type IN ('policy.proposed', 'policy.rollback_requested',"
        " 'capability.proposed')")}
    assert rows["policy.proposed"][0] is None
    assert rows["policy.rollback_requested"][0] is None
    assert rows["capability.proposed"][0] is None
    # Correlation rule for non-run workflows: the workflow identifier.
    assert rows["policy.proposed"][1] == "root-policy"
    assert rows["policy.rollback_requested"][1] == "root-policy"
    assert rows["capability.proposed"][1] is not None


# ------------------------------------------------- dangling-ID rejection

def test_dangling_causation_id_refused(tmp_path):
    """An event whose causation_id points nowhere is refused, never
    silently chained."""
    rt, db = _env(tmp_path)
    _seed_run_row(db, "r1")
    with pytest.raises(ValueError, match="dangling causation_id"):
        rt.store.insert(Event(type="test.evt", run_id="r1",
                              causation_id="does-not-exist"))
    with pytest.raises(ValueError, match="dangling causation_id"):
        asyncio.run(rt.emit("test.evt", run_id="r1",
                            causation_id="does-not-exist"))
    # A real parent links fine.
    parent = rt.store.insert(Event(type="test.parent", run_id="r1"))
    child = rt.store.insert(Event(type="test.child", run_id="r1",
                                  causation_id=parent.event_id))
    assert child.causation_id == parent.event_id


# ------------------------------------------------------- the policy chain

def test_policy_reject_chain(tmp_path):
    """policy.rejected is caused by the policy.proposed it answers."""
    async def main():
        rt, db = _env(tmp_path)

        async def emit(type, payload=None, causation_id=None):
            await rt.emit(type, payload=payload or {},
                          causation_id=causation_id,
                          correlation_id=(payload or {}).get("policy"))

        store = PolicyStore(db.conn, emit=emit, store=rt.store)
        store.ensure("chain-policy", {"spawn_threshold": 0.05})
        ver = await store.propose("chain-policy",
                                  {"spawn_threshold": 0.3}, reason="test")
        await store.reject("chain-policy", ver.version, reason="too hot")
        return db

    db = asyncio.run(main())
    proposed = db.conn.execute(
        "SELECT event_id FROM events WHERE type='policy.proposed'").fetchone()
    rejected = db.conn.execute(
        "SELECT event_id, causation_id, correlation_id FROM events"
        " WHERE type='policy.rejected'").fetchone()
    assert rejected[1] == proposed[0]
    assert rejected[2] == "chain-policy"


def test_rollback_chain(tmp_path):
    """policy.rollback_approved is caused by policy.rollback_requested;
    policy.activated is caused by policy.rollback_approved (same batch)."""
    async def main():
        rt, db = _env(tmp_path)

        async def emit(type, payload=None, causation_id=None):
            await rt.emit(type, payload=payload or {},
                          causation_id=causation_id,
                          correlation_id=(payload or {}).get("policy"))

        store = PolicyStore(db.conn, emit=emit, store=rt.store)
        store.ensure("rb-policy", {"spawn_threshold": 0.05})
        await store.propose("rb-policy", {"spawn_threshold": 0.2},
                            reason="test")
        await store.promote(
            "rb-policy", "2",
            evaluation={"verdict": "SUPPORTED", "id": "e1"},
            assurance={"evaluator_verdict": "SOUND",
                       "system_verdict": "SUPPORTED", "id": "a1"})
        run_id = await rt.create_run("t", agent_budget=1)
        rb = await store.request_rollback(
            "rb-policy", reason="verified regression",
            evidence={"eval": "e9"}, requested_by="operator")
        await store.approve_rollback(rb["id"], approved_by="operator")
        return db

    db = asyncio.run(main())

    def ev(typ, key=None, val=None):
        sql = "SELECT event_id, causation_id, correlation_id FROM events WHERE type=?"
        params = [typ]
        if key:
            sql += f" AND json_extract(payload, '$.{key}')=?"
            params.append(val)
        return db.conn.execute(sql, params).fetchone()

    requested = ev("policy.rollback_requested")
    approved = ev("policy.rollback_approved")
    activated = ev("policy.activated")
    assert requested[1] is None  # declared root: operator intent
    assert approved[1] == requested[0]
    assert activated[1] == approved[0]  # "__prev__" intra-batch chain
    assert requested[2] == approved[2] == activated[2] == "rb-policy"


# --------------------------------------------------- the capability chain

def test_capability_lifecycle_chain(tmp_path):
    """capability.validated <- proposed <- ...; promotion_reviewed <-
    validated; promoted <- promotion_reviewed."""
    from air.assurance.probes import AssuranceEngine
    from air.allocation.allocator import Strategy

    (tmp_path / "probe.txt").write_text("capability probe")

    async def honest(agent, runtime):
        rec = await runtime.call_tool(agent.id, "fs.read",
                                      {"path": "probe.txt"})
        assert str(rec.state) == "COMMITTED", rec.state
        return {"success": True, "tokens": 50, "cost_usd": 0.0}

    async def main():
        rt, db = _env(tmp_path)
        rt.register_behavior("specialist", honest)

        async def emit(type, capability_id=None, payload=None,
                       causation_id=None):
            await rt.emit(type,
                          payload={"capability_id": capability_id,
                                   **(payload or {})},
                          causation_id=causation_id,
                          correlation_id=capability_id)

        pipeline = CapabilityPipeline(db.conn, emit=emit)
        cap = await pipeline.propose(
            "chain-cap", "Prefer execute-then-verify",
            effect=CapabilityEffect(type="strategy_boost",
                                    strategy="execute_then_verify",
                                    value=0.5))
        runs = []
        for _ in range(2):
            run_id = await rt.create_run("verify", strategy=Strategy.SINGLE_AGENT,
                                         agent_budget=2)
            await rt.start_run(run_id)
            for _ in range(200):
                row = db.conn.execute(
                    "SELECT status FROM runs WHERE id=?",
                    (run_id,)).fetchone()
                if row[0] == "COMPLETED":
                    break
                await asyncio.sleep(0.05)
            runs.append(run_id)
        ev_id, verdict = await pipeline.validate(cap.capability_id, runs)
        assert verdict.value == "SUPPORTED"
        ar = AssuranceEngine(db.conn).assure(ev_id)
        assert ar.system_verdict.value == "SUPPORTED"
        await pipeline.promote(cap.capability_id, decided_by="operator")
        return db, cap.capability_id

    db, cap_id = asyncio.run(main())

    def ev(typ):
        return db.conn.execute(
            "SELECT event_id, causation_id, correlation_id FROM events"
            " WHERE type=? AND"
            " json_extract(payload, '$.capability_id')=?",
            (typ, cap_id)).fetchone()

    proposed = ev("capability.proposed")
    validated = ev("capability.validated")
    reviewed = ev("capability.promotion_reviewed")
    promoted = ev("capability.promoted")
    assert proposed[1] is None  # declared root
    assert validated[1] == proposed[0]
    assert reviewed[1] == validated[0]
    assert promoted[1] == reviewed[0]
    for e in (proposed, validated, reviewed, promoted):
        assert e[2] == cap_id


# ------------------------------------------------------ the gateway chain

def test_gateway_denial_chain(tmp_path):
    """tool.requested <- agent.started; tool.denied <- tool.requested
    when the tool is unknown (policy denial)."""
    async def main():
        rt, db = _env(tmp_path)
        from air.allocation.allocator import Strategy

        async def denied_user(agent, runtime):
            rec = await runtime.call_tool(agent.id, "nope.tool", {})
            assert str(rec.state) == "DENIED", rec.state
            return {"ok": True, "tokens": 1, "cost_usd": 0.0}

        rt.register_behavior("specialist", denied_user)
        run_id = await rt.create_run("denial chain",
                                     strategy=Strategy.SINGLE_AGENT)
        await rt.start_run(run_id)
        for _ in range(400):
            row = db.conn.execute(
                "SELECT status FROM runs WHERE id=?", (run_id,)).fetchone()
            if row[0] == "COMPLETED":
                break
            await asyncio.sleep(0.05)
        assert row[0] == "COMPLETED"
        return db, run_id

    db, run_id = asyncio.run(main())
    events = _events_by_type(db, run_id)
    requested = _one(events, "tool.requested")
    denied = [e for e in events.get("tool.denied", [])]
    assert denied, "expected tool.denied events"
    agent_started = _one(events, "agent.started")
    assert requested["causation_id"] == agent_started["event_id"]
    # The denial answers the request: every tool.denied chains to the
    # requested event (auth decision or the denial itself).
    for d in denied:
        assert d["causation_id"] == requested["event_id"], d
