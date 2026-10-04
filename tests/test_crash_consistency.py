"""Crash-consistency suite (INVARIANTS.md #9, Recovery).

One test per lifecycle transition. Each test drives the runtime to
mid-transition, simulates a crash, restarts over the SAME database file,
and asserts the persisted state is the clean pre-transition state or the
clean post-transition state -- never a contradictory hybrid.

Crash simulation (fault injection, documented seams):
- crash-before-commit: ``EventStore.insert`` (or the connection commit)
  raises ``SimulatedCrash`` inside the atomic section. The transaction
  rolls back, exactly like a process dying with the transaction open.
- crash-after-commit: ``EventStore.publish`` raises after the atomic
  commit landed. Only live fanout is lost; the ledger is complete.
- restart: the old ``Database`` is closed (process death: uncommitted work
  is discarded) and a fresh ``Database`` + ``AgentRuntime`` is built over
  the same file. The new runtime runs the startup reconciler
  (``air.persistence.recovery.reconcile``).
"""

from __future__ import annotations

import asyncio
from unittest import mock

import pytest

from air.agents.models import AgentStatus
from air.agents.runtime import AgentRuntime
from air.allocation.allocator import Strategy
from air.config import AirConfig
from air.events.fabric import EventStore, utcnow
from air.persistence.db import Database, _ThreadSafeConnection, find_migrations_dir
from air.security.approvals import ApprovalRequired, ApprovalStore
from air.security.policy import CapabilityClass
from air.tools.gateway import ToolCallState
from air.tools.registry import ToolDefinition


class SimulatedCrash(Exception):
    """Fault-injection stand-in for a process crash."""


# ------------------------------------------------------------------ helpers

def _env(tmp_path):
    """Fresh config + Database + AgentRuntime over tmp_path/air.db."""
    config = AirConfig(data_dir=tmp_path)
    db = Database(tmp_path / "air.db")
    db.migrate(find_migrations_dir())
    rt = AgentRuntime(config, db)
    return config, rt, db


def _restart(tmp_path, config):
    """Simulate process restart: new Database + AgentRuntime, same file."""
    db = Database(tmp_path / "air.db")
    db.migrate(find_migrations_dir())
    rt = AgentRuntime(config, db)
    return rt, db


def _crash(db):
    """Simulate process death: close the DB; uncommitted work is lost."""
    db.close()


def _event_count(db, type: str) -> int:
    row = db.conn.execute(
        "SELECT COUNT(*) FROM events WHERE type=?", (type,)).fetchone()
    return row[0]


def _event_payloads(db, type: str) -> list:
    import json
    rows = db.conn.execute(
        "SELECT payload FROM events WHERE type=? ORDER BY rowid",
        (type,)).fetchall()
    return [json.loads(r[0]) for r in rows]


def _policy_state(db, name="cognitive-allocation"):
    row = db.conn.execute(
        "SELECT id, current_version FROM policies WHERE name=?",
        (name,)).fetchone()
    if not row:
        return None, None, {}
    pid, cur = row
    versions = {
        r[0]: r[1] for r in db.conn.execute(
            "SELECT version, status FROM policy_versions WHERE policy_id=?",
            (pid,)).fetchall()
    }
    return pid, cur, versions


def _consumed(db, run_id: str, field: str):
    row = db.conn.execute(
        f"SELECT {field} FROM budgets WHERE run_id=? AND scope='run'",
        (run_id,)).fetchone()
    return row[0] if row else None


async def _block_forever(agent, runtime):
    await asyncio.Event().wait()
    return {}


def _gate_evidence():
    return (
        {"verdict": "SUPPORTED", "evaluation_id": "eval_crash",
         "metrics": {"success_rate": 1.0}},
        {"evaluator_verdict": "SOUND", "system_verdict": "SUPPORTED",
         "assurance_id": "asr_crash"},
    )


async def _with_v2_promoted(db, rt, name="cognitive-allocation"):
    """Real promotion of v2 (no crash). Returns (store, version)."""
    from air.learning.policies import PolicyStore
    store = PolicyStore(db.conn, store=rt.store)
    store.ensure(name)
    v2 = await store.propose(name, {"spawn_threshold": 0.9},
                             reason="crash-consistency fixture")
    ev, ar = _gate_evidence()
    await store.promote(name, v2.version, evaluation=ev, assurance=ar)
    return store, v2.version


# ------------------------------------------------- 1. policy promotion

async def _active_run_with_blocked_agent(rt, db):
    """Create + start a run whose agent blocks forever: genuinely active."""
    rt.register_behavior("specialist", _block_forever)
    run_id = await rt.create_run("active run during promotion",
                                 strategy=Strategy.SINGLE_AGENT)
    await rt.start_run(run_id)
    for _ in range(200):
        row = db.conn.execute(
            "SELECT id, status FROM agents WHERE root_run_id=?",
            (run_id,)).fetchone()
        if row and row[1] == "RUNNING":
            break
        await asyncio.sleep(0.05)
    assert row and row[1] == "RUNNING", "agent did not reach RUNNING"
    return run_id, row[0]


