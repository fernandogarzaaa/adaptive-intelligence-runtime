"""Phase 5a: discriminating evaluation.

The decisive evaluation is an INTERVENTION, not a retrospective
association: parent and candidate are executed on the same sealed
tasks, same environment, same budgets, same evaluator, same
assurance. Paired outcomes feed:

- decision delta: tasks where parent and candidate choose different
  organizations. Empty delta -> VACUOUS (promotion refused outright;
  a candidate that changes nothing cannot be promoted).
- improvement: exact McNemar-style paired test on binary verified
  success, required on at least one discriminating class.
- non-inferiority: per class, the candidate's verified-rate lower
  bound (Wilson) must remain above parent rate - epsilon
  (preregistered per class). Safety/invariant classes are hard
  zero-tolerance: any violation rejects.
- multiplicity: the method is preregistered (default: the primary
  discriminating class is tested at alpha; remaining classes are
  guardrails via non-inferiority, not separate superiority tests).
- resource gate: verified success must improve; cost and latency
  must be non-inferior within preregistered bounded increases.

Paired holdout -> McNemar. Independent holdout -> Fisher's exact
(from generation.py). The choice is frozen before evaluation via
the `paired` flag.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from air.learning_v2.contracts import DiscriminatingEvaluation
from air.learning_v2.generation import fisher_exact_2x2

DEFAULT_ALPHA = 0.05
DEFAULT_NON_INFERIORITY_EPS = 0.1
DEFAULT_MAX_COST_INCREASE = 0.25
DEFAULT_MAX_LATENCY_INCREASE = 0.25


@dataclass(frozen=True)
class EvalTask:
    id: str
    features: dict
    eval_class: str = "general"   # performance class for non-inferiority
    safety: bool = False          # safety/invariant class: zero tolerance


def _org_key(org) -> tuple:
    return (org.strategy, tuple(sorted(org.roles)),
            tuple(sorted(org.capabilities)), org.topology)


def decision_delta(parent_choose, candidate_choose,
                   tasks: list[EvalTask]) -> list[str]:
    """Task ids where parent and candidate choose different
    organizations. Deterministic."""
    delta = []
    for t in sorted(tasks, key=lambda x: x.id):
        if _org_key(parent_choose(t)) != _org_key(candidate_choose(t)):
            delta.append(t.id)
    return delta


def mcnemar_exact_onesided(b: int, c: int) -> float:
    """Exact one-sided McNemar p-value for candidate improvement.

    b = parent-success/candidate-failure discordants,
    c = parent-failure/candidate-success discordants.
    H0: candidate no better than parent. p = P(X >= c), X ~ Bin(b+c, 0.5).
    """
    n = b + c
    if n == 0:
        return 1.0
    return round(sum(math.comb(n, k) for k in range(c, n + 1)) / 2 ** n, 6)


def wilson_lower(successes: int, n: int, z: float = 1.96) -> float:
    if n == 0:
        return 0.0
    p = successes / n
    denom = 1 + z * z / n
    center = p + z * z / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return round(max(0.0, (center - margin) / denom), 4)


def _verified(eval_verdict: str, assurance_verdict: str) -> bool | None:
    """Paired binary outcome: True = verified success, False = verified
    failure, None = nondirectional (excluded from the paired test)."""
    ev, av = eval_verdict.upper(), assurance_verdict.upper()
    if av != "SOUND":
        return None
    if ev == "SUPPORTED":
        return True
    if ev == "FALSIFIED":
        return False
    return None


def evaluate_paired(*, id: str, candidate_id: str, parent_version: str,
                    sealed_pool_id: str, tasks: list[EvalTask],
                    parent_choose, candidate_choose, oracle,
                    paired: bool = True,
                    alpha: float = DEFAULT_ALPHA,
                    non_inferiority_eps: float = DEFAULT_NON_INFERIORITY_EPS,
                    max_cost_increase: float = DEFAULT_MAX_COST_INCREASE,
                    max_latency_increase: float = DEFAULT_MAX_LATENCY_INCREASE,
                    ) -> DiscriminatingEvaluation:
    """Run the sealed intervention. `oracle(task, org)` returns a dict
    with evaluation_verdict, assurance_verdict, resources {cost,
    latency_ms, ...}, and safety_violations (list)."""
    delta_ids = set(decision_delta(parent_choose, candidate_choose, tasks))
    paired_outcomes: list[dict] = []
    for t in sorted(tasks, key=lambda x: x.id):
        p_org = parent_choose(t)
        c_org = candidate_choose(t)
        p_res = oracle(t, p_org)
        c_res = oracle(t, c_org)
        paired_outcomes.append({
            "task_id": t.id,
            "eval_class": t.eval_class,
            "safety": t.safety,
            "differ": t.id in delta_ids,
            "parent_strategy": p_org.strategy,
            "candidate_strategy": c_org.strategy,
            "parent_verified": _verified(p_res["evaluation_verdict"],
                                        p_res["assurance_verdict"]),
            "candidate_verified": _verified(c_res["evaluation_verdict"],
                                           c_res["assurance_verdict"]),
            "parent_resources": dict(p_res.get("resources", {})),
            "candidate_resources": dict(c_res.get("resources", {})),
            "candidate_safety_violations": list(
                c_res.get("safety_violations", [])),
        })

    stats: dict = {"paired": paired, "alpha": alpha,
                   "non_inferiority_eps": non_inferiority_eps,
                   "classes": {}}
    verdict = "PASS"
    notes: list[str] = []

    if not delta_ids:
        return DiscriminatingEvaluation(
            id=id, candidate_id=candidate_id, parent_version=parent_version,
            sealed_pool_id=sealed_pool_id, decision_delta_task_ids=(),
            paired_outcomes=tuple(paired_outcomes), statistics={
                **stats, "verdict_reason": "empty decision delta: the "
                "candidate changes nothing; promotion refused"},
            verdict="VACUOUS")

    # Safety: hard zero-tolerance on safety-class tasks.
    safety_violations = [p["task_id"] for p in paired_outcomes
                         if p["safety"] and p["candidate_safety_violations"]]
    stats["safety_violations"] = safety_violations
    if safety_violations:
        verdict = "FAIL"
        notes.append(f"safety violations on {safety_violations}")

    # Per-class analysis over paired binary outcomes.
    classes: dict[str, list[dict]] = {}
    for p in paired_outcomes:
        classes.setdefault(p["eval_class"], []).append(p)
    improved_classes: list[str] = []
    for cls in sorted(classes):
        rows = classes[cls]
        pv = [r["parent_verified"] for r in rows]
        cv = [r["candidate_verified"] for r in rows]
        pairs = [(a, b) for a, b in zip(pv, cv)
                 if a is not None and b is not None]
        if not pairs:
            stats["classes"][cls] = {"n_paired": 0,
                                     "note": "no directional pairs"}
            continue
        p_rate = sum(pv) / len(pv) if pv else 0.0
        c_rate = sum(cv) / len(cv) if cv else 0.0
        c_lb = wilson_lower(sum(cv), len(cv))
        non_inferior = c_lb >= round(p_rate - non_inferiority_eps, 4)
        cls_stats: dict = {
            "n_paired": len(pairs),
            "parent_rate": round(p_rate, 4),
            "candidate_rate": round(c_rate, 4),
            "candidate_wilson_lower": c_lb,
            "non_inferior": non_inferior,
        }
        if paired:
            b = sum(1 for a, bb in pairs if a and not bb)
            cc = sum(1 for a, bb in pairs if not a and bb)
            p_val = mcnemar_exact_onesided(b, cc)
            cls_stats.update({"mcnemar_b": b, "mcnemar_c": cc,
                              "mcnemar_p_one_sided": p_val})
            disc = [r for r in rows if r["differ"]]
            if disc and cc > b and p_val < alpha:
                improved_classes.append(cls)
                cls_stats["improved"] = True
        else:
            a = sum(1 for x, y in pairs if x and y)
            b_ = sum(1 for x, y in pairs if x and not y)
            c_ = sum(1 for x, y in pairs if not x and y)
            d_ = sum(1 for x, y in pairs if not x and not y)
            p_val = fisher_exact_2x2(a, b_, c_, d_)
            cls_stats.update({"fisher_table": [a, b_, c_, d_],
                              "fisher_p_two_sided": p_val})
            disc = [r for r in rows if r["differ"]]
            if disc and c_rate > p_rate and p_val < alpha:
                improved_classes.append(cls)
                cls_stats["improved"] = True
        stats["classes"][cls] = cls_stats
        if not non_inferior:
            verdict = "FAIL"
            notes.append(f"class {cls}: candidate not non-inferior "
                         f"(lower {c_lb} < parent {round(p_rate, 4)} - eps)")
    stats["improved_classes"] = improved_classes
    if not improved_classes:
        verdict = "FAIL"
        notes.append("no discriminating class improved at the "
                     "preregistered bar")

    # Resource gate: success must improve (above); cost/latency
    # non-inferior within bounded increases.
    def mean_cost(rows, side):
        vals = [r[f"{side}_resources"].get("cost", 0.0) for r in rows]
        return sum(vals) / len(vals) if vals else 0.0

    def mean_lat(rows, side):
        vals = [r[f"{side}_resources"].get("latency_ms", 0.0) for r in rows]
        return sum(vals) / len(vals) if vals else 0.0

    pc, ccost = mean_cost(paired_outcomes, "parent"), mean_cost(
        paired_outcomes, "candidate")
    plat, clat = mean_lat(paired_outcomes, "parent"), mean_lat(
        paired_outcomes, "candidate")
    stats["resources"] = {
        "parent_mean_cost": round(pc, 4), "candidate_mean_cost": round(ccost, 4),
        "parent_mean_latency_ms": round(plat, 2),
        "candidate_mean_latency_ms": round(clat, 2),
    }
    if pc > 0 and ccost > pc * (1 + max_cost_increase):
        verdict = "FAIL"
        notes.append(f"cost increase {round(ccost / pc - 1, 3)} exceeds "
                     f"bound {max_cost_increase}")
    if plat > 0 and clat > plat * (1 + max_latency_increase):
        verdict = "FAIL"
        notes.append(f"latency increase {round(clat / plat - 1, 3)} exceeds "
                     f"bound {max_latency_increase}")
    stats["verdict_reason"] = "; ".join(notes) if notes else \
        "improvement on >=1 class, non-inferior elsewhere, resources bounded"

    return DiscriminatingEvaluation(
        id=id, candidate_id=candidate_id, parent_version=parent_version,
        sealed_pool_id=sealed_pool_id,
        decision_delta_task_ids=tuple(sorted(delta_ids)),
        paired_outcomes=tuple(paired_outcomes), statistics=stats,
        verdict=verdict)
