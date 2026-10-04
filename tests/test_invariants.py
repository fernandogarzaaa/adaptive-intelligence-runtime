"""Invariant / adversarial tests: prove AIR cannot accidentally promote,
mutate, bypass budget, cross namespaces, or corrupt lineage under hostile
conditions. The milestone is not test count; it is unbreakable invariants."""

import asyncio
import json
from pathlib import Path

import pytest

from air.agents.runtime import AgentRuntime
from air.allocation.allocator import Strategy
from air.config import AirConfig
from air.learning.engine import POLICY_NAME
from air.learning.policies import PolicyStore
from air.memory.store import MemoryStore, MemoryType, Scope
from air.persistence.db import Database, find_migrations_dir


def _env(tmp_path):
    config = AirConfig(data_dir=tmp_path)
    db = Database(tmp_path / "air.db")
    db.migrate(find_migrations_dir())
    rt = AgentRuntime(config, db)

    async def emit(type, payload=None):
        await rt.emit(type, payload=payload or {})

    return rt, db, PolicyStore(db.conn, emit=emit)


def test_sql_promotion_bypass_fails(tmp_path):
    """An attacker (or bug) flips the version row to PROMOTED directly.
    The active policy must not change: only promote() moves current_version."""
    async def main():
        _, db, store = _env(tmp_path)
        store.ensure(POLICY_NAME, {"spawn_threshold": 0.05})
        v2 = await store.propose(POLICY_NAME, {"spawn_threshold": 0.9},
                                 reason="hostile")
        # Hostile direct mutation, bypassing the gate.
        db.conn.execute(
            "UPDATE policy_versions SET status='PROMOTED' WHERE id=?",
            (v2.id,))
        db.conn.commit()
        # Invariants hold:
        assert store.current(POLICY_NAME).version == "1"
        effects = store.current_effects(POLICY_NAME)
        assert not any(e.get("value") == 0.9 for e in effects), \
            "bypassed promotion must not affect allocation"
        # And the gate still refuses the real path without evidence.
        from air.learning.policies import GateBlocked
        with pytest.raises(GateBlocked):
            await store.promote(POLICY_NAME, "2")

    asyncio.run(main())


def test_propose_never_mutates_active_policy(tmp_path):
    async def main():
        _, _, store = _env(tmp_path)
        store.ensure(POLICY_NAME, {"spawn_threshold": 0.05})
        before = store.current(POLICY_NAME).version
        for i in range(3):
            await store.propose(POLICY_NAME, {"spawn_threshold": 0.1 + i},
                                reason=f"c{i}")
        assert store.current(POLICY_NAME).version == before
        assert len(store.history(POLICY_NAME)) == 4  # v1 + 3 candidates

    asyncio.run(main())


def test_budget_cannot_be_bypassed_via_create_agent(tmp_path):
    """spawn_agent is not the only path: create_agent enforces the budget too."""
    async def main():
        rt, db, _ = _env(tmp_path)
        run_id = await rt.create_run("t", strategy=Strategy.SINGLE_AGENT,
                                     agent_budget=1)
        parent = await rt.create_agent(run_id, "planner", "p")
        # Budget is now exhausted (1/1). Even the spawn policy path denies.
        child, decision = await rt.spawn_agent(parent.id, "x", "researcher",
                                               uncertainty=0.99)
        assert child is None and decision.decision == "DENY"
        # And direct creation raises instead of silently exceeding.
        from air.agents.runtime import BudgetExhausted
        with pytest.raises(BudgetExhausted):
            await rt.create_agent(run_id, "researcher", "y")

    asyncio.run(main())