def test_promotion_crash_before_commit_leaves_pre_state(tmp_path):
    """Crash after the promotion writes but before the commit: the policy
    stays at v1, v2 stays CANDIDATE, no promotion event exists."""
    from air.learning.policies import PolicyStore

    async def main():
        config, rt, db = _env(tmp_path)
        run_id, agent_id = await _active_run_with_blocked_agent(rt, db)
        store = PolicyStore(db.conn, store=rt.store)
        store.ensure("cognitive-allocation")
        v2 = await store.propose("cognitive-allocation",
                                 {"spawn_threshold": 0.9},
                                 reason="crash test")
        ev, ar = _gate_evidence()
        with mock.patch.object(EventStore, "insert",
                               side_effect=SimulatedCrash("crash")):
            with pytest.raises(SimulatedCrash):
                await store.promote("cognitive-allocation", v2.version,
                                    evaluation=ev, assurance=ar)
        for task in list(rt._tasks.values()):
            task.cancel()
        _crash(db)
        return config, run_id
    config, run_id = asyncio.run(main())

    rt2, db2 = _restart(tmp_path, config)
    try:
        _pid, cur, versions = _policy_state(db2)
        assert cur == "1", f"promotion leaked through the crash: {cur}"
        assert versions.get("2") == "CANDIDATE", versions
        assert _event_count(db2, "policy.promoted") == 0
        # The active run is untouched by the aborted promotion.
        row = db2.conn.execute(
            "SELECT status, policy_version FROM runs WHERE id=?",
            (run_id,)).fetchone()
        assert row[0] == "RUNNING", row
        assert row[1] == "cognitive-allocation@v1", row
        ok, bad = rt2.store.verify_chain()
        assert ok, f"event chain broken at {bad}"
    finally:
        db2.close()


def test_promotion_crash_after_commit_leaves_post_state(tmp_path):
    """Crash after the atomic commit but before bus fanout: the promotion
    is fully durable, including its event. The active run keeps its v1 tag:
    promotion never rewrites active runs."""
    from air.learning.policies import PolicyStore

    async def main():
        config, rt, db = _env(tmp_path)
        run_id, agent_id = await _active_run_with_blocked_agent(rt, db)
        store = PolicyStore(db.conn, store=rt.store)
        store.ensure("cognitive-allocation")
        v2 = await store.propose("cognitive-allocation",
                                 {"spawn_threshold": 0.9},
                                 reason="crash test")
        ev, ar = _gate_evidence()
        with mock.patch.object(EventStore, "publish",
                               side_effect=SimulatedCrash("crash")):
            with pytest.raises(SimulatedCrash):
                await store.promote("cognitive-allocation", v2.version,
                                    evaluation=ev, assurance=ar)
        for task in list(rt._tasks.values()):
            task.cancel()
        _crash(db)
        return config, run_id
    config, run_id = asyncio.run(main())

    rt2, db2 = _restart(tmp_path, config)
    try:
        _pid, cur, versions = _policy_state(db2)
        assert cur == "2", f"promotion lost across the crash: {cur}"
        assert versions.get("2") == "PROMOTED", versions
        assert versions.get("1") == "DEPRECATED", versions
        # The promotion event is in the ledger even though fanout died.
        payloads = _event_payloads(db2, "policy.promoted")
        assert len(payloads) == 1, payloads
        assert payloads[0]["version"] == "2", payloads[0]
        # Active run untouched: still RUNNING under its v1 tag.
        row = db2.conn.execute(
            "SELECT status, policy_version FROM runs WHERE id=?",
            (run_id,)).fetchone()
        assert row[0] == "RUNNING", row
        assert row[1] == "cognitive-allocation@v1", row
        ok, bad = rt2.store.verify_chain()
        assert ok, f"event chain broken at {bad}"
    finally:
        db2.close()


# ------------------------------------------------- 2. rollback

def test_rollback_crash_before_commit_leaves_pre_state(tmp_path):
    """Crash during rollback approval: v2 stays active, the rollback stays
    REQUESTED, no approval/activation events exist."""
    from air.learning.policies import PolicyStore

    async def main():
        config, rt, db = _env(tmp_path)
        store, _v2 = await _with_v2_promoted(db, rt)
        # A run created under v2: it is "affected" if rollback lands.
        run_id = await rt.create_run("run under v2")
        rb = await store.request_rollback(
            "cognitive-allocation", reason="regression",
            evidence={}, requested_by="test")
        with mock.patch.object(EventStore, "insert",
                               side_effect=SimulatedCrash("crash")):
            with pytest.raises(SimulatedCrash):
                await store.approve_rollback(rb["id"], approved_by="test")
        _crash(db)
        return config, run_id, rb["id"]
    config, run_id, rb_id = asyncio.run(main())

    rt2, db2 = _restart(tmp_path, config)
    try:
        _pid, cur, versions = _policy_state(db2)
        assert cur == "2", f"rollback leaked through the crash: {cur}"
        assert versions.get("2") == "PROMOTED", versions
        assert versions.get("1") == "DEPRECATED", versions
        row = db2.conn.execute(
            "SELECT status FROM policy_rollbacks WHERE id=?",
            (rb_id,)).fetchone()
        assert row[0] == "REQUESTED", row
        assert _event_count(db2, "policy.rollback_approved") == 0
        assert _event_count(db2, "policy.activated") == 0
        # The v2 run is unaffected.
        row = db2.conn.execute(
            "SELECT policy_version FROM runs WHERE id=?", (run_id,)).fetchone()
        assert row[0] == "cognitive-allocation@v2", row
        ok, bad = rt2.store.verify_chain()
        assert ok, f"event chain broken at {bad}"
    finally:
        db2.close()


