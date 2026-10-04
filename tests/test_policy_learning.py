"""Policy evolution: versioned, gated, rollback-able. The learning engine
proposes from evidence; it never promotes."""

import asyncio
from pathlib import Path

import pytest

from air.agents.models import Agent
from air.agents.runtime import AgentRuntime
from air.allocation.allocator import Strategy
from air.config import AirConfig
from air.learning.engine import POLICY_NAME, LearningEngine
from air.learning.policies import GateBlocked, PolicyStore
from air.persistence.db import Database


def _env(tmp_path):
    config = AirConfig(data_dir=tmp_path)
    db = Database(tmp_path / "air.db")
    db.migrate(Path(__file__).resolve().parents[1] / "migrations")
    rt = AgentRuntime(config, db)

    async def emit(type, payload=None):
        await rt.emit(type, payload=payload or {})

    return rt, db, PolicyStore(db.conn, emit=emit)


def test_policy_lifecycle_gated_and_rollback(tmp_path):
    async def main():
        rt, db, store = _env(tmp_path)
        pid = store.ensure(POLICY_NAME, {"spawn_threshold": 0.05,
                                         "strategy_boosts": {}})
        assert pid.startswith("pol_")
        cur = store.current(POLICY_NAME)
        assert cur.version == "1" and cur.status == "PROMOTED"

        # Propose v2.
        v2 = await store.propose(POLICY_NAME, {"spawn_threshold": 0.2,
                                               "strategy_boosts": {}},
                                 reason="test change",
                                 evidence={"note": "test"})
        assert v2.version == "2" and v2.status == "CANDIDATE"
        assert v2.parent_version == "1"

        # Gate blocks promotion without evaluation + assurance.
        with pytest.raises(GateBlocked):
            await store.promote(POLICY_NAME, "2")

        # Promote with independent evidence attached.
        v2 = await store.promote(
            POLICY_NAME, "2",
            evaluation={"verdict": "SUPPORTED", "id": "eval_1"},
            assurance={"evaluator_verdict": "SOUND",
                       "system_verdict": "SUPPORTED", "id": "asr_1"})
        assert v2.status == "PROMOTED"
        assert store.current(POLICY_NAME).version == "2"

        # The new policy changes future allocation for real.
        run_id = await rt.create_run("audit and verify", agent_budget=2)
        effects = rt._run_configs[run_id]["capability_effects"]
        thresh = [e for e in effects if e["type"] == "spawn_threshold"]
        assert thresh and thresh[0]["value"] == 0.2
        assert thresh[0]["source"] == f"policy:{POLICY_NAME}@v2"

        # Rollback restores v1 immediately.
        back = await store.rollback(POLICY_NAME)
        assert back.version == "1"
        run2 = await rt.create_run("audit and verify", agent_budget=2)
        effects2 = rt._run_configs[run2]["capability_effects"]
        thresh2 = [e for e in effects2 if e["type"] == "spawn_threshold"]
        assert not thresh2 or thresh2[0]["value"] == 0.05

    asyncio.run(main())


async def _worker(agent: Agent, rt: AgentRuntime) -> dict:
    await rt.emit("tool.completed", run_id=agent.root_run_id,
                  agent_id=agent.id, payload={"tool": "t"})
    return {"tokens": 10, "cost_usd": 0.0}


async def _run(rt, strategy, budget=4):
    run_id = await rt.create_run("do work", strategy=strategy,
                                 agent_budget=budget)
    await rt.start_run(run_id)
    for _ in range(300):
        row = rt.db.conn.execute("SELECT status FROM runs WHERE id=?",
                                 (run_id,)).fetchone()
        if row[0] == "COMPLETED":
            break
        await asyncio.sleep(0.05)
    return run_id


def test_learning_engine_proposes_from_evidence(tmp_path):
    async def main():
        rt, db, _ = _env(tmp_path)
        for role in ("specialist", "researcher", "synthesizer", "verifier",
                     "critic", "planner"):
            rt.register_behavior(role, _worker)
        # 3 successful runs across strategies -> enough evidence.
        await _run(rt, Strategy.SINGLE_AGENT, budget=2)
        await _run(rt, Strategy.PARALLEL_AGENTS, budget=4)
        await _run(rt, Strategy.EXECUTE_THEN_VERIFY, budget=3)

        engine = LearningEngine(db.conn)
        analysis = engine.analyze()
        assert analysis["experiences"] == 3
        assert "single_agent" in analysis["strategies"]
        assert analysis["strategies"]["single_agent"]["success_rate"] == 1.0

        # All succeeded -> positive boosts proposed, never promoted.
        proposal = await engine.propose_policy_update()
        assert proposal is not None
        assert proposal["policy"] == POLICY_NAME
        store = PolicyStore(db.conn)
        cur = store.current(POLICY_NAME)
        # Still v1: proposal does not promote.
        assert cur.version == "1"
        v2 = store.get_version(store.ensure(POLICY_NAME),
                               proposal["version"])
        assert v2.status == "CANDIDATE"
        assert v2.evidence["analysis"]["experiences"] == 3

    asyncio.run(main())


def test_learning_engine_insufficient_evidence(tmp_path):
    async def main():
        rt, db, _ = _env(tmp_path)
        rt.register_behavior("specialist", _worker)
        await _run(rt, Strategy.SINGLE_AGENT, budget=2)
        engine = LearningEngine(db.conn)
        assert engine.analyze()["experiences"] == 1
        assert await engine.propose_policy_update() is None

    asyncio.run(main())
