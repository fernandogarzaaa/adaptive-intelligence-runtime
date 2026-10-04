"""Budget enforcement happens in code, not in prompts."""

import pytest

from air.agents.models import Budget
from air.agents.runtime import AgentRuntime, BudgetExhausted
from air.config import AirConfig
from air.persistence.db import Database
from pathlib import Path


def _rt(tmp_path):
    config = AirConfig(data_dir=tmp_path)
    db = Database(tmp_path / "air.db")
    db.migrate(Path(__file__).resolve().parents[1] / "migrations")
    return AgentRuntime(config, db)


def test_agent_slot_exhaustion_blocks_spawn(tmp_path):
    import asyncio
    rt = _rt(tmp_path)

    async def main():
        run_id = await rt.create_run("test goal", agent_budget=2)
        parent = await rt.create_agent(run_id, "planner", "plan things")
        # Creating the parent consumed one of two slots; the spawn consumes
        # the last one.
        agent, decision = await rt.spawn_agent(parent.id, "do work", "researcher",
                                               uncertainty=0.9)
        assert decision.decision == "SPAWN"
        assert agent is not None
        # Second spawn must be denied by the agent budget.
        agent2, decision2 = await rt.spawn_agent(parent.id, "more work", "coder",
                                                 uncertainty=0.9)
        assert agent2 is None
        assert decision2.decision == "DENY"
        assert "budget" in decision2.reason.lower()

    asyncio.run(main())


def test_token_overconsumption_raises(tmp_path):
    rt = _rt(tmp_path)
    import asyncio

    async def main():
        run_id = await rt.create_run("test", token_budget=100)
        with pytest.raises(BudgetExhausted):
            rt._consume(run_id, tokens=10_000)

    asyncio.run(main())