def test_rollback_crash_after_commit_leaves_post_state(tmp_path):
    """Crash after the rollback commit but before fanout: v1 is active
    again, the rollback is APPROVED with its affected runs, and both
    events are in the ledger."""
    from air.learning.policies import PolicyStore

    async def main():
        config, rt, db = _env(tmp_path)
        store, _v2 = await _with_v2_promoted(db, rt)
        run_id = await rt.create_run("run under v2")
        rb = await store.request_rollback(
            "cognitive-allocation", reason="regression",
            evidence={}, requested_by="test")
        with mock.patch.object(EventStore, "publish",
                               side_effect=SimulatedCrash("crash")):
            with pytest.raises(SimulatedCrash):
                await store.approve_rollback(rb["id"], approved_by="test")
        _crash(db)
        return config, run_id, rb["id"]
    config, run_id, rb_id = asyncio.run(main())

    rt2, db2 = _restart(tmp_path, config)
    try:
        _pid, cur, versions = _policy_state(db2)
        assert cur == "1", f"rollback lost across the crash: {cur}"
        assert versions.get("1") == "PROMOTED", versions
        assert versions.get("2") == "DEPRECATED", versions
        row = db2.conn.execute(
            "SELECT status, affected_runs FROM policy_rollbacks WHERE id=?",
            (rb_id,)).fetchone()
        assert row[0] == "APPROVED", row
        import json
        assert run_id in json.loads(row[1]), row
        approved = _event_payloads(db2, "policy.rollback_approved")
        assert len(approved) == 1 and approved[0]["to_version"] == "1"
        activated = _event_payloads(db2, "policy.activated")
        assert len(activated) == 1 and activated[0]["version"] == "1"
        ok, bad = rt2.store.verify_chain()
        assert ok, f"event chain broken at {bad}"
    finally:
        db2.close()


# ------------------------------------------------- 3. agent spawning

def test_spawn_crash_before_commit_leaves_pre_state(tmp_path):
    """Crash inside the spawn commit: no agent row, no consumed budget
    slot, no lineage row, no creation event."""
    async def main():
        config, rt, db = _env(tmp_path)
        run_id = await rt.create_run("spawn target")
        parent = await rt.create_agent(run_id, "parent", "parent work")
        before = _consumed(db, run_id, "consumed_agents")
        with mock.patch.object(EventStore, "insert",
                               side_effect=SimulatedCrash("crash")):
            with pytest.raises(SimulatedCrash):
                await rt.create_agent(run_id, "child", "child work",
                                      parent_id=parent.id)
        _crash(db)
        return config, run_id, parent.id, before
    config, run_id, parent_id, before = asyncio.run(main())

    rt2, db2 = _restart(tmp_path, config)
    try:
        n = db2.conn.execute(
            "SELECT COUNT(*) FROM agents WHERE root_run_id=? AND role=?",
            (run_id, "child")).fetchone()[0]
        assert n == 0, "child agent row leaked through the crash"
        assert _consumed(db2, run_id, "consumed_agents") == before
        n = db2.conn.execute(
            "SELECT COUNT(*) FROM agent_lineage WHERE parent_id=?",
            (parent_id,)).fetchone()[0]
        assert n == 0, "lineage row leaked through the crash"
        assert _event_count(db2, "agent.created") == 1, \
            "only the parent's creation event may exist"
        ok, bad = rt2.store.verify_chain()
        assert ok, f"event chain broken at {bad}"
    finally:
        db2.close()


