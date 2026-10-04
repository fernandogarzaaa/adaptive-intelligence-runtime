"""Multi-dimensional policy evaluation with guardrails.

A policy is never judged on one scalar. The candidate is compared against the
baseline policy version on success rate, VERIFIED rate, cost, and latency —
and guardrails can reject a candidate that improves raw success while
verified performance deteriorates.

Example that must be rejected:
    v1:        success 82%  verified 76%  cost $0.41
    candidate: success 84%  verified 71%  cost $0.39
The +2pp success improvement is insufficient: verified performance regressed.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone

from air.evaluation.suites import CHECKS, EvalCase, EvalSuite, Evaluator, Verdict


def _group_dimensions(conn, policy_version: str) -> dict | None:
    """Aggregate experience dimensions for runs under a policy version."""
    rows = conn.execute(
        """SELECT e.dimensions, e.outcomes, e.cost, e.latency_ms
           FROM experiences e JOIN runs r ON e.run_id = r.id
           WHERE r.policy_version = ?""", (policy_version,)).fetchall()
    if not rows:
        return None
    n = len(rows)
    successes = verified = 0
    cost = latency = 0.0
    for dims_json, outcomes_json, c, lat in rows:
        dims = json.loads(dims_json or "{}")
        if dims.get("outcome") == "COMPLETED":
            successes += 1
        if dims.get("verified"):
            verified += 1
        cost += c or 0.0
        latency += lat or 0
    return {
        "runs": n,
        "success_rate": round(successes / n, 3),
        "verified_rate": round(verified / n, 3),
        "avg_cost": round(cost / n, 4),
        "avg_latency_ms": round(latency / n),
    }


# ---------------------------------------------------------------------------
# Registered checks. They operate on experience={"baseline": {...},
# "candidate": {...}} — dimension dicts, not events.
# ---------------------------------------------------------------------------
def _check_verified_no_regression(events, experience, params):
    base = (experience or {}).get("baseline") or {}
    cand = (experience or {}).get("candidate") or {}
    tol = float(params.get("tolerance", 0.0))
    bv, cv = base.get("verified_rate", 0), cand.get("verified_rate", 0)
    if cv < bv - tol:
        return False, (f"verified rate regressed: candidate {cv:.3f} <"
                       f" baseline {bv:.3f} (tolerance {tol})")
    return True, f"verified rate held: {cv:.3f} vs baseline {bv:.3f}"


def _check_cost_ceiling(events, experience, params):
    base = (experience or {}).get("baseline") or {}
    cand = (experience or {}).get("candidate") or {}
    ratio = float(params.get("max_ratio", 1.25))
    bc, cc = base.get("avg_cost", 0), cand.get("avg_cost", 0)
    ceiling = bc * ratio
    if cc > ceiling:
        return False, (f"cost ceiling breached: candidate ${cc:.4f} >"
                       f" ${ceiling:.4f} (baseline ${bc:.4f} x {ratio})")
    return True, f"cost within ceiling: ${cc:.4f} <= ${ceiling:.4f}"


def _check_success_improved(events, experience, params):
    base = (experience or {}).get("baseline") or {}
    cand = (experience or {}).get("candidate") or {}
    bs, cs = base.get("success_rate", 0), cand.get("success_rate", 0)
    if cs <= bs:
        return False, f"success did not improve: {cs:.3f} vs {bs:.3f}"
    return True, f"success improved: {cs:.3f} vs {bs:.3f}"


CHECKS["policy_verified_no_regression"] = _check_verified_no_regression
CHECKS["policy_cost_ceiling"] = _check_cost_ceiling
CHECKS["policy_success_improved"] = _check_success_improved


def default_policy_suite() -> EvalSuite:
    return EvalSuite(name="policy-dimensions", version="1.0.0", cases=[
        EvalCase(id="p1", name="verified no regression",
                 check="policy_verified_no_regression",
                 params={"tolerance": 0.0}, weight=2.0),
        EvalCase(id="p2", name="cost ceiling", check="policy_cost_ceiling",
                 params={"max_ratio": 1.25}, weight=1.0),
        EvalCase(id="p3", name="success improved",
                 check="policy_success_improved", params={}, weight=1.0),
    ])


def evaluate_policy_candidate(conn, policy_name: str, candidate_version: str,
                              baseline_version: str | None = None,
                              suite: EvalSuite | None = None,
                              evaluator_name: str = "air-policy-evaluator",
                              min_runs: int = 2) -> tuple[str, Verdict, dict]:
    """Evaluate a policy candidate against the baseline on dimensions.

    Returns (evaluation_id, verdict, dimensions). The verdict feeds the
    promotion gate; the dimensions feed assurance probes.
    """
    from air.learning.policies import PolicyStore
    store = PolicyStore(conn)
    pid = store.ensure(policy_name)
    cand = store.get_version(pid, candidate_version)
    if cand is None:
        raise ValueError(f"candidate {candidate_version} not found")
    baseline_version = baseline_version or cand.parent_version
    if not baseline_version:
        raise ValueError("candidate has no parent baseline")
    baseline_tag = f"{policy_name}@v{baseline_version}"
    candidate_tag = f"{policy_name}@v{candidate_version}"

    base_dims = _group_dimensions(conn, baseline_tag)
    cand_dims = _group_dimensions(conn, candidate_tag)
    dimensions = {"baseline": base_dims, "candidate": cand_dims,
                  "baseline_tag": baseline_tag,
                  "candidate_tag": candidate_tag}
    suite = suite or default_policy_suite()
    evaluator = Evaluator(conn, name=evaluator_name, version="1.0.0")
    evaluator.save_suite(suite)

    if base_dims is None or cand_dims is None:
        verdict = Verdict.INCONCLUSIVE
        detail = "insufficient runs under baseline or candidate"
    elif base_dims["runs"] < min_runs or cand_dims["runs"] < min_runs:
        verdict = Verdict.INCONCLUSIVE
        detail = (f"need >= {min_runs} runs per side: baseline"
                  f" {base_dims['runs']}, candidate {cand_dims['runs']}")
    else:
        # Run the suite's checks over the dimension dicts.
        passed, total, failures = 0.0, 0.0, []
        for case in suite.cases:
            fn = CHECKS.get(case.check)
            if fn is None:
                total += case.weight
                failures.append(f"unknown check {case.check}")
                continue
            ok, msg = fn([], dimensions, case.params)
            total += case.weight
            if ok:
                passed += case.weight
            else:
                failures.append(msg)
        if failures:
            # Guardrails (weight >= 2) failing => FALSIFIED. Otherwise
            # inconclusive: the candidate neither proved nor disproved itself.
            guardrail_failed = any(
                c.weight >= 2.0 and not CHECKS[c.check]([], dimensions,
                                                       c.params)[0]
                for c in suite.cases if c.check in CHECKS)
            verdict = Verdict.FALSIFIED if guardrail_failed else Verdict.INCONCLUSIVE
        elif passed == total:
            verdict = Verdict.SUPPORTED
        else:
            verdict = Verdict.INCONCLUSIVE
        detail = "; ".join(failures) if failures else "all checks passed"

    ev_id = "eval_" + uuid.uuid4().hex[:12]
    now = datetime.now(timezone.utc).isoformat()
    subject = {"kind": "policy", "policy": policy_name,
               "version": candidate_version, "baseline": baseline_version,
               "generated_by": cand.generated_by,
               "dimensions": dimensions, "detail": detail}
    conn.execute(
        """INSERT INTO evaluation_runs (id, suite_id, subject, evaluator,
           evaluator_version, metrics, verdict, evidence, started_at,
           completed_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (ev_id, suite.id, json.dumps(subject), evaluator_name, "1.0.0",
         json.dumps(dimensions), verdict.value,
         json.dumps({"evidence_hash": hashlib.sha256(
             json.dumps(dimensions, sort_keys=True).encode()).hexdigest(),
             "detail": detail}),
         now, now))
    conn.commit()
    # Attach to the candidate version for the promotion gate.
    cand.evaluation = {"id": ev_id, "verdict": verdict.value,
                       "evaluator": evaluator_name}
    store._persist_version(cand)
    conn.commit()
    return ev_id, verdict, dimensions
