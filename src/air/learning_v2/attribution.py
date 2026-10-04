"""Phase 3: contrastive attribution over a fixed schema.

For each organizational property P (strategy value, role presence,
capability presence, topology value) and each task-feature stratum S,
compute the verified-rate differential of eligible records with P
present vs absent inside S.

Verified rate = positives / (positives + negatives), where positive =
SUPPORTED+SOUND and negative = FALSIFIED+SOUND. INCONCLUSIVE,
INVALID/UNTESTED, and unsound-evaluator records never enter the
computation.

This is CONDITIONAL ASSOCIATION, not causal attribution. The output
hypotheses feed rule generation; only the discriminating intervention
(evaluation.py) can establish effects.

Overlap requirements (preregistered constants, not tuned per run):
- treated (P present) support   >= MIN_TREATED
- untreated (P absent) support   >= MIN_UNTREATED
- the stratum must contain at least one positive and one negative
  eligible record (otherwise no contrast is possible)
When these fail, the attribution outcome is INSUFFICIENT_OVERLAP: a
first-class result, never an exception and never a causal claim.
"""

from __future__ import annotations

from air.learning_v2.contracts import (
    AtomicCondition,
    AttributionOutcome,
    Eligibility,
    ExperienceAttribution,
    LearningEvidence,
    Operator,
    TASK_FEATURES_V1,
)

MIN_TREATED = 2
MIN_UNTREATED = 2
MIN_EFFECT = 0.2  # minimum |differential| to propose an association

_INT_FEATURES = {
    "output_artifact_count",
    "independent_work_unit_count",
    "dependency_depth",
}
_BOOL_FEATURES = {
    "requires_output_artifact",
    "has_named_output_targets",
    "requested_write_effect",
    "requested_read_effect",
    "requested_execute_effect",
    "multi_step",
}


def _bin_value(feature: str, value) -> AtomicCondition:
    """Deterministic binning of a feature value into a stratum predicate."""
    if feature in _BOOL_FEATURES:
        return AtomicCondition(feature, Operator.EQ, bool(value))
    if feature in _INT_FEATURES:
        v = int(value)
        if v <= 0:
            return AtomicCondition(feature, Operator.EQ, 0)
        if v == 1:
            return AtomicCondition(feature, Operator.EQ, 1)
        return AtomicCondition(feature, Operator.GT, 1)
    raise ValueError(f"no binning defined for feature {feature!r}")


def _strata(records: list[LearningEvidence]) -> list[tuple[AtomicCondition, ...]]:
    """All single-feature strata observed in the records, deterministically
    ordered. Single-feature strata keep the hypothesis space small and
    inspectable; conjunctions are a future extension, not silent behavior."""
    seen: dict[tuple, AtomicCondition] = {}
    for r in sorted(records, key=lambda x: x.id):
        for f in TASK_FEATURES_V1:
            cond = _bin_value(f, r.task_features.get(f))
            key = (cond.feature, cond.op.value, cond.value)
            seen.setdefault(key, cond)
    return [(c,) for _, c in sorted(seen.items())]


def _property_values(records: list[LearningEvidence]) \
        -> list[tuple[str, str]]:
    """(dimension, value) pairs observed: strategy values, role names,
    capability names, topology values. Deterministically ordered."""
    strategies, roles, caps, topologies = set(), set(), set(), set()
    for r in records:
        strategies.add(r.organization.strategy)
        roles.update(r.organization.roles)
        caps.update(r.organization.capabilities)
        topologies.add(r.organization.topology)
    out: list[tuple[str, str]] = []
    for s in sorted(strategies):
        out.append(("strategy", s))
    for x in sorted(roles):
        out.append(("role", x))
    for x in sorted(caps):
        out.append(("capability", x))
    for t in sorted(topologies):
        out.append(("topology", t))
    return out


def _holds(dimension: str, value: str, record: LearningEvidence) -> bool:
    org = record.organization
    if dimension == "strategy":
        return org.strategy == value
    if dimension == "role":
        return value in org.roles
    if dimension == "capability":
        return value in org.capabilities
    if dimension == "topology":
        return org.topology == value
    raise ValueError(f"unknown property dimension {dimension!r}")