def test_spawn_crash_after_commit_leaves_post_state(tmp_path):
    """Crash after the spawn commit but before fanout: the agent row, the
    consumed budget slot, the lineage row, and the creation event are all
    durable."""
    async def main():
        config, rt, db = _env(tmp_path)
        run_id = await rt.create_run("spawn target")
        parent = await rt.create_agent(run_id, "parent", "parent work")
        before = _consumed(db, run_id, "consumed_agents")
        with mock.patch.object(EventStore, "publish",
                               side_effect=SimulatedCrash("crash")):
            with pytest.raises(SimulatedCrash):
                await rt.create_agent(run_id, "child", "child work",
                                      parent_id=parent.id)
        _crash(db)
        return config, run_id, parent.id, before
    config, run_id, parent_id, before = asyncio.run(main())

    rt2, db2 = _restart(tmp_path, config)
    try:
        # The restart reconciler marks the never-launched agents FAILED;
        # the rows and their creation events must exist regardless.
        rows = db2.conn.execute(
            "SELECT id, status FROM agents WHERE root_run_id=? AND role=?",
            (run_id, "child")).fetchall()
        assert len(rows) == 1, "child agent row lost across the crash"
        assert rows[0][1] in ("CREATED", "FAILED"), rows[0]
        assert _consumed(db2, run_id, "consumed_agents") == before + 1
        n = db2.conn.execute(
            "SELECT COUNT(*) FROM agent_lineage WHERE parent_id=?",
            (parent_id,)).fetchone()[0]
        assert n == 1, "lineage row lost across the crash"
        payloads = _event_payloads(db2, "agent.created")
        roles = {p["role"] for p in payloads}
        assert {"parent", "child"} <= roles, roles
        ok, bad = rt2.store.verify_chain()
        assert ok, f"event chain broken at {bad}"
    finally:
        db2.close()


# ------------------------------------------------- 4. tool execution

def _blocking_tool_registry():
    from air.tools.builtin import build_default_registry
    reg = build_default_registry()
    started = asyncio.Event()
    release = asyncio.Event()

    async def blocking_handler(args, ctx):
        started.set()
        await release.wait()
        return {"ok": True}

    reg.register(ToolDefinition(
        name="test.block", description="blocking test tool",
        input_schema={"type": "object"},
        capability=CapabilityClass.READ), blocking_handler)
    return reg, started


def test_tool_execution_crash_mid_dispatch(tmp_path):
    """Crash while the tool handler is executing: on restart the call is
    INTERRUPTED (terminal), the tool.interrupted event is in the ledger,
    and the budget unit consumed for the attempt is counted exactly once:
    no leak, no double-spend."""
    async def main():
        config, rt, db = _env(tmp_path)
        reg, started = _blocking_tool_registry()
        rt.tool_gateway()._registry = reg
        run_id = await rt.create_run("tool crash target")
        agent = await rt.create_agent(run_id, "worker", "work",
                                      granted=["READ"])
        task = asyncio.create_task(rt.call_tool(agent.id, "test.block", {}))
        await asyncio.wait_for(started.wait(), timeout=10)
        for _ in range(200):
            row = db.conn.execute(
                "SELECT id, state FROM tool_calls ORDER BY created_at DESC"
                " LIMIT 1").fetchone()
            if row and row[1] == "DISPATCHED":
                break
            await asyncio.sleep(0.01)
        assert row and row[1] == "DISPATCHED", f"not dispatched: {row}"
        call_id = row[0]
        consumed_before = _consumed(db, run_id, "consumed_tool_calls")
        assert consumed_before == 1, consumed_before
        # Crash mid-execution: the handler dies with the process.
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
        _crash(db)
        return config, run_id, call_id
    config, run_id, call_id = asyncio.run(main())

    rt2, db2 = _restart(tmp_path, config)
    try:
        row = db2.conn.execute(
            "SELECT state, budget_reservation, error FROM tool_calls"
            " WHERE id=?", (call_id,)).fetchone()
        assert row[0] == "INTERRUPTED", \
            f"stuck call not reconciled to a terminal state: {row}"
        assert row[1], "budget reservation record lost"
        payloads = _event_payloads(db2, "tool.interrupted")
        assert len(payloads) == 1, payloads
        assert payloads[0]["call_id"] == call_id, payloads[0]
        # Exactly one budget unit spent on the attempt: not zero (leak),
        # not two (double-spend on restart).
        assert _consumed(db2, run_id, "consumed_tool_calls") == 1
        ok, bad = rt2.store.verify_chain()
        assert ok, f"event chain broken at {bad}"
    finally:
        db2.close()


# ------------------------------------------------- 5. approval while pending

def _privileged_tool_registry():
    from air.tools.builtin import build_default_registry
    reg = build_default_registry()

    async def priv_handler(args, ctx):
        return {"ok": True}

    reg.register(ToolDefinition(
        name="test.priv", description="privileged test tool",
        input_schema={"type": "object"},
        capability=CapabilityClass.PRIVILEGED), priv_handler)
    return reg


