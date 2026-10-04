"""Learning bridge: the validated path run -> experience -> evidence ->
evaluation -> assurance -> knowledge. No shortcuts."""

import asyncio
from pathlib import Path

import pytest

from air.agents.models import Agent
from air.agents.runtime import AgentRuntime
from air.allocation.allocator import Strategy
from air.assurance.probes import AssuranceEngine
from air.config import AirConfig
from air.evaluation.suites import EvalCase, EvalSuite, Evaluator
from air.experience.provenance import Provenance
from air.experience.recorder import ExperienceRecorder
from air.learning.bridge import BridgeBlocked, LearningBridge
from air.memory.store import MemoryType, Provenance as MemProv
from air.persistence.db import Database, find_migrations_dir


def _env(tmp_path):
    config = AirConfig(data_dir=tmp_path)
    db = Database(tmp_path / "air.db")
    db.migrate(find_migrations_dir())
    return AgentRuntime(config, db), db


async def _honest(agent: Agent, rt: AgentRuntime) -> dict:
    # Real execution evidence through the gateway: the evaluation
    # grounding gate requires ok=true, a persisted result, and a valid
    # result hash. A hand-emitted tool.completed event is not evidence
    # (deliberate: the tightened SUPPORTED semantics forbid it).
    rec = await rt.call_tool(agent.id, "fs.read",
                             {"path": "bridge_probe.txt"})
    return {"tokens": 50, "cost_usd": 0.0, "tool_state": str(rec.state)}


async def _completed_run(rt, strategy=Strategy.SINGLE_AGENT, agent_budget=2):
    run_id = await rt.create_run("verify the thing", strategy=strategy,
                                 agent_budget=agent_budget)
    await rt.start_run(run_id)
    for _ in range(200):
        row = rt.db.conn.execute("SELECT status FROM runs WHERE id=?",
                                 (run_id,)).fetchone()
        if row[0] == "COMPLETED":
            break
        await asyncio.sleep(0.05)
    return run_id


def _evaluate_and_assure(db, run_id):
    suite = EvalSuite(name="bridge-suite", cases=[
        EvalCase(id="c1", name="evidence", check="event_evidence",
                 params={"required": ["tool.completed"]}),
        EvalCase(id="c2", name="no failures", check="no_failures",
                 params={}),
    ])
    evaluator = Evaluator(db.conn)
    evaluator.save_suite(suite)
    result = evaluator.evaluate_run(run_id, suite)
    assert result.verdict.value == "SUPPORTED"
    ar = AssuranceEngine(db.conn).assure(result.id)
    assert ar.evaluator_verdict.value == "SOUND"
    return result.id, ar.id


def test_bridge_blocked_without_evaluation(tmp_path):
    async def main():
        rt, db = _env(tmp_path)
        (tmp_path / "bridge_probe.txt").write_text("learning-bridge probe")
        rt.register_behavior("specialist", _honest)
        run_id = await _completed_run(rt)
        exp = ExperienceRecorder(db.conn).list()[0]
        bridge = LearningBridge(db.conn)
        ok, reasons = bridge.assess(exp["id"])
        assert not ok
        assert any("never independently evaluated" in r for r in reasons)
        with pytest.raises(BridgeBlocked):
            bridge.promote_to_knowledge(exp["id"], "global",
                                        {"lesson": "verify first"})

    asyncio.run(main())


def test_bridge_promotes_validated_knowledge(tmp_path):
    async def main():
        rt, db = _env(tmp_path)
        (tmp_path / "bridge_probe.txt").write_text("learning-bridge probe")
        rt.register_behavior("specialist", _honest)
        run_id = await _completed_run(rt)
        exp_id = ExperienceRecorder(db.conn).list()[0]["id"]
        ev_id, ar_id = _evaluate_and_assure(db, run_id)
        ExperienceRecorder(db.conn).link_evaluation(exp_id, ev_id, ar_id)

        bridge = LearningBridge(db.conn)
        ok, reasons = bridge.assess(exp_id)
        assert ok, reasons
        mem_id = bridge.promote_to_knowledge(
            exp_id, "global", {"lesson": "verify before claiming success"},
            memory_type="semantic")
        from air.memory.store import MemoryStore
        mem = MemoryStore(db.conn).get(mem_id)
        assert mem.type == MemoryType.SEMANTIC
        # Provenance is DERIVED, never OBSERVED: it came from assessment.
        assert mem.provenance == MemProv.DERIVED
        assert mem.provenance_detail["validated_by"] == [ev_id]
        assert mem.provenance_detail["source_experience"] == exp_id

    asyncio.run(main())


def test_experience_compare_distinguishes_outcomes(tmp_path):
    async def main():
        rt, db = _env(tmp_path)
        (tmp_path / "bridge_probe.txt").write_text("learning-bridge probe")
        rt.register_behavior("specialist", _honest)
        rt.register_behavior("researcher", _honest)
        rt.register_behavior("synthesizer", _honest)
        run_ok = await _completed_run(rt, Strategy.SINGLE_AGENT)
        run_ok2 = await _completed_run(rt, Strategy.PARALLEL_AGENTS,
                                       agent_budget=4)
        rec = ExperienceRecorder(db.conn)
        ids = [e["id"] for e in rec.list()]
        assert len(ids) == 2
        cmp = rec.compare(ids)
        assert cmp["compared"] == 2
        assert len(cmp["successful"]) == 2
        # Dimensions table has the comparable schema.
        dims = cmp["dimensions"]
        assert set(dims) >= {"cognitive_strategy", "agent_count", "outcome"}
        # cognitive_strategy differs between the two runs.
        diff = [d for d in cmp["differences"]
                if d["dimension"] == "cognitive_strategy"]
        assert diff and diff[0]["discriminates_outcome"] is False  # both succeeded

    asyncio.run(main())