def _robustness_check(in_stratum: list[LearningEvidence], dimension: str,
                      value: str, overall_diff: float,
                      stratum_feature: str,
                      min_treated: int, min_untreated: int,
                      min_effect: float) -> tuple[bool, str]:
    """Condition the association on every other class-A feature AND every
    other organizational property dimension.

    A confounded association (one fully explained by another feature or
    by another property, e.g. a role association that is really a
    capability association) is marked not-robust: it is still recorded,
    but it generates no rule. Sub-strata too small to power are
    untestable and do not count for or against.
    """
    notes: list[str] = []
    robust = True

    def check_split(label: str,
                    sub: list[LearningEvidence]) -> None:
        nonlocal robust
        treated = [r for r in sub if _holds(dimension, value, r)]
        untreated = [r for r in sub if not _holds(dimension, value, r)]
        if len(treated) < min_treated or len(untreated) < min_untreated:
            return
        # Both treatment sides are observed, so the sub-differential is
        # meaningful: 0 means the property adds nothing here, which
        # contradicts a nonzero overall differential.
        t_pos = sum(1 for r in treated if r.is_positive)
        u_pos = sum(1 for r in untreated if r.is_positive)
        diff = round(t_pos / len(treated) - u_pos / len(untreated), 4)
        same_sign = (diff > 0) == (overall_diff > 0)
        if not same_sign or abs(diff) < min_effect / 2:
            robust = False
            notes.append(
                f"conditioned on {label}: sub-differential {diff} "
                f"contradicts overall {overall_diff}")

    # Phase 1: condition on other task features.
    for g in TASK_FEATURES_V1:
        if g == stratum_feature:
            continue
        bins: dict[tuple, list[LearningEvidence]] = {}
        for r in in_stratum:
            cond = _bin_value(g, r.task_features.get(g))
            bins.setdefault((cond.op.value, repr(cond.value)), []).append(r)
        for key in sorted(bins):
            check_split(f"{g}={key[0]} {key[1]}", bins[key])

    # Phase 2: condition on other organizational property dimensions.
    for d2, v2 in _property_values(in_stratum):
        if (d2, v2) == (dimension, value):
            continue
        sub = [r for r in in_stratum if _holds(d2, v2, r)]
        check_split(f"{d2}={v2}", sub)

    summary = ("robust across conditioned confounders" if robust
               else "CONFOUNDED: " + "; ".join(notes))
    return robust, summary


def attribute(records: list[LearningEvidence], *,
              min_treated: int = MIN_TREATED,
              min_untreated: int = MIN_UNTREATED,
              min_effect: float = MIN_EFFECT) -> list[ExperienceAttribution]:
    """Run the fixed-schema contrastive search. Returns one attribution
    per (stratum, property) pair, including INSUFFICIENT_OVERLAP and
    NO_DIFFERENTIAL outcomes: the absence of evidence is recorded, not
    silently dropped."""
    eligible = [r for r in records
                if r.eligibility in (Eligibility.POSITIVE, Eligibility.NEGATIVE)]
    results: list[ExperienceAttribution] = []
    seq = 0
    for stratum in _strata(eligible):
        in_stratum = [r for r in eligible
                      if all(c.holds(r.task_features) for c in stratum)]
        if not in_stratum:
            continue
        n_pos = sum(1 for r in in_stratum if r.is_positive)
        n_neg = sum(1 for r in in_stratum if r.is_negative)
        for dimension, value in _property_values(in_stratum):
            seq += 1
            aid = f"attr_{seq:04d}"
            treated = [r for r in in_stratum if _holds(dimension, value, r)]
            untreated = [r for r in in_stratum
                         if not _holds(dimension, value, r)]
            base = dict(id=aid, stratum=stratum,
                        property_dimension=dimension, property_value=value,
                        evidence_ids=tuple(r.id for r in in_stratum))
            # Overlap requirements first: without both sides observed,
            # no contrastive claim is licensed.
            if (len(treated) < min_treated or len(untreated) < min_untreated
                    or n_pos == 0 or n_neg == 0):
                results.append(ExperienceAttribution(
                    **base, outcome=AttributionOutcome.INSUFFICIENT_OVERLAP,
                    treated_n=len(treated), untreated_n=len(untreated),
                    notes=(f"treated_n={len(treated)} untreated_n={len(untreated)} "
                           f"stratum_pos={n_pos} stratum_neg={n_neg}; "
                           f"requires treated>={min_treated}, "
                           f"untreated>={min_untreated}, both outcome "
                           f"sides observed")))
                continue
            t_pos = sum(1 for r in treated if r.is_positive)
            t_neg = sum(1 for r in treated if r.is_negative)
            u_pos = sum(1 for r in untreated if r.is_positive)
            u_neg = sum(1 for r in untreated if r.is_negative)
            t_rate = t_pos / (t_pos + t_neg)
            u_rate = u_pos / (u_pos + u_neg)
            diff = round(t_rate - u_rate, 4)
            common = dict(treated_n=len(treated), untreated_n=len(untreated),
                          treated_positives=t_pos, treated_negatives=t_neg,
                          untreated_positives=u_pos, untreated_negatives=u_neg,
                          differential=diff)
            if abs(diff) < min_effect:
                results.append(ExperienceAttribution(
                    **base, **common,
                    outcome=AttributionOutcome.NO_DIFFERENTIAL,
                    notes=f"|differential|={abs(diff)} < {min_effect}"))
                continue
            direction = ("higher verified rate with property present"
                         if diff > 0 else
                         "lower verified rate with property present")
            robust, robustness_notes = _robustness_check(
                in_stratum, dimension, value, diff, stratum[0].feature,
                min_treated, min_untreated, min_effect)
            results.append(ExperienceAttribution(
                **base, **common,
                outcome=AttributionOutcome.ASSOCIATION,
                robust=robust, robustness_notes=robustness_notes,
                notes=(f"{direction}: treated {t_pos}/{t_pos + t_neg} "
                       f"vs untreated {u_pos}/{u_pos + u_neg}")))
    return results