def test_approval_granted_then_crash_before_resume(tmp_path):
    """Approval granted, crash before resume: on restart the approval is
    APPROVED, the tool call is still APPROVAL_PENDING (a legitimate rest
    state, not reconciled away), and resume() fails closed -- the call goes
    to FAILED with an explicit reason instead of executing blindly or
    hanging forever."""
    async def main():
        config, rt, db = _env(tmp_path)
        rt.tool_gateway()._registry = _privileged_tool_registry()
        run_id = await rt.create_run("approval crash target")
        agent = await rt.create_agent(run_id, "operator", "op work",
                                      granted=["PRIVILEGED"])
        with pytest.raises(ApprovalRequired) as ei:
            await rt.call_tool(agent.id, "test.priv", {})
        ap_id = ei.value.approval_id
        # The approval row and the PENDING transition committed atomically.
        arow = db.conn.execute(
            "SELECT status FROM approvals WHERE id=?", (ap_id,)).fetchone()
        trow = db.conn.execute(
            "SELECT state, approval_id FROM tool_calls WHERE approval_id=?",
            (ap_id,)).fetchone()
        assert arow[0] == "PENDING", arow
        assert trow[0] == "APPROVAL_PENDING", trow
        # The operator approves; the process dies before resume().
        ApprovalStore(db.conn).decide(ap_id, True, "operator")
        _crash(db)
        return config, ap_id
    config, ap_id = asyncio.run(main())

    async def after():
        rt2, db2 = _restart(tmp_path, config)
        try:
            # APPROVAL_PENDING is a rest state: the reconciler leaves it.
            trow = db2.conn.execute(
                "SELECT id, state FROM tool_calls WHERE approval_id=?",
                (ap_id,)).fetchone()
            assert trow[1] == "APPROVAL_PENDING", trow
            assert ApprovalStore(db2.conn).get(ap_id)["status"] == "APPROVED"
            # Resume fails closed: the in-memory call context is gone, so
            # the call is failed explicitly rather than executed blindly.
            rec = await rt2.tool_gateway().resume(ap_id)
            assert rec.state == ToolCallState.FAILED, rec.state
            assert "after restart" in (rec.error or ""), rec.error
            # Consistent final state: APPROVED approval, FAILED call, and a
            # tool.failed event whose foreign keys resolve.
            assert ApprovalStore(db2.conn).get(ap_id)["status"] == "APPROVED"
            payloads = _event_payloads(db2, "tool.failed")
            assert any(p["call_id"] == trow[0] for p in payloads), payloads
            ok, bad = rt2.store.verify_chain()
            assert ok, f"event chain broken at {bad}"
        finally:
            db2.close()
    asyncio.run(after())


# ------------------------------------------------- 6. experience creation

def test_experience_crash_before_commit_leaves_pre_state(tmp_path):
    """Crash during experience recording (after the run itself completed):
    the run stays COMPLETED with its event, but no experience row and no
    experience.created event exist."""
    async def main():
        config, rt, db = _env(tmp_path)
        run_id = await rt.create_run("experience crash target")
        agent = await rt.create_agent(run_id, "worker", "work")
        rt._set_status(agent, AgentStatus.COMPLETED)

        orig_insert = EventStore.insert
        hit = []

        def flaky_insert(self, event):
            # Crash only at the experience commit point, not the earlier
            # run.completed commit.
            if event.type == "experience.created":
                hit.append(True)
                raise SimulatedCrash("crash before experience commit")
            return orig_insert(self, event)

        with mock.patch.object(EventStore, "insert", flaky_insert):
            # _complete_run's designed except-path swallows the fault and
            # records experience.failed; the experience transaction itself
            # still rolled back, like a real crash.
            await rt._complete_run(run_id, {"mode": "test"})
        assert hit, "fault was not injected at the experience commit point"
        _crash(db)
        return config, run_id
    config, run_id = asyncio.run(main())

    rt2, db2 = _restart(tmp_path, config)
    try:
        row = db2.conn.execute(
            "SELECT status FROM runs WHERE id=?", (run_id,)).fetchone()
        assert row[0] == "COMPLETED", row
        assert _event_count(db2, "run.completed") == 1
        n = db2.conn.execute(
            "SELECT COUNT(*) FROM experiences WHERE run_id=?",
            (run_id,)).fetchone()[0]
        assert n == 0, "experience row leaked through the crash"
        assert _event_count(db2, "experience.created") == 0
        ok, bad = rt2.store.verify_chain()
        assert ok, f"event chain broken at {bad}"
    finally:
        db2.close()


def test_experience_crash_after_commit_leaves_post_state(tmp_path):
    """Experience recorded and committed, crash before fanout: the row and
    its experience.created event are both durable."""
    from air.experience.recorder import ExperienceRecorder

    async def main():
        config, rt, db = _env(tmp_path)
        run_id = await rt.create_run("experience crash target")
        agent = await rt.create_agent(run_id, "worker", "work")
        rt._set_status(agent, AgentStatus.COMPLETED)
        recorder = ExperienceRecorder(db.conn, store=rt.store)
        exp, event = recorder.record_run(run_id)
        assert event is not None and event.type == "experience.created"
        # Crash: the runtime dies without ever publishing the event.
        _crash(db)
        return config, run_id, exp.id
    config, run_id, exp_id = asyncio.run(main())

    rt2, db2 = _restart(tmp_path, config)
    try:
        row = db2.conn.execute(
            "SELECT id, run_id FROM experiences WHERE id=?",
            (exp_id,)).fetchone()
        assert row is not None, "experience row lost across the crash"
        assert row[1] == run_id
        payloads = _event_payloads(db2, "experience.created")
        assert len(payloads) == 1, payloads
        assert payloads[0]["experience_id"] == exp_id, payloads[0]
        ok, bad = rt2.store.verify_chain()
        assert ok, f"event chain broken at {bad}"
    finally:
        db2.close()


