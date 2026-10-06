"""CTC-1 pre-execution frozen-score audit (preregistered, Section 4).

Computes exact v1/pv2 strategy scores for every AB pair member using
the FROZEN allocator (allocation only, no task execution). Determines
each pair's PREDICTION_STATUS mechanically:

  DISCRIMINATING iff:
    A: (single_agent final > parallel_agents final) AND/OR
       (parallel_agents infeasible), AND
    B: (parallel_agents feasible) AND
       (parallel_agents final > single_agent final)
  NON_DISCRIMINATING otherwise.

Per the protocol, ALL 12 AB pairs MUST be DISCRIMINATING at freeze.
This script exits nonzero if any pair is non-discriminating, blocking
the freeze. Regeneration happens pre-freeze via the task generator.

Output: immutable prediction artifact with hash-link to task registry.
Must be generated BEFORE any CTC-1 execution occurs.

Usage:
    PYTHONPATH=src AIR_EXP_ID=dseries-v2-1 python -m air.experiments.audit_ctc_1
"""

import json
import hashlib
import os
import sys
from pathlib import Path

POOL_PATH = Path("src/air/experiments/tasks/v2/ctc_1.json")


def main():
    from air.experiments.v2harness import (
        decision_decomposition,
        _active_rules,
    )

    pool = json.loads(POOL_PATH.read_text())
    tasks = {t["id"]: t for t in pool["tasks"]}
    pool_hash = hashlib.sha256(POOL_PATH.read_bytes()).hexdigest()

    pv2_rules, pv2_version = _active_rules()
    assert pv2_version == "v2", f"expected frozen pv2 v2, got {pv2_version}"
    assert len(pv2_rules) == 12, f"expected 12 rules, got {len(pv2_rules)}"

    # Group AB pairs
    ab_pairs = {}
    for tid in tasks:
        if tid.startswith("ctc_ab"):
            pair_id = tid[:-1]  # ctc_ab01
            member = tid[-1]    # a or b
            ab_pairs.setdefault(pair_id, {})[member] = tid
    assert len(ab_pairs) == 12, f"expected 12 AB pairs, got {len(ab_pairs)}"
    for pid, m in ab_pairs.items():
        assert set(m) == {"a", "b"}, f"{pid}: members {m}"

    predictions = []
    for pair_id in sorted(ab_pairs):
        ta = tasks[ab_pairs[pair_id]["a"]]
        tb = tasks[ab_pairs[pair_id]["b"]]

        # Byte-identical text invariant (re-verify at audit time)
        assert ta["goal"] == tb["goal"], f"{pair_id}: goal text differs"
        assert ta["raw_text_hash"] == tb["raw_text_hash"]

        # Frozen-allocator decompositions (no execution)
        da = decision_decomposition(ta, pv2_rules)
        db = decision_decomposition(tb, pv2_rules)

        # v1 scores (empty rules = pure lexical)
        v1a = decision_decomposition(ta, [])
        v1b = decision_decomposition(tb, [])

        # Feasibility from pv2 decomposition
        fa = da["feasibility"]
        fb = db["feasibility"]
        par_feas_a = fa["parallel_agents"]["feasible"]
        par_feas_b = fb["parallel_agents"]["feasible"]
        sng_feas_a = fa["single_agent"]["feasible"]
        sng_feas_b = fb["single_agent"]["feasible"]

        # pv2 final scores
        pva = da["pv2_final_scores"]
        pvb = db["pv2_final_scores"]
        # v1 base scores
        v1sa = v1a["v1_base_scores"]
        v1sb = v1b["v1_base_scores"]

        # DISCRIMINATING criteria (mechanical, per protocol 4.2)
        a_single_gt_par = pva.get("single_agent", -1) > pva.get("parallel_agents", -1)
        a_par_infeasible = not par_feas_a
        a_ok = (a_single_gt_par or a_par_infeasible) and sng_feas_a

        b_par_feas = par_feas_b
        b_par_gt_single = pvb.get("parallel_agents", -1) > pvb.get("single_agent", -1)
        b_ok = b_par_feas and b_par_gt_single and sng_feas_b

        discriminating = a_ok and b_ok
        status = "DISCRIMINATING" if discriminating else "NON_DISCRIMINATING"

        # E1/E2 predictions (mechanical from frozen scores)
        # E1: A->single (infeasible or lower score), B->parallel (feasible + higher)
        # E2: A->single, B->single (no switch despite feasibility)
        e1_pred = {"A": "single_agent", "B": "parallel_agents"}
        e2_pred = {"A": "single_agent", "B": "single_agent"}

        predictions.append({
            "pair_id": pair_id,
            "task_hash_A": hashlib.sha256(
                json.dumps(ta, sort_keys=True).encode()).hexdigest(),
            "task_hash_B": hashlib.sha256(
                json.dumps(tb, sort_keys=True).encode()).hexdigest(),
            "task_text_hash": ta["raw_text_hash"],
            "topology_hash_A": ta["topology_hash"],
            "topology_hash_B": tb["topology_hash"],
            "v1_scores_A": v1sa,
            "v1_scores_B": v1sb,
            "pv2_scores_A": pva,
            "pv2_scores_B": pvb,
            "parallel_feasible_A": par_feas_a,
            "parallel_feasible_B": par_feas_b,
            "single_agent_feasible_A": sng_feas_a,
            "single_agent_feasible_B": sng_feas_b,
            "v1_choice_A": v1a["v1_choice"],
            "v1_choice_B": v1b["v1_choice"],
            "PREDICTION_STATUS": status,
            "E1_prediction": e1_pred,
            "E2_prediction": e2_pred,
            "_audit_detail": {
                "A_single_gt_parallel": a_single_gt_par,
                "A_parallel_infeasible": a_par_infeasible,
                "B_parallel_gt_single": b_par_gt_single,
            },
        })

        flag = "OK " if discriminating else "FAIL"
        print(f"[{flag}] {pair_id}: {status} | "
              f"A pv2 {da['pv2_choice']} (par_feas={par_feas_a}) | "
              f"B pv2 {db['pv2_choice']} (par_feas={par_feas_b}) | "
              f"B par={pvb.get('parallel_agents')} vs sng={pvb.get('single_agent')}")

    n_disc = sum(1 for p in predictions if p["PREDICTION_STATUS"] == "DISCRIMINATING")
    n_nondisc = 12 - n_disc
    print(f"\nDiscriminating: {n_disc}/12 | Non-discriminating: {n_nondisc}/12")

    artifact = {
        "artifact": "ctc-1-prediction",
        "version": 1,
        "task_pool_sha256": pool_hash,
        "task_pool_path": str(POOL_PATH),
        "pv2_version": pv2_version,
        "pv2_n_rules": len(pv2_rules),
        "generated_before_execution": True,
        "predictions": predictions,
        "summary": {
            "n_pairs": 12,
            "n_discriminating": n_disc,
            "n_non_discriminating": n_nondisc,
        },
    }
    # Hash-link: artifact hash covers the pool hash
    canon = json.dumps(artifact, sort_keys=True).encode()
    artifact["artifact_sha256"] = hashlib.sha256(canon).hexdigest()
    artifact["hash_linked_to_pool"] = pool_hash

    out_path = Path("src/air/experiments/tasks/v2/ctc_1_predictions.json")
    out_path.write_text(json.dumps(artifact, indent=1))
    print(f"\nPrediction artifact: {out_path}")
    print(f"Artifact SHA256: {artifact['artifact_sha256']}")
    print(f"Hash-linked to pool: {pool_hash[:16]}...")

    if n_nondisc > 0:
        print(f"\nFREEZE BLOCKED: {n_nondisc} non-discriminating pair(s). "
              f"Regenerate before freeze (pre-execution iteration only).")
        sys.exit(1)
    print("\nFREEZE GATE: PASS — 12/12 AB pairs discriminating.")


if __name__ == "__main__":
    main()
