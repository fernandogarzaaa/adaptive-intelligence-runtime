"""D-series report: five-question matrix, counterfactual analysis, ledgers.

Reads the sealed registry and the per-phase ledgers post-hoc. The
primary metric is computed from immutable artifacts, never reported
by the agents under test.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

from air.experiments import registry

# Strategies whose agent specs include a role that can execute
# "produce"-phase operations (specialist/planner/coder). parallel_agents
# (researchers + synthesizer) structurally cannot produce.
PRODUCER_STRATEGIES = {"single_agent", "hierarchical_agents",
                       "adaptive_spawn", "research_then_execute",
                       "execute_then_verify", "direct"}
RESEARCH_STRATEGIES = {"parallel_agents"}

FIVE_QUESTIONS = [
    "D1: does AIR reproduce the baseline failure?",
    "Learning 1: can AIR generate and validate a useful allocation hypothesis?",
    "D2: does the promoted policy eliminate/reduce the known failure?",
    "Learning 2: does AIR refine rather than blindly reinforce the first lesson?",
    "D3: does the learned allocation principle generalize to unseen tasks?",
]


def _run_records(exp_dir: str, phase: str) -> list[dict]:
    return [r["payload"] for r in registry.load(exp_dir, "run")
            if r["payload"].get("phase") == phase]


def _verdict_of(rec: dict) -> str:
    return rec.get("metrics", {}).get("verdict", "UNTESTED")


def phase_summary(exp_dir: str, phase: str) -> dict:
    recs = _run_records(exp_dir, phase)
    buckets = Counter(_verdict_of(r) for r in recs)
    by_kind: dict[str, Counter] = defaultdict(Counter)
    by_strategy: dict[str, Counter] = defaultdict(Counter)
    for r in recs:
        v = _verdict_of(r)
        by_kind[r.get("task_kind", "?")][v] += 1
        by_strategy[r.get("strategy", "?")][v] += 1
    n = len(recs)
    supported = buckets.get("SUPPORTED", 0)
    return {
        "n_runs": n,
        "verified_successes": supported,
        "verified_rate": round(supported / n, 4) if n else 0,
        "verdict_buckets": dict(buckets),
        "by_task_kind": {k: dict(v) for k, v in by_kind.items()},
        "by_strategy": {k: dict(v) for k, v in by_strategy.items()},
    }


def counterfactual_analysis(exp_dir: str) -> dict:
    """Overfitting vs structural-learning verdict.

    For each D1 failure: chosen_strategy and the successful
    alternatives (strategies that verified the same task kind in
    D1 or the baseline A/B data). Then: under the final policy,
    did D3 production tasks get producer-containing organizations
    while D3 research tasks kept parallel research?
    """
    d1 = _run_records(exp_dir, "D1")
    d3 = _run_records(exp_dir, "D3")

    # Which (task_kind, strategy) verified in D1.
    verified_by_kind_strategy: dict[tuple[str, str], int] = Counter()
    for r in d1:
        if _verdict_of(r) == "SUPPORTED":
            verified_by_kind_strategy[(r.get("task_kind", "?"),
                                       r.get("strategy", "?"))] += 1

    failures = []
    for r in d1:
        if _verdict_of(r) != "SUPPORTED":
            kind = r.get("task_kind", "?")
            chosen = r.get("strategy", "?")
            alternatives = sorted(
                {s for (k, s) in verified_by_kind_strategy if k == kind})
            failures.append({
                "run_id": r["run_id"], "task_id": r["task_id"],
                "task_kind": kind, "chosen_strategy": chosen,
                "verdict": _verdict_of(r),
                "successful_alternatives": alternatives,
            })

    # D3 decisions under the final policy.
    d3_prod = [r for r in d3 if r.get("task_kind", "").startswith(
        ("exec_write", "read_filter_write", "config_nested",
         "multi_exec_write", "trap_write"))]
    d3_research = [r for r in d3 if r.get("task_kind", "").startswith(
        "research")]
    prod_strategies = Counter(r.get("strategy", "?") for r in d3_prod)
    research_strategies = Counter(r.get("strategy", "?") for r in d3_research)
    prod_producer = sum(n for s, n in prod_strategies.items()
                        if s in PRODUCER_STRATEGIES)
    research_parallel = sum(n for s, n in research_strategies.items()
                            if s in RESEARCH_STRATEGIES)

    n_prod = len(d3_prod)
    n_res = len(d3_research)
    structural = (n_prod > 0 and prod_producer == n_prod
                  and n_res > 0 and research_parallel == n_res)
    overfit = (n_res > 0 and research_parallel == 0)

    if structural:
        verdict = ("STRUCTURAL: producer-containing organizations for all "
                   "production tasks; parallel research retained for all "
                   "research tasks")
    elif overfit:
        verdict = ("OVERFITTING: parallel_agents avoided uniformly, including "
                   "on research tasks where it is the right organization")
    else:
        verdict = ("MIXED/INCONCLUSIVE: neither clean signature; see "
                   "decision counts")
    return {
        "d1_failures": failures,
        "d3_production_decisions": dict(prod_strategies),
        "d3_research_decisions": dict(research_strategies),
        "d3_production_with_producer": f"{prod_producer}/{n_prod}",
        "d3_research_with_parallel": f"{research_parallel}/{n_res}",
        "verdict": verdict,
    }


def _attributions(exp_dir: str) -> list[dict]:
    return [r["payload"] for r in registry.load(exp_dir, "learning_boundary")
            if "attribution" in r["payload"]]


def write_report(exp_dir: str) -> None:
    d1 = phase_summary(exp_dir, "D1")
    d2 = phase_summary(exp_dir, "D2")
    d3 = phase_summary(exp_dir, "D3")
    cf = counterfactual_analysis(exp_dir)
    attrs = _attributions(exp_dir)

    answers = {
        "D1": ("YES - reproduced" if d1["verified_rate"] < 0.9
               else "NO - failure did not reproduce"),
        "Learning 1": _l_answer(attrs, 0),
        "D2": ("see metrics" if d2["n_runs"] else "not run"),
        "Learning 2": _l_answer(attrs, 1),
        "D3": ("see counterfactual verdict" if d3["n_runs"] else "not run"),
    }
    report = {
        "exp_id": "d-series-v1",
        "five_questions": FIVE_QUESTIONS,
        "answers": answers,
        "phases": {"D1": d1, "D2": d2, "D3": d3},
        "counterfactual": cf,
        "attribution_chain": attrs,
        "honest_statement": (
            "The product learning engine (LearningEngine v1) aggregates run "
            "completion, not verification verdicts: it proposed positive "
            "strategy boosts for both strategies on D1 data, including the "
            "failing one. The policy format supports only global strategy "
            "boosts and a spawn threshold; it cannot represent task-kind x "
            "strategy interactions. Promotion requires an independent "
            "SUPPORTED evaluation, which the dims-based policy evaluation "
            "cannot produce from this data (ceiling effect). See the "
            "attribution records for the exact gate decisions."),
    }
    exp_path = Path(exp_dir)
    (exp_path / "report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True))
    lines = ["# D-series v1 report", ""]
    for i, q in enumerate(FIVE_QUESTIONS):
        key = ["D1", "Learning 1", "D2", "Learning 2", "D3"][i]
        lines.append(f"## {q}\n\n**{answers[key]}**\n")
    for phase, summ in (("D1", d1), ("D2", d2), ("D3", d3)):
        lines.append(
            f"### {phase}: {summ['verified_successes']}/{summ['n_runs']} "
            f"verified ({summ['verified_rate']:.1%})")
        lines.append(f"verdicts: {summ['verdict_buckets']}")
        lines.append(f"by strategy: {summ['by_strategy']}\n")
    lines.append("## Counterfactual: " + cf["verdict"])
    lines.append(f"production decisions: {cf['d3_production_decisions']}")
    lines.append(f"research decisions: {cf['d3_research_decisions']}\n")
    lines.append("## Attribution chain")
    for a in attrs:
        attr = a["attribution"]
        lines.append(f"- {a.get('label')}: v{attr.get('policy_version')} <- "
                     f"v{attr.get('parent_policy_version')}: "
                     f"{attr.get('promotion_decision')}")
    lines.append("\n## Honest statement\n\n" + report["honest_statement"])
    (exp_path / "report.md").write_text("\n".join(lines))
    print(f"report written: {exp_path / 'report.md'}")


def _l_answer(attrs: list[dict], idx: int) -> str:
    if idx >= len(attrs):
        return "not run"
    d = attrs[idx]["attribution"].get("promotion_decision", "?")
    if d == "PROMOTED":
        return "YES - candidate promoted through the gate"
    return f"NO - {d}"