# ------------------------------------------------- 7/8. evaluation & assurance

def _completed_run_with_experience(tmp_path):
    """A fully completed run (experience auto-recorded). The agent executes
    a real tool, so the run carries execution evidence. Returns
    (config, rt, db, run_id)."""
    async def main():
        config, rt, db = _env(tmp_path)
        (tmp_path / "probe.txt").write_text("crash-consistency probe")

        async def tool_user(agent, runtime):
            rec = await runtime.call_tool(agent.id, "fs.read",
                                          {"path": "probe.txt"})
            assert str(rec.state) == "COMMITTED", rec.state
            return {"ok": True, "tokens": 10, "cost_usd": 0.0}

        rt.register_behavior("specialist", tool_user)
        run_id = await rt.create_run("evaluation crash target",
                                     strategy=Strategy.SINGLE_AGENT)
        await rt.start_run(run_id)
        for _ in range(400):
            row = db.conn.execute(
                "SELECT status FROM runs WHERE id=?", (run_id,)).fetchone()
            if row[0] == "COMPLETED":
                break
            await asyncio.sleep(0.05)
        assert row[0] == "COMPLETED", f"run did not complete: {row}"
        n = db.conn.execute(
            "SELECT COUNT(*) FROM experiences WHERE run_id=?",
            (run_id,)).fetchone()[0]
        assert n == 1, "experience was not recorded"
        return config, rt, db, run_id
    return asyncio.run(main())


def _crash_suite():
    from air.evaluation.suites import EvalCase, EvalSuite
    return EvalSuite(name="crash-suite", version="1", cases=[
        EvalCase(id="c1", name="tool evidence", check="event_evidence",
                 params={"required": ["tool.completed"]}),
        EvalCase(id="c2", name="agents completed", check="agents_completed",
                 params={"min_completed": 1}),
    ])


def test_evaluation_crash_before_commit_leaves_no_row(tmp_path):
    """Crash before the evaluation persist commits: no partial
    evaluation row exists after restart. (Single-statement transaction:
    inherently atomic.)"""
    from air.evaluation.suites import Evaluator

    config, rt, db, run_id = _completed_run_with_experience(tmp_path)
    suite = _crash_suite()
    Evaluator(db.conn).save_suite(suite)
    with mock.patch.object(_ThreadSafeConnection, "commit",
                           side_effect=SimulatedCrash("crash")):
        with pytest.raises(SimulatedCrash):
            Evaluator(db.conn).evaluate_run(run_id, suite)
    # Process death discards the open transaction.
    _crash(db)

    rt2, db2 = _restart(tmp_path, config)
    try:
        n = db2.conn.execute(
            "SELECT COUNT(*) FROM evaluation_runs").fetchone()[0]
        assert n == 0, "partial evaluation row leaked through the crash"
        ok, bad = rt2.store.verify_chain()
        assert ok, f"event chain broken at {bad}"
    finally:
        db2.close()


def test_evaluation_completed_row_is_self_consistent(tmp_path):
    """A completed evaluation row is whole: id, verdict, metrics, evidence
    hash, and timestamps are all present (no torn writes)."""
    from air.evaluation.suites import Evaluator

    config, rt, db, run_id = _completed_run_with_experience(tmp_path)
    suite = _crash_suite()
    evaluator = Evaluator(db.conn)
    evaluator.save_suite(suite)
    result = evaluator.evaluate_run(run_id, suite)
    assert result.verdict.value == "SUPPORTED"
    _crash(db)

    rt2, db2 = _restart(tmp_path, config)
    try:
        row = db2.conn.execute(
            "SELECT id, suite_id, verdict, metrics, evidence, started_at,"
            " completed_at FROM evaluation_runs WHERE id=?",
            (result.id,)).fetchone()
        assert row is not None, "evaluation row lost"
        assert row[2] == "SUPPORTED", row
        import json
        metrics = json.loads(row[3])
        evidence = json.loads(row[4])
        assert "evidence_hash" in evidence, evidence
        assert row[5] and row[6], "torn timestamps"
        assert metrics, "empty metrics"
    finally:
        db2.close()


def test_assurance_crash_before_commit_leaves_no_row(tmp_path):
    """Crash before the assurance persist commits: no partial assurance
    row exists after restart."""
    from air.assurance.probes import AssuranceEngine
    from air.evaluation.suites import Evaluator

    config, rt, db, run_id = _completed_run_with_experience(tmp_path)
    suite = _crash_suite()
    evaluator = Evaluator(db.conn)
    evaluator.save_suite(suite)
    result = evaluator.evaluate_run(run_id, suite)
    with mock.patch.object(_ThreadSafeConnection, "commit",
                           side_effect=SimulatedCrash("crash")):
        with pytest.raises(SimulatedCrash):
            AssuranceEngine(db.conn).assure(result.id)
    _crash(db)

    rt2, db2 = _restart(tmp_path, config)
    try:
        n = db2.conn.execute(
            "SELECT COUNT(*) FROM assurance_runs").fetchone()[0]
        assert n == 0, "partial assurance row leaked through the crash"
        # The evaluation it was assuring is intact.
        n = db2.conn.execute(
            "SELECT COUNT(*) FROM evaluation_runs WHERE id=?",
            (result.id,)).fetchone()[0]
        assert n == 1
        ok, bad = rt2.store.verify_chain()
        assert ok, f"event chain broken at {bad}"
    finally:
        db2.close()


