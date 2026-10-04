"""The positive chain that closes the policy-evolution loop:

    genuinely better candidate -> evaluation SUPPORTED -> assurance SOUND
    -> promotion -> subsequent run actually consumes v2
    -> rollback works when v2 later regresses.

The exploit test proves the negative boundary; this proves the loop can
complete honestly end to end.
"""

import asyncio
import json
from pathlib import Path

from air.assurance.probes import AssuranceEngine
from air.config import AirConfig
from air.evaluation.suites import Verdict
from air.learning.engine import POLICY_NAME
from air.learning.policies import PolicyStore
from air.learning.policy_eval import evaluate_policy_candidate
from air.persistence.db import Database, find_migrations_dir


def _env(tmp_path):
    config = AirConfig(data_dir=tmp_path)
    db = Database(tmp_path / "air.db")
    db.migrate(find_migrations_dir())
    return db


def _seed_runs(db, policy_tag: str, specs: list[tuple[bool, bool]],
               suffix: str = ""):
    """specs: (completed, verified) per run."""
    for i, (completed, verified) in enumerate(specs):
        run_id = (f"run_{policy_tag.replace('@', '_').replace('.', '_')}"
                  f"_{suffix}_{i}")
        status = "COMPLETED" if completed else "FAILED"
        db.conn.execute(
            """INSERT INTO runs (id, goal, status, strategy, cognitive_plan,
               policy_version, runtime_version, created_at, started_at,
               completed_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (run_id, "synthetic", status, "single_agent", "{}",
             policy_tag, "0.1.0", "2026-01-01T00:00:00+00:00",
             "2026-01-01T00:00:00+00:00", "2026-01-01T00:01:00+00:00"))
        db.conn.execute(
            """INSERT INTO experiences (id, run_id, goal, dimensions,
               outcomes, cost, latency_ms, created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (f"exp_{run_id}", run_id, "synthetic",
             json.dumps({"outcome": status, "verified": verified}),
             json.dumps({"status": status, "provenance": "OBSERVED"}),
             0.40, 60000, "2026-01-01T00:01:00+00:00"))
    db.conn.commit()


def test_positive_promotion_closes_the_loop(tmp_path):
    async def main():
        db = _env(tmp_path)
        store = PolicyStore(db.conn)
        store.ensure(POLICY_NAME, {"spawn_threshold": 0.05})

        # v1 baseline: 4 runs, 3 completed, 2 verified.
        _seed_runs(db, f"{POLICY_NAME}@v1",
                   [(True, True), (True, True), (True, False), (False, False)])

        # The learning engine proposes v2 from evidence.
        v2 = await store.propose(
            POLICY_NAME, {"spawn_threshold": 0.04},
            reason="lower spawn bar raised verified completion in analysis",
            hypothesis="A slightly lower spawn threshold lets verification"
                       " agents spawn more often, raising verified rate.",
            expected_effect={"verified_rate": "increase"},
            constraints={"min_verified_rate": "no regression vs baseline"},
            generated_by="air-learning-engine v1",
            source_experiences=["exp_a", "exp_b"])
        assert v2.version == "2"
        assert v2.parent_version == "1"
        assert v2.source_experiences == ["exp_a", "exp_b"]

        # v2 candidate runs: genuinely better on every dimension.
        _seed_runs(db, f"{POLICY_NAME}@v2",
                   [(True, True)] * 4)

        # Independent evaluation: SUPPORTED.
        ev_id, verdict, dims = evaluate_policy_candidate(
            db.conn, POLICY_NAME, "2")
        assert verdict == Verdict.SUPPORTED, dims
        assert dims["candidate"]["verified_rate"] == 1.0
        assert dims["baseline"]["verified_rate"] == 0.5

        # Independent assurance: the evaluator is SOUND.
        ar = AssuranceEngine(db.conn).assure(ev_id)
        assert ar.system_verdict.value == "SUPPORTED", \
            [p.model_dump() for p in ar.probes]

        # Promotion through the gate.
        v2 = store.get_version(store.ensure(POLICY_NAME), "2")
        v2.assurance = {"id": ar.id,
                        "evaluator_verdict": ar.evaluator_verdict.value,
                        "system_verdict": ar.system_verdict.value}
        store._persist_version(v2)
        db.conn.commit()
        promoted = await store.promote(POLICY_NAME, "2")
        assert promoted.version == "2"
        assert store.current(POLICY_NAME).version == "2"

        # A subsequent run actually consumes v2: the policy tag and the
        # effects flow into the new run's allocation context.
        from air.agents.runtime import AgentRuntime
        rt = AgentRuntime(AirConfig(data_dir=tmp_path), db)
        run_id = await rt.create_run("post-promotion run")
        tag = db.conn.execute(
            "SELECT policy_version FROM runs WHERE id=?",
            (run_id,)).fetchone()[0]
        assert tag == f"{POLICY_NAME}@v2", tag
        effects = store.current_effects(POLICY_NAME)
        assert any(e.get("source") == f"policy:{POLICY_NAME}@v2"
                   for e in effects), effects

        # v2 later regresses in production: auditable rollback to v1.
        _seed_runs(db, f"{POLICY_NAME}@v2",
                   [(True, False)] * 4 + [(False, False)] * 2,
                   suffix="regress")
        rb = await store.request_rollback(
            POLICY_NAME, reason="verified rate regressed under v2",
            evidence={"window": "2026-01-02"},
            requested_by="operator")
        back = await store.approve_rollback(rb["id"], approved_by="operator")
        assert back.version == "1"
        assert store.current(POLICY_NAME).version == "1"

        # The provenance chain tells the whole story.
        chain = store.provenance_chain(POLICY_NAME)
        assert chain["active_version"] == "1"
        by_ver = {c["version"]: c for c in chain["chain"]}
        assert by_ver["2"]["status"] == "DEPRECATED"
        assert by_ver["2"]["evaluation"]["verdict"] == "SUPPORTED"
        assert by_ver["2"]["assurance"]["system_verdict"] == "SUPPORTED"
        assert by_ver["2"]["hypothesis"].startswith("A slightly lower")

    asyncio.run(main())
