"""Regression guardrail: a policy must not need to be catastrophically bad
to be rejected.

    v1:        success 82%   verified 76%   cost $0.41
    candidate: success 84%   verified 71%   cost $0.39

Raw success improved, but verified performance deteriorated. The candidate
must be FALSIFIED and promotion blocked — policy evaluation is
multi-dimensional, not one scalar reward.
"""

import asyncio
import json
from pathlib import Path

from air.config import AirConfig
from air.evaluation.suites import Verdict
from air.learning.policies import GateBlocked, PolicyStore
from air.learning.policy_eval import evaluate_policy_candidate
from air.persistence.db import Database


def _env(tmp_path):
    config = AirConfig(data_dir=tmp_path)
    db = Database(tmp_path / "air.db")
    db.migrate(Path(__file__).resolve().parents[1] / "migrations")
    return PolicyStore(db.conn), db


def _seed_runs(db, policy_tag: str, verified_outcomes: list[bool],
               failed: int = 0):
    """Synthetic but structurally real runs+experiences under a policy tag."""
    specs = ([(True, v) for v in verified_outcomes]
             + [(False, False)] * failed)
    for i, (completed, verified) in enumerate(specs):
        run_id = f"run_{policy_tag.replace('@', '_').replace('.', '_')}_{i}"
        status = "COMPLETED" if completed else "FAILED"
        db.conn.execute(
            """INSERT INTO runs (id, goal, status, strategy, cognitive_plan,
               policy_version, runtime_version, created_at, started_at,
               completed_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (run_id, "synthetic task", status, "single_agent", "{}",
             policy_tag, "0.1.0", "2026-01-01T00:00:00+00:00",
             "2026-01-01T00:00:00+00:00", "2026-01-01T00:01:00+00:00"))
        dims = {"outcome": status, "verified": verified,
                "cognitive_strategy": "single_agent"}
        db.conn.execute(
            """INSERT INTO experiences (id, run_id, goal, dimensions, outcomes,
               cost, latency_ms, created_at) VALUES (?,?,?,?,?,?,?,?)""",
            (f"exp_{run_id}", run_id, "synthetic task",
             json.dumps(dims),
             json.dumps({"status": status, "provenance": "OBSERVED"}),
             0.41 if "v1" in policy_tag else 0.39, 60000,
             "2026-01-01T00:01:00+00:00"))
    db.conn.commit()


def test_verified_regression_rejects_candidate(tmp_path):
    async def main():
        store, db = _env(tmp_path)
        store.ensure("cognitive-allocation", {"spawn_threshold": 0.05})
        v2 = await store.propose(
            "cognitive-allocation", {"spawn_threshold": 0.02},
            reason="cheaper and slightly more successful",
            hypothesis="Lower spawn bar raises success without harm.",
            generated_by="air-learning-engine v1",
            source_experiences=["exp_a", "exp_b"])

        # v1: 4 runs, 3 verified (75%). Candidate: 4 runs, 2 verified (50%)
        # with higher raw success cost profile. (All COMPLETED: raw success
        # is 100% on both sides; the verified dimension is what regresses.)
        _seed_runs(db, "cognitive-allocation@v1",
                   [True, True, True, False])
        _seed_runs(db, "cognitive-allocation@v2",
                   [True, True, False, False])

        ev_id, verdict, dims = evaluate_policy_candidate(
            db.conn, "cognitive-allocation", "2")
        assert dims["baseline"]["verified_rate"] == 0.75
        assert dims["candidate"]["verified_rate"] == 0.5
        assert verdict == Verdict.FALSIFIED, \
            f"verified regression must falsify, got {verdict}: {dims}"

        # Promotion blocked: the gate sees FALSIFIED.
        v2 = store.get_version(store.ensure("cognitive-allocation"), "2")
        ok, reasons = store._gate(v2)
        assert not ok
        try:
            await store.promote("cognitive-allocation", "2")
            promoted = True
        except GateBlocked:
            promoted = False
        assert not promoted
        assert store.current("cognitive-allocation").version == "1"

    asyncio.run(main())


def test_genuine_improvement_supported(tmp_path):
    """Control: improvement on all dimensions is SUPPORTED."""
    async def main():
        store, db = _env(tmp_path)
        store.ensure("cognitive-allocation", {"spawn_threshold": 0.05})
        await store.propose("cognitive-allocation", {"spawn_threshold": 0.04},
                            reason="test",
                            generated_by="air-learning-engine v1")
        _seed_runs(db, "cognitive-allocation@v1", [True, True, False, False],
                   failed=1)
        _seed_runs(db, "cognitive-allocation@v2", [True, True, True, True])
        ev_id, verdict, dims = evaluate_policy_candidate(
            db.conn, "cognitive-allocation", "2")
        assert verdict == Verdict.SUPPORTED, dims

    asyncio.run(main())