def test_assurance_completed_row_is_self_consistent(tmp_path):
    """A completed assurance row is whole: both verdicts, probe results,
    and evidence hash are all present."""
    from air.assurance.probes import AssuranceEngine, EvaluatorVerdict
    from air.evaluation.suites import Evaluator

    config, rt, db, run_id = _completed_run_with_experience(tmp_path)
    suite = _crash_suite()
    evaluator = Evaluator(db.conn)
    evaluator.save_suite(suite)
    result = evaluator.evaluate_run(run_id, suite)
    ar = AssuranceEngine(db.conn).assure(result.id)
    assert ar.evaluator_verdict == EvaluatorVerdict.SOUND, \
        [p.model_dump() for p in ar.probes]
    _crash(db)

    rt2, db2 = _restart(tmp_path, config)
    try:
        import json
        row = db2.conn.execute(
            "SELECT id, target, evaluator_verdict, system_verdict, probes,"
            " evidence FROM assurance_runs WHERE id=?",
            (ar.id,)).fetchone()
        assert row is not None, "assurance row lost"
        assert row[2] == "SOUND", row
        assert row[3] == "SUPPORTED", row
        probes = json.loads(row[4])
        assert len(probes) > 0, "probe results missing"
        evidence = json.loads(row[5])
        assert "evidence_hash" in evidence, evidence
    finally:
        db2.close()


# ------------------------------------------------- 9. DB reconnect

def test_db_reconnect_mid_run_continues_safely(tmp_path):
    """Close and reopen the SQLite connection mid-run: the new runtime sees
    intact state, the never-launched agent is reconciled to a terminal
    state (not stuck), and the run can continue safely."""
    async def main():
        config, rt, db = _env(tmp_path)
        run_id = await rt.create_run("reconnect target")
        agent = await rt.create_agent(run_id, "worker", "work")
        n_events_before = _event_count(db, "agent.created")
        # Disconnect: process death for the DB handle.
        _crash(db)
        return config, run_id, agent.id, n_events_before
    config, run_id, agent_id, n_events_before = asyncio.run(main())

    async def after():
        rt2, db2 = _restart(tmp_path, config)
        try:
            # State survived the reconnect.
            row = db2.conn.execute(
                "SELECT id, goal, status FROM runs WHERE id=?",
                (run_id,)).fetchone()
            assert row is not None and row[1] == "reconnect target", row
            assert row[2] == "CREATED", row
            # The never-launched agent is reconciled to FAILED (terminal),
            # not left in a state that pretends it might still run.
            row = db2.conn.execute(
                "SELECT status FROM agents WHERE id=?", (agent_id,)).fetchone()
            assert row[0] == "FAILED", \
                f"agent left in non-terminal state after reconnect: {row}"
            # The run can continue: new agents, new events, chain intact.
            agent2 = await rt2.create_agent(run_id, "worker2", "more work")
            assert agent2.id != agent_id
            assert _event_count(db2, "agent.created") == n_events_before + 1
            ok, bad = rt2.store.verify_chain()
            assert ok, f"event chain broken at {bad}"
        finally:
            db2.close()
    asyncio.run(after())


# ------------------------------------------------- 10. agent cancellation

