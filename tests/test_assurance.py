"""Acceptance test 3: attack the evaluator.

A weak suite (no evidence check) accepts a liar run. Assurance must catch the
false accept, mark the evaluator EXPLOITABLE, invalidate the system verdict,
and the promotion gate must refuse to promote.
"""

import asyncio
from pathlib import Path

from air.agents.models import Agent
from air.agents.runtime import AgentRuntime
from air.allocation.allocator import Strategy
from air.assurance.probes import AssuranceEngine, EvaluatorVerdict, SystemVerdict
from air.capabilities.models import CapabilityEffect
from air.capabilities.pipeline import CapabilityPipeline, GateBlocked
from air.config import AirConfig
from air.evaluation.suites import EvalCase, EvalSuite, Evaluator
from air.persistence.db import Database, find_migrations_dir


def _env(tmp_path):
    config = AirConfig(data_dir=tmp_path)
    db = Database(tmp_path / "air.db")
    db.migrate(find_migrations_dir())
    rt = AgentRuntime(config, db)

    async def emit(type, capability_id=None, payload=None):
        await rt.emit(type, payload={"capability_id": capability_id,
                                     **(payload or {})})

    return rt, db, CapabilityPipeline(db.conn, emit=emit)


async def _liar(agent: Agent, rt: AgentRuntime) -> dict:
    # Claims success, emits NO tool evidence. Classic self-report spoof.
    return {"success": True, "output": "all tests pass",
            "tokens": 10, "cost_usd": 0.0}


def test_spoofed_evaluator_blocked_from_promotion(tmp_path):
    async def main():
        rt, db, pipeline = _env(tmp_path)
        rt.register_behavior("specialist", _liar)

        run_id = await rt.create_run("do the task",
                                     strategy=Strategy.SINGLE_AGENT)
        await rt.start_run(run_id)
        for _ in range(200):
            row = rt.db.conn.execute("SELECT status FROM runs WHERE id=?",
                                     (run_id,)).fetchone()
            if row[0] == "COMPLETED":
                break
            await asyncio.sleep(0.05)

        # Weak suite: only checks an agent completed, no evidence required.
        weak = EvalSuite(name="weak-suite", version="0.0.1", cases=[
            EvalCase(id="w1", name="agent completed", check="agents_completed",
                     params={"min_completed": 1}),
        ])
        evaluator = Evaluator(db.conn, name="naive-evaluator",
                              version="0.0.1")
        evaluator.save_suite(weak)
        result = evaluator.evaluate_run(run_id, weak)
        assert result.verdict.value == "SUPPORTED", \
            "weak suite should (wrongly) accept the liar"

        # Assurance attacks the evaluator.
        ar = AssuranceEngine(db.conn).assure(result.id)
        assert ar.false_accepts > 0, \
            f"expected false accepts, got {[p.model_dump() for p in ar.probes]}"
        assert ar.evaluator_verdict == EvaluatorVerdict.EXPLOITABLE
        assert ar.system_verdict == SystemVerdict.INVALID

        # The spoofed evaluation must not promote a capability.
        cap = await pipeline.propose(
            "liar-cap", "learned from spoofed evidence",
            effect=CapabilityEffect(type="strategy_boost",
                                    strategy="debate", value=1.0))
        ev_id, verdict = await pipeline.validate(
            cap.capability_id, [run_id], suite=weak)
        assert verdict.value == "SUPPORTED"  # weak suite still says yes
        ar2 = AssuranceEngine(db.conn).assure(ev_id)
        assert ar2.evaluator_verdict == EvaluatorVerdict.EXPLOITABLE
        ok, reasons = pipeline.promotion_gate(cap.capability_id)
        assert not ok
        assert any("not trustworthy" in r for r in reasons), reasons
        try:
            await pipeline.promote(cap.capability_id)
            promoted = True
        except GateBlocked:
            promoted = False
        assert not promoted, "exploitable evaluator must block promotion"

    asyncio.run(main())


def test_sound_evaluator_passes_assurance(tmp_path):
    """Sanity: a strong suite (evidence required) is SOUND on honest runs."""
    async def main():
        rt, db, _ = _env(tmp_path)

        async def honest(agent: Agent, rt: AgentRuntime) -> dict:
            await rt.emit("tool.completed", run_id=agent.root_run_id,
                          agent_id=agent.id, payload={"tool": "verify"})
            return {"tokens": 10, "cost_usd": 0.0}

        rt.register_behavior("specialist", honest)
        run_id = await rt.create_run("do the task",
                                     strategy=Strategy.SINGLE_AGENT)
        await rt.start_run(run_id)
        for _ in range(200):
            row = rt.db.conn.execute("SELECT status FROM runs WHERE id=?",
                                     (run_id,)).fetchone()
            if row[0] == "COMPLETED":
                break
            await asyncio.sleep(0.05)

        strong = EvalSuite(name="strong-suite", cases=[
            EvalCase(id="s1", name="evidence", check="event_evidence",
                     params={"required": ["tool.completed"]}),
            EvalCase(id="s2", name="no failures", check="no_failures",
                     params={}),
        ])
        evaluator = Evaluator(db.conn)
        evaluator.save_suite(strong)
        result = evaluator.evaluate_run(run_id, strong)
        assert result.verdict.value == "SUPPORTED"
        ar = AssuranceEngine(db.conn).assure(result.id)
        assert ar.evaluator_verdict == EvaluatorVerdict.SOUND
        assert ar.system_verdict == SystemVerdict.SUPPORTED

    asyncio.run(main())
