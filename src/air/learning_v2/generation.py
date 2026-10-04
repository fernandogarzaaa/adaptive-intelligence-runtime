"""Phase 4: rule generation by enumeration.

The hypothesis space is intentionally small, so generation enumerates
it rather than searching it cleverly: every ASSOCIATION attribution
maps to exactly one candidate rule by a fixed, documented mapping.
There is no heuristic search, no beam, no LLM. Given the attributions,
the generated rules are fully determined, which is the point: you
want to know exactly why the learner discovered a rule.

Attribution -> rule mapping (fixed):
- capability C present, differential > 0  -> require_capability(C)
- role R present,       differential > 0  -> require_role(R)
- strategy S,           differential > 0  -> boost_strategy(S, +DELTA)
- strategy S,           differential < 0  -> penalize_strategy(S, -DELTA)
- topology associations -> reported, no rule (no topology action in v1)
- negative capability/role associations -> reported, no rule (no
  forbid_* actions in v1; the positive hard requirements cover the
  observed failure mode)

evidence_strength (evidence-strength/v1, deterministic):
    strength = |differential| * n / (n + 4),  n = treated + untreated
Uncertainty is reported alongside, never conflated: effect estimate,
sample sizes, overlap counts, and Fisher's exact p-value on the 2x2
table (treated pos/neg vs untreated pos/neg).
"""

from __future__ import annotations

import math

from air.learning_v2.contracts import (
    AllocationRule,
    AtomicCondition,
    AttributionOutcome,
    ExperienceAttribution,
    PolicyCandidate,
    RuleAction,
    RuleThen,
    EVIDENCE_STRENGTH_VERSION,
    MAX_STRATEGY_DELTA,
)

STRATEGY_DELTA = 0.1  # preregistered scoring adjustment magnitude
STRENGTH_SMOOTHING_K = 4


def evidence_strength(differential: float, n: int) -> float:
    return round(abs(differential) * n / (n + STRENGTH_SMOOTHING_K), 4)


def fisher_exact_2x2(a: int, b: int, c: int, d: int) -> float:
    """Two-sided Fisher's exact p-value for [[a,b],[c,d]] via the
    hypergeometric enumeration. Exact; no scipy dependency."""
    n = a + b + c + d
    if n == 0:
        return 1.0
    row1, col1 = a + b, a + c

    def hyper(x: int) -> float:
        # P(X = x) for X ~ Hypergeometric(n, col1, row1)
        if x < 0 or x > row1 or x > col1 or row1 - x > n - col1:
            return 0.0
        return (math.comb(col1, x) * math.comb(n - col1, row1 - x)
                / math.comb(n, row1))

    p_obs = hyper(a)
    # Two-sided: sum probabilities of tables no more likely than observed.
    lo = max(0, row1 + col1 - n)
    hi = min(row1, col1)
    return round(sum(hyper(x) for x in range(lo, hi + 1)
                     if hyper(x) <= p_obs + 1e-12), 6)


def _uncertainty(attr: ExperienceAttribution) -> dict:
    return {
        "effect_estimate": attr.differential,
        "n_treated": attr.treated_n,
        "n_untreated": attr.untreated_n,
        "overlap": {
            "treated_positives": attr.treated_positives,
            "treated_negatives": attr.treated_negatives,
            "untreated_positives": attr.untreated_positives,
            "untreated_negatives": attr.untreated_negatives,
        },
        "p_value_fisher_exact": fisher_exact_2x2(
            attr.treated_positives, attr.treated_negatives,
            attr.untreated_positives, attr.untreated_negatives),
    }


def _rule_for(attr: ExperienceAttribution, seq: int) -> AllocationRule | None:
    dim, val, diff = (attr.property_dimension, attr.property_value,
                      attr.differential)
    n = attr.treated_n + attr.untreated_n
    common = dict(
        id=f"rule_{seq:04d}",
        when=attr.stratum,
        scope=attr.stratum,
        evidence=attr.evidence_ids,
        evidence_strength=evidence_strength(diff, n),
        uncertainty=_uncertainty(attr),
        constraints={"max_strategy_delta": MAX_STRATEGY_DELTA,
                     "evidence_strength_version": EVIDENCE_STRENGTH_VERSION,
                     "attribution_id": attr.id},
        priority=0,
    )
    if dim == "capability" and diff > 0:
        then = RuleThen(RuleAction.REQUIRE_CAPABILITY, val)
    elif dim == "role" and diff > 0:
        then = RuleThen(RuleAction.REQUIRE_ROLE, val)
    elif dim == "strategy" and diff > 0:
        then = RuleThen(RuleAction.BOOST_STRATEGY, val, delta=STRATEGY_DELTA)
    elif dim == "strategy" and diff < 0:
        then = RuleThen(RuleAction.PENALIZE_STRATEGY, val,
                        delta=-STRATEGY_DELTA)
    else:
        # topology associations and negative capability/role associations:
        # reported in the attribution, no rule in this revision.
        return None
    return AllocationRule(then=then, **common)


def generate_rules(attributions: list[ExperienceAttribution]) -> list[AllocationRule]:
    """Enumerate the hypothesis space over ASSOCIATION attributions.
    Deterministic: sorted input order, sequential rule ids.
    Only robust associations generate rules: a confounded association
    is recorded in the attribution but must not become policy."""
    rules: list[AllocationRule] = []
    seq = 0
    for attr in sorted(attributions, key=lambda a: a.id):
        if attr.outcome is not AttributionOutcome.ASSOCIATION:
            continue
        if not attr.robust:
            continue
        seq += 1
        rule = _rule_for(attr, seq)
        if rule is not None:
            rules.append(rule)
    return rules


def build_candidate(*, id: str, parent_version: str, hypothesis: str,
                    rules: list[AllocationRule],
                    attributions: list[ExperienceAttribution]) -> PolicyCandidate:
    """Assemble the candidate the learner hands to evaluation. The
    candidate carries its evidence; it never touches the allocator."""
    source: set[str] = set()
    for r in rules:
        source.update(r.evidence)
    return PolicyCandidate(
        id=id,
        parent_version=parent_version,
        rules=tuple(rules),
        hypothesis=hypothesis,
        source_experiences=tuple(sorted(source)),
        attribution_ids=tuple(a.id for a in sorted(
            attributions, key=lambda x: x.id)
            if a.outcome is AttributionOutcome.ASSOCIATION),
    )
