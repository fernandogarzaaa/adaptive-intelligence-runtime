"""Capability acquisition: propose -> validate -> assure -> promote -> use ->
rollback. Every transition is real and gated."""

import asyncio
from pathlib import Path

import pytest

from air.agents.models import Agent
from air.agents.runtime import AgentRuntime
from air.allocation.allocator import Strategy
from air.capabilities.models import CapabilityEffect, CapabilityStatus
from air.capabilities.pipeline import CapabilityPipeline, GateBlocked
from air.capabilities.store import CapabilityStore
from air.config import AirConfig
from air.evaluation.suites import EvalCase, EvalSuite
from air.persistence.db import Database


def _env(tmp_path):
    config = AirConfig(data_dir=tmp_path)
    db = Database(tmp_path / "air.db")
    db.migrate(Path(__file__).resolve().parents[1] / "migrations")
    rt = AgentRuntime(config, db)

    async def emit(type, capability_id=None, payload=None):
        await rt.emit(type, payload={"capability_id": capability_id,
                                     **(payload or {})})

    pipeline = CapabilityPipeline(db.conn, emit=emit)
    return rt, db, pipeline


async def _honest_worker(agent: Agent, rt: AgentRuntime) -> dict:
    await rt.emit("tool.completed", run_id=agent.root_run_id,
                  agent_id=agent.id, payload={"tool": "verify"})
    return {"tokens": 50, "cost_usd": 0.0}


async def _run_task(rt, strategy=Strategy.SINGLE_AGENT):
    run_id = await rt.create_run("verify the thing",
                                 strategy=strategy, agent_budget=2)
    await rt.start_run(run_id)
    for _ in range(200):
        row = rt.db.conn.execute("SELECT status FROM runs WHERE id=?",
                                 (run_id,)).fetchone()
        if row[0] == "COMPLETED":
            break
        await asyncio.sleep(0.05)
    return run_id


def test_full_lifecycle_promote_use_rollback(tmp_path):
    async def main():
        rt, db, pipeline = _env(tmp_path)
        rt.register_behavior("specialist", _honest_worker)

        # 1. Propose: candidate with a real effect (boost execute_then_verify).
        cap = await pipeline.propose(
            "verify-first", "Prefer execute-then-verify strategy",
            effect=CapabilityEffect(type="strategy_boost",
                                    strategy="execute_then_verify", value=0.5),
            created_from="exp_001")
        assert cap.validation_status == CapabilityStatus.CANDIDATE

        # 2. Gate blocks promotion without evaluation.
        ok, reasons = pipeline.promotion_gate(cap.capability_id)
        assert not ok
        with pytest.raises(GateBlocked):
            await pipeline.promote(cap.capability_id)

        # 3. Validate over two honest task runs.
        run1 = await _run_task(rt)
        run2 = await _run_task(rt)
        ev_id, verdict = await pipeline.validate(
            cap.capability_id, [run1, run2])
        assert verdict.value == "SUPPORTED"
        store = CapabilityStore(db.conn)
        assert store.get(cap.capability_id).validation_status == \
            CapabilityStatus.VALIDATING

        # 4. Assurance: evaluator must be SOUND.
        from air.assurance.probes import AssuranceEngine
        ar = AssuranceEngine(db.conn).assure(ev_id)
        assert ar.evaluator_verdict.value == "SOUND", \
            [p.model_dump() for p in ar.probes]
        assert ar.system_verdict.value == "SUPPORTED"

        # 5. Promote.
        cap = await pipeline.promote(cap.capability_id)
        assert cap.validation_status == CapabilityStatus.PROMOTED

        # 6. The promoted capability changes future allocation for real.
        run3 = await rt.create_run("audit this code and verify each finding",
                                   agent_budget=4)
        plan = rt._run_configs[run3]["plan"]
        assert any("capability boost" in r for r in plan["rationale"]), \
            plan["rationale"]
        assert plan["scores"]["execute_then_verify"] > \
            plan["scores"]["single_agent"]

        # 7. Rollback: subsequent runs no longer use it.
        cap = await pipeline.rollback(cap.capability_id)
        assert cap.validation_status == CapabilityStatus.DEPRECATED
        run4 = await rt.create_run("audit this code and verify each finding",
                                   agent_budget=4)
        plan4 = rt._run_configs[run4]["plan"]
        assert not any("capability boost" in r
                       for r in plan4["rationale"])

        # 8. Performance tracking is real.
        store.record_use(cap.capability_id, True)
        store.record_use(cap.capability_id, False)
        perf = store.get(cap.capability_id).performance
        assert perf["uses"] == 2 and perf["successes"] == 1

    asyncio.run(main())


def test_reject_is_terminal(tmp_path):
    async def main():
        _, _, pipeline = _env(tmp_path)
        cap = await pipeline.propose("bad-idea", "does not work")
        await pipeline.reject(cap.capability_id, "failed validation")
        with pytest.raises(GateBlocked):
            await pipeline.promote(cap.capability_id)

    asyncio.run(main())
