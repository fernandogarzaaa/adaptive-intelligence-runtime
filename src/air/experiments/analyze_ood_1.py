"""Frozen OOD-1 analysis script (preregistered).

Implements the analysis plan from docs/PREREGISTRATION_OOD_1.md exactly.
Reads the ood-1 ledger records and decision decompositions; writes a
frozen results report. No parameters, no tuning, no post-hoc exclusions.

Usage:
    PYTHONPATH=src AIR_EXP_ID=<exp> python -m air.experiments.analyze_ood_1

Requires: ood-1 runs recorded in the ledger (phase == "ood-1").
"""

import json
import sys

LEDGER_PATH = None  # set in main()


def load_ledger():
    import os
    exp_id = os.environ.get("AIR_EXP_ID", "dseries-v2-1")
    path = os.path.expanduser(f"~/workspace/air-experiments/{exp_id}/ledger.jsonl")
    recs = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                recs.append(json.loads(line))
    return recs


def verified(rec):
    return (rec.get("evaluation_verdict") == "SUPPORTED"
            and rec.get("assurance_verdict") == "SOUND")


def main():
    recs = load_ledger()

    # Load decision decompositions
    decomps = {}
    for r in recs:
        if r["kind"] == "ood1_decomposition":
            p = r["payload"]
            decomps[p["task_id"]] = p

    # Load runs
    runs = {}
    for r in recs:
        if r["kind"] == "run" and r["payload"].get("phase") == "ood-1":
            p = r["payload"]
            runs[(p["task_id"], p["policy"])] = p

    # Load metadata: derived deterministically from frozen task ID
    # naming conventions (repair 2026-10-05: eliminated /tmp dependency;
    # see ood1_defect ledger record). Pair structure is frozen in the
    # task pool and protocol doc Section 4.1.
    def _meta_for(tid):
        if tid.startswith("ood_p"):
            return {"pair": None, "pair_type": None,
                    "expected_organization": "single_agent"}
        if tid.startswith("ood_r"):
            return {"pair": None, "pair_type": None,
                    "expected_organization": None}
        # Minimal pairs: ood_m1a, ood_m1b, ood_m2a, ...
        # ood_l1a, ood_l1b, ood_l2a, ...
        kind = tid[4]  # 'm' or 'l'
        pair = tid[4:6].upper()  # M1, M2, L1, L2
        member = tid[6]  # 'a' or 'b'
        if kind == "m":
            exp = "single_agent" if member == "a" else "parallel_agents"
            return {"pair": pair, "pair_type": "mechanical-contrast",
                    "expected_organization": exp}
        else:
            exp = "single_agent" if pair == "L1" else None
            return {"pair": pair, "pair_type": "lexical-contrast",
                    "expected_organization": exp}

    meta = {t: _meta_for(t) for t in decomps}

    def group(tid):
        if tid.startswith("ood_p"):
            return "production-OOD"
        if tid.startswith("ood_r"):
            return "research-OOD"
        m = meta.get(tid, {})
        pt = m.get("pair_type", "")
        return f"pair-{m.get('pair')}-{pt}"

    # ---- 5.1 PRIMARY: production-OOD allocation (H2) ----
    prod_ids = sorted(t for t in decomps if t.startswith("ood_p"))
    h2_correct = sum(1 for t in prod_ids
                     if decomps[t]["pv2_choice"] == "single_agent")
    h2_total = len(prod_ids)
    v1_prod_single = sum(1 for t in prod_ids
                         if decomps[t]["v1_choice"] == "single_agent")

    # ---- 5.2 SECONDARY: mechanical-contrast discrimination (H1) ----
    h1_results = {}
    for pair in ["M1", "M2"]:
        a, b = f"ood_{pair.lower()}a", f"ood_{pair.lower()}b"
        da, db = decomps.get(a), decomps.get(b)
        if da and db:
            h1_results[pair] = {
                "a_pv2": da["pv2_choice"], "b_pv2": db["pv2_choice"],
                "a_v1": da["v1_choice"], "b_v1": db["v1_choice"],
                "discriminates": da["pv2_choice"] != db["pv2_choice"],
                "mech_hash_equal": (da["mechanical_features_hash"]
                                    == db["mechanical_features_hash"]),
                "raw_hash_equal": (da["raw_text_hash"]
                                   == db["raw_text_hash"]),
            }

    # ---- 5.3 SECONDARY: research-OOD stability ----
    res_ids = sorted(t for t in decomps if t.startswith("ood_r"))
    pv2_incoherent = sum(1 for t in res_ids
                         if decomps[t]["pv2_choice"] == "INCOHERENT")
    # verified outcome comparison
    pv2_res_ver = sum(1 for t in res_ids
                      if verified(runs.get((t, "active"), {})))
    v1_res_ver = sum(1 for t in res_ids
                     if verified(runs.get((t, "parent"), {})))

    # ---- 5.4 SECONDARY: lexical-contrast invariance ----
    l_results = {}
    for pair in ["L1", "L2"]:
        a, b = f"ood_{pair.lower()}a", f"ood_{pair.lower()}b"
        da, db = decomps.get(a), decomps.get(b)
        if da and db:
            l_results[pair] = {
                "a_pv2": da["pv2_choice"], "b_pv2": db["pv2_choice"],
                "invariant": da["pv2_choice"] == db["pv2_choice"],
                "mech_hash_equal": (da["mechanical_features_hash"]
                                    == db["mechanical_features_hash"]),
                "raw_hash_equal": (da["raw_text_hash"]
                                   == db["raw_text_hash"]),
            }

    # ---- 5.5 SECONDARY: verified outcome rates ----
    all_ids = sorted(decomps.keys())
    pv2_ver = sum(1 for t in all_ids if verified(runs.get((t, "active"), {})))
    v1_ver = sum(1 for t in all_ids if verified(runs.get((t, "parent"), {})))

    # Resource check on verified runs
    def mean_latency(policy):
        lat = [runs[(t, policy)]["metrics"]["latency_s"] * 1000
               for t in all_ids
               if verified(runs.get((t, policy), {}))]
        return sum(lat) / len(lat) if lat else None

    pv2_lat = mean_latency("active")
    v1_lat = mean_latency("parent")
    lat_ratio = (pv2_lat / v1_lat) if (pv2_lat and v1_lat) else None

    report = {
        "experiment": "ood-1",
        "primary_H2": {
            "production_ood_accuracy": f"{h2_correct}/{h2_total}",
            "production_ood_rate": round(h2_correct / h2_total, 4) if h2_total else None,
            "v1_baseline_single_agent": f"{v1_prod_single}/{h2_total}",
            "interpretation": (
                "H2 supported" if h2_correct >= 14 else
                "H2 weakly supported" if h2_correct >= 10 else
                "H2 refuted"
            ),
        },
        "secondary_H1_mechanical_discrimination": h1_results,
        "secondary_research_stability": {
            "pv2_incoherent": pv2_incoherent,
            "pv2_verified": pv2_res_ver,
            "v1_verified": v1_res_ver,
            "non_regression": (pv2_res_ver >= v1_res_ver - 2),
        },
        "secondary_lexical_invariance": l_results,
        "secondary_verified_outcomes": {
            "pv2_verified": f"{pv2_ver}/{len(all_ids)}",
            "v1_verified": f"{v1_ver}/{len(all_ids)}",
            "latency_ratio_pv2_over_v1": round(lat_ratio, 3) if lat_ratio else None,
            "latency_gate_2x": (lat_ratio <= 2.0) if lat_ratio else None,
        },
        "hash_audit": {
            pair: {
                "mech_equal": v["mech_hash_equal"],
                "raw_equal": v["raw_hash_equal"],
            } for pair, v in {**h1_results, **l_results}.items()
        },
    }

    print(json.dumps(report, indent=2))
    return report


if __name__ == "__main__":
    main()
