"""CTC-1 analysis: mechanical per-pair classification + synthesis.

TWO SEPARATE OUTPUTS (per Inan 2026-10-06):
1. Immutable execution ledger: raw observations + preregistered
   classification per pair, via the falsification matrix (protocol 7).
   No interpretation beyond the matrix.
2. Synthesis: scientific interpretation, generated AFTER all 48 tasks
   complete, kept separate from classification.

The primary object is the AB counterfactual, not an aggregate rate.

Usage:
    PYTHONPATH=src AIR_EXP_ID=dseries-v2-1 python -m air.experiments.analyze_ctc_1
"""

import json
import hashlib
import os
from pathlib import Path
from collections import defaultdict


def load_ledger():
    exp_id = os.environ.get("AIR_EXP_ID", "dseries-v2-1")
    path = Path.home() / f"workspace/air-experiments/{exp_id}/ledger.jsonl"
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def verified(rec):
    return (rec.get("evaluation_verdict") == "SUPPORTED"
            and rec.get("assurance_verdict") == "SOUND")


def classify_ab_pair(pair_id, da, db, v1a, v1b):
    """Apply preregistered falsification matrix (protocol Section 7).

    Returns (classification, detail). Classifications:
    - E1_SUPPORTED, E2_SUPPORTED, ANOMALY, CONTROL_FAILURE
    """
    pa, pb = da["pv2_choice"], db["pv2_choice"]
    fa, fb = da["feasibility"], db["feasibility"]

    # Control check first: v1 must choose parallel for both
    v1_ok = (v1a["v1_choice"] == "parallel_agents"
             and v1b["v1_choice"] == "parallel_agents")
    if not v1_ok:
        return ("CONTROL_FAILURE",
                f"v1 chose {v1a['v1_choice']}/{v1b['v1_choice']}, "
                f"expected parallel/parallel")

    # A member: parallel must be infeasible by construction
    par_feas_a = fa["parallel_agents"]["feasible"]
    if pa == "parallel_agents":
        return ("ANOMALY",
                "A chose parallel despite infeasible topology "
                "(protocol/mechanism failure)")

    # Feasibility path check for B switch
    par_feas_b = fb["parallel_agents"]["feasible"]
    b_par_score = db["pv2_final_scores"].get("parallel_agents")
    b_sng_score = db["pv2_final_scores"].get("single_agent")

    # Did the feasibility mechanism explain the outcome?
    # Check: A parallel infeasible with WRITE reason, B parallel feasible
    a_write_reason = any("WRITE" in r for r in
                         fa["parallel_agents"]["infeasible_reasons"])
    feas_path = (not par_feas_a) and a_write_reason and par_feas_b

    if pa == "single_agent" and pb == "parallel_agents":
        if feas_path and b_par_score is not None and b_sng_score is not None:
            if b_par_score > b_sng_score:
                return ("E1_SUPPORTED",
                        f"switch via feasibility path "
                        f"(B par={b_par_score} > sng={b_sng_score})")
        return ("ANOMALY",
                "B switched but not via preregistered feasibility path")

    if pa == "single_agent" and pb == "single_agent":
        # E2 iff B parallel was feasible AND scored higher
        if par_feas_b and b_par_score is not None and b_sng_score is not None:
            if b_par_score > b_sng_score:
                return ("E2_SUPPORTED",
                        f"no switch despite feasible B "
                        f"(par={b_par_score} > sng={b_sng_score})")
        return ("ANOMALY",
                f"B single but feasibility/score ambiguous "
                f"(feas={par_feas_b}, par={b_par_score}, sng={b_sng_score})")

    return ("ANOMALY", f"unexpected pattern A={pa} B={pb}")


def classify_c_pair(pair_id, da, db):
    """C: both members should be parallel_agents (WRITE feasible)."""
    pa, pb = da["pv2_choice"], db["pv2_choice"]
    if pa == "parallel_agents" and pb == "parallel_agents":
        return ("E1_SUPPORTED", "both parallel, capability substitution OK")
    return ("CAPABILITY_SENSITIVITY_FAILURE",
            f"A={pa} B={pb}, expected parallel/parallel")


def classify_d_pair(pair_id, da, db):
    """D: both members should be single_agent (WRITE infeasible both)."""
    pa, pb = da["pv2_choice"], db["pv2_choice"]
    if pa == "single_agent" and pb == "single_agent":
        return ("INVARIANT_HELD", "both single, irrelevant caps ignored")
    return ("SPURIOUS_SENSITIVITY",
            f"A={pa} B={pb}, decision changed on irrelevant capability")