def test_cancel_inflight_agent_is_consistent(tmp_path):
    """Cancel an agent blocked inside a tool call: the agent reaches a
    terminal persisted state, the termination is recorded as an event, the
    orphaned tool call is reconciled on restart, and the budget unit spent
    on the attempt is counted exactly once."""
    async def main():
        config, rt, db = _env(tmp_path)
        reg, started = _blocking_tool_registry()
        rt.tool_gateway()._registry = reg

        async def tool_blocker(agent, runtime):
            await runtime.call_tool(agent.id, "test.block", {})
            return {"ok": True}

        rt.register_behavior("specialist", tool_blocker)
        run_id = await rt.create_run("cancel target",
                                     strategy=Strategy.SINGLE_AGENT)
        await rt.start_run(run_id)
        await asyncio.wait_for(started.wait(), timeout=10)
        for _ in range(200):
            row = db.conn.execute(
                "SELECT id, state FROM tool_calls ORDER BY created_at DESC"
                " LIMIT 1").fetchone()
            if row and row[1] == "DISPATCHED":
                break
            await asyncio.sleep(0.01)
        assert row and row[1] == "DISPATCHED", f"not dispatched: {row}"
        call_id = row[0]
        agent_id = db.conn.execute(
            "SELECT id FROM agents WHERE root_run_id=?",
            (run_id,)).fetchone()[0]
        # Cancel the in-flight agent through the public API.
        terminated = await rt.terminate_agent(
            agent_id, subtree=False, reason="test cancellation")
        assert terminated == [agent_id], terminated
        task = rt._tasks.get(agent_id)
        if task is not None:
            try:
                await asyncio.wait_for(task, timeout=10)
            except (asyncio.CancelledError, Exception):
                pass
        status = db.conn.execute(
            "SELECT status, terminated_at FROM agents WHERE id=?",
            (agent_id,)).fetchone()
        # Terminal either way: terminate_agent persists TERMINATED, then the
        # cancelled task's handler persists CANCELLED. Both are terminal and
        # honest; the row must never be left non-terminal.
        assert status[0] in ("CANCELLED", "TERMINATED"), status
        assert status[1], "terminated_at not set"
        assert _event_count(db, "agent.terminated") >= 1
        consumed = _consumed(db, run_id, "consumed_tool_calls")
        assert consumed == 1, consumed
        _crash(db)
        return config, run_id, call_id, agent_id
    config, run_id, call_id, agent_id = asyncio.run(main())

    async def after():
        rt2, db2 = _restart(tmp_path, config)
        try:
            # The orphaned tool call is reconciled; the budget unit is still
            # counted exactly once (no double-spend by recovery).
            row = db2.conn.execute(
                "SELECT state FROM tool_calls WHERE id=?", (call_id,)).fetchone()
            assert row[0] == "INTERRUPTED", row
            assert _consumed(db2, run_id, "consumed_tool_calls") == 1
            payloads = _event_payloads(db2, "tool.interrupted")
            assert any(p["call_id"] == call_id for p in payloads), payloads
            # The agent is still terminal after restart.
            row = db2.conn.execute(
                "SELECT status FROM agents WHERE id=?", (agent_id,)).fetchone()
            assert row[0] in ("CANCELLED", "TERMINATED", "FAILED"), row
            ok, bad = rt2.store.verify_chain()
            assert ok, f"event chain broken at {bad}"
        finally:
            db2.close()
    asyncio.run(after())


def test_reconciler_covers_all_stuck_execution_states(tmp_path):
    """Every non-terminal tool-call state except APPROVAL_PENDING is
    transient inside execute()/resume(), so after a restart any row left
    in one of those states is definitionally stuck: the reconciler moves
    each to INTERRUPTED with a tool.interrupted event. APPROVAL_PENDING is
    left alone (legitimate rest state)."""
    import uuid as _uuid

    async def main():
        config, rt, db = _env(tmp_path)
        run_id = await rt.create_run("stuck states target")
        agent = await rt.create_agent(run_id, "worker", "work")
        states = ["REQUESTED", "VALIDATED", "RESERVED", "DISPATCHED",
                  "OBSERVED", "VERIFIED", "APPROVAL_PENDING"]
        call_ids = {}
        for st in states:
            cid = "tc_" + _uuid.uuid4().hex[:12]
            db.conn.execute(
                "INSERT INTO tool_calls (id, run_id, agent_id, tool_name,"
                " capability, state, args_redacted, args_hash, created_at)"
                " VALUES (?, ?, ?, ?, 'READ', ?, '{}', 'x', ?)",
                (cid, run_id, agent.id, "fs.read", st, utcnow()))
            call_ids[st] = cid
        db.conn.commit()
        _crash(db)
        return config, run_id, call_ids
    config, run_id, call_ids = asyncio.run(main())

    rt2, db2 = _restart(tmp_path, config)
    try:
        for st, cid in call_ids.items():
            row = db2.conn.execute(
                "SELECT state FROM tool_calls WHERE id=?", (cid,)).fetchone()
            if st == "APPROVAL_PENDING":
                assert row[0] == "APPROVAL_PENDING", \
                    f"reconciler wrongly touched a resting call: {row}"
            else:
                assert row[0] == "INTERRUPTED", \
                    f"stuck state {st} not reconciled: {row}"
        payloads = _event_payloads(db2, "tool.interrupted")
        interrupted_ids = {p["call_id"] for p in payloads}
        for st, cid in call_ids.items():
            if st != "APPROVAL_PENDING":
                assert cid in interrupted_ids, f"no event for {cid}"
        # Idempotent: a second restart changes nothing.
        db2.close()
    finally:
        try:
            db2.close()
        except Exception:
            pass

    rt3, db3 = _restart(tmp_path, config)
    try:
        for st, cid in call_ids.items():
            row = db3.conn.execute(
                "SELECT state FROM tool_calls WHERE id=?", (cid,)).fetchone()
            expected = "APPROVAL_PENDING" if st == "APPROVAL_PENDING" \
                else "INTERRUPTED"
            assert row[0] == expected, (st, row)
        assert _event_count(db3, "tool.interrupted") == 6, \
            "reconciler is not idempotent"
        ok, bad = rt3.store.verify_chain()
        assert ok, f"event chain broken at {bad}"
    finally:
        db3.close()
