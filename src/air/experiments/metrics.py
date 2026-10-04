"""Post-hoc metrics computed from immutable ledger artifacts.

READ-ONLY by construction: every function here issues SELECT
queries only (asserted structurally by test_experiments.py: no
INSERT/UPDATE/DELETE string may appear in this file). Metrics take a
database path and a run_id; they never receive agent handles, and no
agent-reported score flows into any metric (contamination points 9
and 10).

Verdict buckets are explicit and exhaustive: SUPPORTED, FALSIFIED,
INCONCLUSIVE, INVALID, UNTESTED. Nothing is silently converted.
``verified_success`` is defined once, here: an evaluation verdict of
SUPPORTED. Assurance is reported alongside, never folded in
silently.
"""

from __future__ import annotations

import math
import sqlite3

# The five evaluation verdicts, bucketed explicitly. Anything else the
# database might contain is bucketed as UNKNOWN, never dropped.
VERDICTS = ("SUPPORTED", "FALSIFIED", "INCONCLUSIVE", "INVALID", "UNTESTED")

# Event types counted as unexpected agent/tool failures
# ("catastrophic/unsafe actions" in the report).
FAILURE_EVENT_TYPES = ("agent.failed", "tool.failed")


def _one(conn, sql, params=()):
    row = conn.execute(sql, params).fetchone()
    return row[0] if row else 0


def compute_run_metrics(db_path: str, run_id: str,
                        latency_s: float | None) -> dict:
    """Compute every per-run metric from the run's ledger.

    ``latency_s`` is harness-measured wall time (perf_counter around
    start_run -> run completion), passed in because the ledger does
    not record harness wall time. Everything else comes from SELECTs.
    """
    conn = sqlite3.connect(db_path)
    try:
        policy_version = _one(
            conn, "SELECT policy_version FROM runs WHERE id=?", (run_id,))
        status = _one(
            conn, "SELECT status FROM runs WHERE id=?", (run_id,))
        strategy = _one(
            conn, "SELECT strategy FROM runs WHERE id=?", (run_id,))
        seed = _one(conn, "SELECT seed FROM runs WHERE id=?", (run_id,))
        n_agents = _one(
            conn, "SELECT COUNT(*) FROM agents WHERE root_run_id=?",
            (run_id,))
        n_tool_completed = _one(
            conn, "SELECT COUNT(*) FROM events WHERE run_id=? "
                  "AND type='tool.completed'", (run_id,))
        n_tool_denied = _one(
            conn, "SELECT COUNT(*) FROM events WHERE run_id=? "
                  "AND type='tool.denied'", (run_id,))
        n_spawn_requested = _one(
            conn, "SELECT COUNT(*) FROM events WHERE run_id=? "
                  "AND type='spawn.requested'", (run_id,))
        n_spawn_approved = _one(
            conn, "SELECT COUNT(*) FROM events WHERE run_id=? "
                  "AND type='spawn.approved'", (run_id,))
        n_spawn_denied = _one(
            conn, "SELECT COUNT(*) FROM events WHERE run_id=? "
                  "AND type='spawn.denied'", (run_id,))
        n_failures = _one(
            conn, "SELECT COUNT(*) FROM events WHERE run_id=? AND type IN "
                  "('agent.failed','tool.failed')", (run_id,))
        n_policy_blocked = _one(
            conn, "SELECT COUNT(*) FROM events WHERE run_id=? "
                  "AND type='policy.blocked'", (run_id,))
        n_rollbacks = _one(
            conn, "SELECT COUNT(*) FROM events WHERE run_id=? AND type IN "
                  "('policy.rolled_back','capability.rolled_back')",
            (run_id,))
        cost = _one(
            conn, "SELECT cost FROM experiences WHERE run_id=? "
                  "ORDER BY created_at DESC LIMIT 1", (run_id,)) or 0.0
        tokens = _one(
            conn, "SELECT COALESCE(SUM(consumed_tokens),0) FROM budgets "
                  "WHERE run_id=?", (run_id,))
        tool_calls_consumed = _one(
            conn, "SELECT COALESCE(SUM(consumed_tool_calls),0) FROM budgets "
                  "WHERE run_id=?", (run_id,))

        ev = conn.execute(
            "SELECT id, verdict FROM evaluation_runs "
            "WHERE json_extract(subject,'$.run_id')=? "
            "ORDER BY rowid DESC LIMIT 1", (run_id,)).fetchone()
        verdict = ev[1] if ev else "UNTESTED"
        evaluation_id = ev[0] if ev else None

        assurance_evaluator = None
        assurance_system = None
        assurance_probe_ids: list[str] = []
        if evaluation_id:
            ar = conn.execute(
                "SELECT evaluator_verdict, system_verdict, probes "
                "FROM assurance_runs "
                "WHERE json_extract(target,'$.id')=? "
                "ORDER BY rowid DESC LIMIT 1",
                (evaluation_id,)).fetchone()
            if ar:
                assurance_evaluator, assurance_system = ar[0], ar[1]
                try:
                    import json as _json
                    assurance_probe_ids = sorted(
                        p.get("probe", "?")
                        for p in _json.loads(ar[2] or "[]"))
                except Exception:
                    assurance_probe_ids = []

        return {
            "run_id": run_id,
            "policy_version": policy_version,
            "run_status": status,
            "strategy": strategy,
            "seed": seed,
            "n_agents": n_agents,
            "n_tool_completed": n_tool_completed,
            "n_tool_denied": n_tool_denied,
            "n_spawn_requested": n_spawn_requested,
            "n_spawn_approved": n_spawn_approved,
            "n_spawn_denied": n_spawn_denied,
            "n_unexpected_failures": n_failures,
            "n_policy_blocked": n_policy_blocked,
            "n_rollbacks": n_rollbacks,
            "cost_usd": float(cost or 0.0),
            "tokens": int(tokens or 0),
            "tool_calls_consumed": int(tool_calls_consumed or 0),
            "latency_s": latency_s,
            "verdict": verdict if verdict in VERDICTS else "UNKNOWN",
            "evaluation_id": evaluation_id,
            "assurance_evaluator_verdict": assurance_evaluator,
            "assurance_system_verdict": assurance_system,
            "assurance_probe_ids": assurance_probe_ids,
            "verified_success": verdict == "SUPPORTED",
        }
    finally:
        conn.close()