def main():
    recs = load_ledger()

    # Load decompositions (pv2 = active rules)
    decomps = {}
    for r in recs:
        if r["kind"] == "ctc1_decomposition":
            p = r["payload"]
            decomps[p["task_id"]] = p

    # Load runs
    runs = {}
    for r in recs:
        if r["kind"] == "run" and r["payload"].get("phase") == "ctc-1":
            p = r["payload"]
            runs[(p["task_id"], p["policy"])] = p

    # Load metadata (pair structure)
    meta = json.loads(Path(
        "src/air/experiments/tasks/v2/ctc_1_metadata.json").read_text())

    # Group by pair
    pairs = defaultdict(dict)
    for tid, m in meta.items():
        pairs[m["pair"]][tid] = m["pair_type"]

    assert len(decomps) == 48, f"expected 48 decomps, got {len(decomps)}"
    assert len(runs) == 96, f"expected 96 runs, got {len(runs)}"

    # --- OUTPUT 1: Immutable execution ledger (classification only) ---
    classifications = []
    for pair_id in sorted(pairs):
        members = pairs[pair_id]
        assert len(members) == 2
        tids = sorted(members)
        ta_id = next(t for t in tids if t.endswith("a"))
        tb_id = next(t for t in tids if t.endswith("b"))
        ptype = members[ta_id]

        da, db = decomps[ta_id], decomps[tb_id]
        # v1 decompositions (empty rules) for control check
        from air.experiments.v2harness import decision_decomposition
        import json as _j
        pool = _j.loads(Path(
            "src/air/experiments/tasks/v2/ctc_1.json").read_text())
        tmap = {t["id"]: t for t in pool["tasks"]}
        v1a = decision_decomposition(tmap[ta_id], [])
        v1b = decision_decomposition(tmap[tb_id], [])

        if ptype == "AB":
            cls, detail = classify_ab_pair(pair_id, da, db, v1a, v1b)
        elif ptype == "C":
            cls, detail = classify_c_pair(pair_id, da, db)
        elif ptype == "D":
            cls, detail = classify_d_pair(pair_id, da, db)

        # Raw observations
        ra = runs.get((ta_id, "active"), {})
        rb = runs.get((tb_id, "active"), {})
        classifications.append({
            "pair_id": pair_id,
            "pair_type": ptype,
            "task_A": ta_id,
            "task_B": tb_id,
            "pv2_choice_A": da["pv2_choice"],
            "pv2_choice_B": db["pv2_choice"],
            "v1_choice_A": v1a["v1_choice"],
            "v1_choice_B": v1b["v1_choice"],
            "parallel_feasible_A": da["feasibility"]["parallel_agents"]["feasible"],
            "parallel_feasible_B": db["feasibility"]["parallel_agents"]["feasible"],
            "pv2_final_A": da["pv2_final_scores"],
            "pv2_final_B": db["pv2_final_scores"],
            "verified_A": verified(ra),
            "verified_B": verified(rb),
            "classification": cls,
            "classification_detail": detail,
        })
        print(f"{pair_id} [{ptype}]: pv2 {da['pv2_choice']}/{db['pv2_choice']} "
              f"-> {cls}")

    # Summary counts (raw, per protocol 8.1)
    ab = [c for c in classifications if c["pair_type"] == "AB"]
    e1 = sum(1 for c in ab if c["classification"] == "E1_SUPPORTED")
    e2 = sum(1 for c in ab if c["classification"] == "E2_SUPPORTED")
    anom = sum(1 for c in ab if c["classification"] not in
               ("E1_SUPPORTED", "E2_SUPPORTED"))

    ledger_out = {
        "artifact": "ctc-1-execution-ledger",
        "version": 1,
        "n_pairs": len(classifications),
        "classifications": classifications,
        "ab_summary": {
            "E1_consistent": e1,
            "E2_consistent": e2,
            "anomalous_or_control_failure": anom,
            "non_discriminating": 0,
            "denominator": 12,
        },
    }
    canon = json.dumps(ledger_out, sort_keys=True).encode()
    ledger_out["artifact_sha256"] = hashlib.sha256(canon).hexdigest()

    out_path = Path("src/air/experiments/tasks/v2/ctc_1_execution_ledger.json")
    out_path.write_text(json.dumps(ledger_out, indent=1))
    print(f"\nExecution ledger: {out_path}")
    print(f"SHA256: {ledger_out['artifact_sha256']}")
    print(f"\nAB summary: E1={e1}/12 E2={e2}/12 anomalous={anom}/12")

    # --- OUTPUT 2: Synthesis (separate, after all 48 complete) ---
    c_pairs = [c for c in classifications if c["pair_type"] == "C"]
    d_pairs = [c for c in classifications if c["pair_type"] == "D"]
    c_ok = sum(1 for c in c_pairs if c["classification"] == "E1_SUPPORTED")
    d_ok = sum(1 for c in d_pairs if c["classification"] == "INVARIANT_HELD")

    # Verified outcomes
    pv2_ver = sum(1 for (t, p), r in runs.items()
                 if p == "active" and verified(r))
    v1_ver = sum(1 for (t, p), r in runs.items()
                if p == "parent" and verified(r))

    synthesis = {
        "artifact": "ctc-1-synthesis",
        "version": 1,
        "execution_ledger_sha256": ledger_out["artifact_sha256"],
        "primary_ab": {
            "E1_consistent": f"{e1}/12",
            "E2_consistent": f"{e2}/12",
            "anomalous": f"{anom}/12",
            "threshold_E1": ">=9/12",
            "threshold_E2": ">=9/12",
            "outcome": ("E1_SUPPORTED" if e1 >= 9 else
                        "E2_SUPPORTED" if e2 >= 9 else "INCONCLUSIVE"),
        },
        "secondary_c": {
            "capability_substitution_ok": f"{c_ok}/6",
        },
        "secondary_d": {
            "irrelevant_invariance_held": f"{d_ok}/6",
        },
        "verified_outcomes": {
            "pv2": f"{pv2_ver}/48",
            "v1": f"{v1_ver}/48",
        },
    }
    syn_path = Path("src/air/experiments/tasks/v2/ctc_1_synthesis.json")
    syn_path.write_text(json.dumps(synthesis, indent=1))
    print(f"\nSynthesis: {syn_path}")
    print(f"C pairs: {c_ok}/6 | D pairs: {d_ok}/6")
    print(f"Verified: pv2 {pv2_ver}/48, v1 {v1_ver}/48")
    print(f"\nPrimary outcome: {synthesis['primary_ab']['outcome']}")


if __name__ == "__main__":
    main()
