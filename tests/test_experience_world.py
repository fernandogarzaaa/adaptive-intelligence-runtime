"""Experience + world state: derived from the event log, never self-report."""

import asyncio
from pathlib import Path

from air.agents.models import Agent
from air.agents.runtime import AgentRuntime
from air.allocation.allocator import Strategy
from air.config import AirConfig
from air.experience.provenance import Provenance
from air.experience.recorder import ExperienceRecorder
from air.persistence.db import Database
from air.world.state import WorldStateStore, reduce_events


def _rt(tmp_path):
    config = AirConfig(data_dir=tmp_path)
    db = Database(tmp_path / "air.db")
    db.migrate(Path(__file__).resolve().parents[1] / "migrations")
    return AgentRuntime(config, db)


async def _worker(agent: Agent, rt: AgentRuntime) -> dict:
    return {"done": True, "tokens": 100, "cost_usd": 0.0}


def test_experience_recorded_on_run_completion(tmp_path):
    async def main():
        rt = _rt(tmp_path)
        rt.register_behavior("specialist", _worker)
        run_id = await rt.create_run("do a thing", strategy=Strategy.SINGLE_AGENT)
        await rt.start_run(run_id)
        for _ in range(200):
            row = rt.db.conn.execute("SELECT status FROM runs WHERE id=?",
                                     (run_id,)).fetchone()
            if row[0] == "COMPLETED":
                break
            await asyncio.sleep(0.05)
        # Experience should have been recorded automatically.
        exps = ExperienceRecorder(rt.db.conn).list()
        assert len(exps) == 1
        exp = ExperienceRecorder(rt.db.conn).get(exps[0]["id"])
        assert exp["run_id"] == run_id
        assert exp["outcomes"]["status"] == "COMPLETED"
        assert exp["outcomes"]["provenance"] == Provenance.OBSERVED.value
        assert len(exp["verification"]) > 0
        # Events recorded the completion too.
        types = {e["type"] for e in rt.store.list(run_id=run_id)}
        assert "experience.created" in types

    asyncio.run(main())


def test_world_state_reducer():
    events = [
        {"type": "run.created", "agent_id": None,
         "payload": {"goal": "g", "strategy": "parallel_agents"},
         "timestamp": "t0"},
        {"type": "agent.created", "agent_id": "a1",
         "payload": {"role": "researcher", "parent_id": None, "generation": 0},
         "timestamp": "t1"},
        {"type": "agent.created", "agent_id": "a2",
         "payload": {"role": "researcher", "parent_id": None, "generation": 0},
         "timestamp": "t2"},
        {"type": "spawn.requested", "agent_id": "a1",
         "payload": {"role": "verifier"}, "timestamp": "t3"},
        {"type": "spawn.denied", "agent_id": "a1",
         "payload": {"role": "verifier", "reason": "cost"}, "timestamp": "t4"},
        {"type": "agent.completed", "agent_id": "a1",
         "payload": {}, "timestamp": "t5"},
    ]
    state = reduce_events(events)
    assert state["agent_count"] == 2
    assert state["active_agents"] == 1
    assert state["spawns"] == {"requested": 1, "approved": 0, "denied": 1}
    assert len(state["pending_decisions"]) == 1
    assert state["pending_decisions"][0]["kind"] == "spawn_denied"


def test_world_state_snapshot_roundtrip(tmp_path):
    async def main():
        rt = _rt(tmp_path)
        rt.register_behavior("specialist", _worker)
        run_id = await rt.create_run("do a thing", strategy=Strategy.SINGLE_AGENT)
        await rt.start_run(run_id)
        for _ in range(200):
            row = rt.db.conn.execute("SELECT status FROM runs WHERE id=?",
                                     (run_id,)).fetchone()
            if row[0] == "COMPLETED":
                break
            await asyncio.sleep(0.05)
        ws = WorldStateStore(rt.db.conn, rt.store)
        snap = ws.snapshot(run_id)
        assert snap["version"] == 1
        assert snap["state"]["run"]["status"] == "COMPLETED"
        assert snap["state"]["agent_count"] == 1
        latest = ws.latest(run_id)
        assert latest["version"] == 1

    asyncio.run(main())
