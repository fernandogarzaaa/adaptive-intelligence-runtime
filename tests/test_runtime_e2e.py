"""End-to-end: a full run with scripted agents executes for real."""

import asyncio
from pathlib import Path

from air.agents.models import Agent
from air.agents.runtime import AgentRuntime
from air.allocation.allocator import Strategy
from air.config import AirConfig
from air.persistence.db import Database, find_migrations_dir


def _rt(tmp_path) -> AgentRuntime:
    config = AirConfig(data_dir=tmp_path)
    db = Database(tmp_path / "air.db")
    db.migrate(find_migrations_dir())
    return AgentRuntime(config, db)


async def _researcher(agent: Agent, rt: AgentRuntime) -> dict:
    await rt.emit("tool.completed", run_id=agent.root_run_id, agent_id=agent.id,
                  payload={"tool": "research_web", "note": "scripted research"})
    return {"findings": [f"finding from {agent.id}"], "tokens": 500, "cost_usd": 0.0}


async def _synthesizer(agent: Agent, rt: AgentRuntime) -> dict:
    return {"answer": "synthesized", "tokens": 300, "cost_usd": 0.0}


def test_full_run_parallel_strategy(tmp_path):
    async def main():
        rt = _rt(tmp_path)
        rt.register_behavior("researcher", _researcher)
        rt.register_behavior("synthesizer", _synthesizer)
        run_id = await rt.create_run(
            "Independently research each of these three topics and compare them",
            strategy=Strategy.PARALLEL_AGENTS, agent_budget=4)
        await rt.start_run(run_id)
        # Wait for completion (scripted behaviors finish fast).
        for _ in range(200):
            row = rt.db.conn.execute("SELECT status FROM runs WHERE id=?",
                                     (run_id,)).fetchone()
            if row[0] == "COMPLETED":
                break
            await asyncio.sleep(0.05)
        assert row[0] == "COMPLETED", "run did not complete"
        agents = rt.db.conn.execute(
            "SELECT COUNT(*) FROM agents WHERE root_run_id=?", (run_id,)).fetchone()[0]
        assert agents == 4, f"expected 4 agents (3 researchers + synthesizer), got {agents}"
        completed = rt.db.conn.execute(
            "SELECT COUNT(*) FROM agents WHERE root_run_id=? AND status='COMPLETED'",
            (run_id,)).fetchone()[0]
        assert completed == 4
        events = rt.store.list(run_id=run_id)
        types = {e["type"] for e in events}
        assert {"run.created", "run.started", "agent.created", "agent.started",
                "agent.completed", "run.completed"} <= types
        ok, bad = rt.store.verify_chain()
        assert ok, f"event chain broken at {bad}"

    asyncio.run(main())


def test_spawn_and_message_roundtrip(tmp_path):
    async def main():
        rt = _rt(tmp_path)
        run_id = await rt.create_run("test", strategy=Strategy.SINGLE_AGENT)
        parent = await rt.create_agent(run_id, "planner", "plan")
        child, decision = await rt.spawn_agent(
            parent.id, "research things", "researcher", uncertainty=0.9,
            reason_hint="test spawn")
        assert decision.decision == "SPAWN"
        assert child is not None and child.parent_id == parent.id
        assert child.generation == 1
        msg_id = await rt.send_message(run_id, parent.id, child.id,
                                       "parent_child", "message",
                                       {"text": "begin"})
        assert msg_id.startswith("msg_")
        # Out-of-scope message across runs is denied.
        run2 = await rt.create_run("other", strategy=Strategy.SINGLE_AGENT)
        outsider = await rt.create_agent(run2, "planner", "other plan")
        try:
            await rt.send_message(run_id, parent.id, outsider.id,
                                  "parent_child", "message", {"text": "x"})
            denied = False
        except PermissionError:
            denied = True
        assert denied, "cross-run message must be denied"
        # Terminate subtree.
        terminated = await rt.terminate_agent(parent.id, subtree=True)
        assert set(terminated) == {parent.id, child.id}

    asyncio.run(main())


def test_no_provider_no_behavior_blocks_honestly(tmp_path):
    async def main():
        rt = _rt(tmp_path)
        # No behaviors registered and no provider creds: agent must BLOCK
        # with MODEL_PROVIDER_UNAVAILABLE, never fake output. The run stays
        # open so the operator can configure a provider and resume.
        run_id = await rt.create_run("do something", strategy=Strategy.SINGLE_AGENT)
        await rt.start_run(run_id)
        for _ in range(200):
            rows = rt.db.conn.execute(
                "SELECT status, status_reason FROM agents WHERE root_run_id=?",
                (run_id,)).fetchall()
            if rows and rows[0][0] in ("BLOCKED", "FAILED", "COMPLETED"):
                break
            await asyncio.sleep(0.05)
        assert rows[0][0] == "BLOCKED"
        assert "MODEL_PROVIDER_UNAVAILABLE" in (rows[0][1] or "")
        types = {e["type"] for e in rt.store.list(run_id=run_id)}
        assert "agent.blocked" in types

    asyncio.run(main())