def test_rollback_produces_auditable_trail(tmp_path):
    async def main():
        rt, db, store = _env(tmp_path)
        store.ensure(POLICY_NAME, {"spawn_threshold": 0.05})
        await store.propose(POLICY_NAME, {"spawn_threshold": 0.2},
                            reason="test")
        await store.promote(
            POLICY_NAME, "2",
            evaluation={"verdict": "SUPPORTED", "id": "e1"},
            assurance={"evaluator_verdict": "SOUND",
                       "system_verdict": "SUPPORTED", "id": "a1"})
        # A run under v2 exists (affected-runs computation needs it).
        run_id = await rt.create_run("t", agent_budget=1)

        rb = await store.request_rollback(
            POLICY_NAME, reason="verified regression observed",
            evidence={"eval": "e9"}, requested_by="operator")
        assert rb["status"] == "REQUESTED"
        assert rb["from_version"] == "2" and rb["to_version"] == "1"
        # Request alone changes nothing.
        assert store.current(POLICY_NAME).version == "2"

        back = await store.approve_rollback(rb["id"], approved_by="operator")
        assert back.version == "1"
        assert store.current(POLICY_NAME).version == "1"

        # The rollback record is complete.
        row = db.conn.execute(
            "SELECT from_version, to_version, reason, requested_by,"
            " approved_by, status, affected_runs FROM policy_rollbacks"
            " WHERE id=?", (rb["id"],)).fetchone()
        assert row[2] == "verified regression observed"
        assert row[5] == "APPROVED"
        assert run_id in json.loads(row[6]), "affected run must be listed"

        # Events tell the story.
        types = [e["type"] for e in rt.store.list(limit=1000)]
        assert "policy.rollback_requested" in types
        assert "policy.rollback_approved" in types
        assert "policy.activated" in types

        # Provenance chain answers "why is this policy active?".
        chain = store.provenance_chain(POLICY_NAME)
        assert chain["active_version"] == "1"
        by_ver = {c["version"]: c for c in chain["chain"]}
        assert by_ver["1"]["status"] == "PROMOTED"
        assert by_ver["2"]["status"] == "DEPRECATED"
        assert by_ver["2"]["parent_version"] == "1"
        assert by_ver["2"]["evaluation"]["verdict"] == "SUPPORTED"
        # The rollback events complete the story.
        assert any("rollback" in t for t in types)

    asyncio.run(main())


def test_stale_rollback_rejected(tmp_path):
    """Approving a rollback after the active version moved on is rejected."""
    async def main():
        from air.learning.policies import GateBlocked
        _, _, store = _env(tmp_path)
        store.ensure(POLICY_NAME, {"spawn_threshold": 0.05})
        await store.propose(POLICY_NAME, {"spawn_threshold": 0.2},
                            reason="t")
        await store.promote(
            POLICY_NAME, "2",
            evaluation={"verdict": "SUPPORTED"},
            assurance={"evaluator_verdict": "SOUND",
                       "system_verdict": "SUPPORTED"})
        rb = await store.request_rollback(POLICY_NAME, reason="t",
                                          evidence={},
                                          requested_by="operator")
        # The world moves on: v3 is evaluated, assured, promoted.
        await store.propose(POLICY_NAME, {"spawn_threshold": 0.3},
                            reason="t")
        await store.promote(
            POLICY_NAME, "3",
            evaluation={"verdict": "SUPPORTED"},
            assurance={"evaluator_verdict": "SOUND",
                       "system_verdict": "SUPPORTED"})
        # The v2->v1 rollback is now stale.
        with pytest.raises(GateBlocked):
            await store.approve_rollback(rb["id"], approved_by="operator")
        assert store.current(POLICY_NAME).version == "3"

    asyncio.run(main())


def test_memory_namespace_isolation_under_adversary(tmp_path):
    """A reader guessing another namespace gets nothing; scopes are enforced
    even when the caller asks for everything."""
    _, db, _ = _env(tmp_path)
    store = MemoryStore(db.conn)
    store.store("run_secret", MemoryType.EPISODIC, {"k": "v"},
                scope=Scope.TASK)
    # Adversarial enumeration attempts:
    assert store.retrieve("run_other", scopes=("global", "task", "agent",
                                               "run")) == []
    assert store.retrieve("run_secret", scopes=("global",)) == []
    assert store.retrieve("run_secret", scopes=("task", "global")) != []