def aggregate(per_run: list[dict]) -> dict:
    """Aggregate per-run metrics into condition-level statistics.

    Verdicts are bucketed explicitly; INCONCLUSIVE, INVALID, and
    UNTESTED are never converted to success or failure silently.
    """
    total = len(per_run)
    buckets = {v: 0 for v in VERDICTS} | {"UNKNOWN": 0}
    for r in per_run:
        v = r["verdict"] if r["verdict"] in VERDICTS else "UNKNOWN"
        buckets[v] += 1
    verified = buckets["SUPPORTED"]
    verified_and_sound = sum(
        1 for r in per_run
        if r["verified_success"]
        and r["assurance_system_verdict"] == "SUPPORTED")
    total_cost = sum(r["cost_usd"] for r in per_run)
    latencies = [r["latency_s"] for r in per_run
                 if r["latency_s"] is not None]
    total_latency = sum(latencies)
    total_agents = sum(r["n_agents"] for r in per_run)
    return {
        "n_runs": total,
        "verdict_buckets": buckets,
        "verified_successes": verified,
        "verified_success_rate": (verified / total) if total else 0.0,
        "invalid_inconclusive_rate": (
            (buckets["INVALID"] + buckets["INCONCLUSIVE"]
             + buckets["UNTESTED"]) / total) if total else 0.0,
        "verified_and_sound": verified_and_sound,
        "assurance_system_buckets": _bucket(per_run,
                                            "assurance_system_verdict"),
        "cost_per_verified_success": (
            total_cost / verified) if verified else None,
        "latency_per_verified_success": (
            total_latency / verified) if verified and latencies else None,
        "mean_latency_s": (total_latency / len(latencies)
                           ) if latencies else None,
        "total_cost_usd": total_cost,
        "total_tokens": sum(r["tokens"] for r in per_run),
        "mean_agents": (total_agents / total) if total else 0.0,
        "total_agents": total_agents,
        "spawn_efficiency": (verified / total_agents
                             ) if total_agents else 0.0,
        "spawn_requested": sum(r["n_spawn_requested"] for r in per_run),
        "spawn_approved": sum(r["n_spawn_approved"] for r in per_run),
        "spawn_denied": sum(r["n_spawn_denied"] for r in per_run),
        # No mid-run spawns are attempted in the shakedown (documented
        # limitation); unnecessary spawning is therefore 0 by
        # construction, not by measurement.
        "unnecessary_spawning": 0,
        "verification_failures": buckets["FALSIFIED"] + buckets["INVALID"],
        "evaluator_failures": buckets["UNTESTED"],
        "unexpected_failures": sum(
            r["n_unexpected_failures"] for r in per_run),
        "tool_denied": sum(r["n_tool_denied"] for r in per_run),
        "policy_blocked": sum(r["n_policy_blocked"] for r in per_run),
        "rollbacks": sum(r["n_rollbacks"] for r in per_run),
        "policy_changes": 0,  # learning off; asserted per run
        "capability_reuse": "n/a: no capabilities promoted in shakedown "
                            "(learning off)",
    }


def _bucket(per_run: list[dict], key: str) -> dict:
    out: dict[str, int] = {}
    for r in per_run:
        v = r.get(key) or "NONE"
        out[v] = out.get(v, 0) + 1
    return out


def fisher_exact(a_succ: int, a_fail: int,
                 b_succ: int, b_fail: int) -> float:
    """Two-sided Fisher's exact p-value for 2x2 success/failure tables.

    Implemented with math.comb (no scipy dependency). Tests the null
    that the two conditions have the same verified-success rate.
    """
    n1, n2 = a_succ + a_fail, b_succ + b_fail
    k = a_succ + b_succ
    n = n1 + n2
    if n == 0 or k == 0 or k == n:
        return 1.0

    def hyper(x: int) -> float:
        # P(X = x) for X ~ Hypergeometric(n, k, n1)
        return (math.comb(k, x) * math.comb(n - k, n1 - x)
                / math.comb(n, n1))

    p_obs = hyper(a_succ)
    # Two-sided: sum probabilities of tables no more likely than observed.
    p = 0.0
    for x in range(max(0, k - n2), min(k, n1) + 1):
        px = hyper(x)
        if px <= p_obs + 1e-12:
            p += px
    return min(1.0, p)
