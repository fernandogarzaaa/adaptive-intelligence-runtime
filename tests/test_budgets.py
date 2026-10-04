"""Budget enforcement happens in code, not in prompts."""

import pytest

from air.agents.models import Budget
from air.agents.runtime import AgentRuntime, BudgetExhausted
from air.config import AirConfig
from air.persistence.db import Database, find_migrations_dir
from pathlib import Path


def _rt(tmp_path):
    config = AirConfig(data_dir=tmp_path)
    db = Database(tmp_path / "air.db")
    db.migrate(find_migrations_dir())
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


def test_concurrent_tool_calls_never_exceed_budget(tmp_path):
    """Invariant 6: concurrent execution cannot consume more budget than
    reserved. 50 parallel slow tool calls against a tool_call_budget of
    10: exactly 10 COMMIT, 40 FAIL on budget exhaustion, and
    consumed_tool_calls stays exactly 10. Wall time also proves handler
    execution is no longer serialized by the gateway lock (only the
    atomic reservation step holds it)."""
    import asyncio
    import time

    from air.security.policy import CapabilityClass
    from air.tools.gateway import ToolCallState
    from air.tools.registry import ToolDefinition

    rt = _rt(tmp_path)

    async def main():
        run_id = await rt.create_run("budget stress", tool_call_budget=10)

        async def _slow(args, ctx):
            await asyncio.sleep(0.3)
            return {"ok": True}

        rt.tool_gateway()._registry.register(
            ToolDefinition(name="test.slow",
                           description="slow test tool",
                           input_schema={"type": "object"},
                           capability=CapabilityClass.READ), _slow)
        agent = await rt.create_agent(run_id, "worker", "stress",
                                      granted=["READ"])
        start = time.monotonic()
        results = await asyncio.gather(*[
            rt.call_tool(agent.id, "test.slow", {}) for _ in range(50)
        ])
        elapsed = time.monotonic() - start
        committed = [r for r in results if r.state == "COMMITTED"]
        failed = [r for r in results if r.state == "FAILED"]
        assert len(committed) == 10, len(committed)
        assert len(failed) == 40, len(failed)
        assert all("budget exhausted" in (r.error or "") for r in failed), \
            [r.error for r in failed[:3]]
        budget = rt._run_budget(run_id)
        assert budget["consumed_tool_calls"] == 10, budget
        # Serialized execution would take >= 10 * 0.3s for the winners
        # alone. Concurrent execution finishes far sooner.
        assert elapsed < 2.5, elapsed

    asyncio.run(main())
