"""Phase 1: immutable contracts for the redesigned learning engine.

Implements docs/LEARNING_REDESIGN.md v2 as code. Every artifact is a frozen
dataclass carrying schema/policy/evaluator versions and canonical hashes,
so an independent party with the ledgers can re-derive every rule,
promotion, and rejection.

The learner's admissible feature set is class A only
(runtime-observable task features). Class B (genuine user/task-author
declarations) and class C (benchmark/evaluation labels) are defined here
for documentation but are NOT representable: the predicate language can
only reference TASK_FEATURES_V1, so a rule over a class C label cannot
be constructed. That is structural, not conventional.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# ---------------------------------------------------------------------------
# Versions. Bump exactly one when its semantics change; the hash of every
# artifact pins all of these, so schema drift is detectable, not silent.
# ---------------------------------------------------------------------------

TASK_FEATURE_SCHEMA_VERSION = "task-features/v1"
ORG_PROPERTY_SCHEMA_VERSION = "org-properties/v1"
RULE_SCHEMA_VERSION = "allocation-rule/v1"
POLICY_SEMANTICS_VERSION = "policy-semantics/v1"
EVIDENCE_STRENGTH_VERSION = "evidence-strength/v1"
# v2.1: the learning-outcome interpretation layer. Bumped when the mapping
# from evaluator verdicts to directional learning signal changes.
LEARNING_SCHEMA_VERSION = "learning-schema/v2.1"


# ---------------------------------------------------------------------------
# Class A: runtime-observable task features (the admissible hypothesis space).
# Mechanically derived from the task specification's declared operations
# and targets. Lexical proxies (word counts, artifact mentions, "three")
# are deliberately absent: the learner cannot condition on them.
# ---------------------------------------------------------------------------

TASK_FEATURES_V1: tuple[str, ...] = (
    "requires_output_artifact",   # bool
    "output_artifact_count",      # int >= 0
    "has_named_output_targets",   # bool
    "requested_write_effect",     # bool
    "requested_read_effect",      # bool
    "requested_execute_effect",   # bool
    "independent_work_unit_count", # int >= 0
    "dependency_depth",           # int >= 0
    "multi_step",                 # bool
)

# Class B: genuine user/task-author declarations. Admissible only when they
# genuinely exist before execution. NOT part of the default H3 hypothesis
# space; the predicate language below cannot reference them.
TASK_FEATURES_CLASS_B: tuple[str, ...] = (
    "declared_kind",
    "declared_constraints",
    "declared_output_type",
)

# Class C: benchmark/evaluation labels. Never admissible to the learner.
# Includes the task record's "kind" field of our own benchmarks, which is
# experiment-designer metadata, not a pre-allocation observable.
TASK_FEATURES_CLASS_C: tuple[str, ...] = (
    "production_task",
    "correct_strategy",
    "expected_role",
    "condition",
    "phase",
    "kind",
)


class Operator(str, Enum):
    EQ = "eq"
    NE = "ne"
    GT = "gt"
    GE = "ge"
    LT = "lt"
    LE = "le"


def _check_condition(feature_value: Any, op: Operator, value: Any) -> bool:
    if op is Operator.EQ:
        return feature_value == value
    if op is Operator.NE:
        return feature_value != value
    if op is Operator.GT:
        return feature_value > value
    if op is Operator.GE:
        return feature_value >= value
    if op is Operator.LT:
        return feature_value < value
    if op is Operator.LE:
        return feature_value <= value
    raise ValueError(f"unknown operator {op}")


@dataclass(frozen=True)
class AtomicCondition:
    """One predicate atom: feature_name OP value.

    feature_name MUST be in TASK_FEATURES_V1. Construction rejects
    anything else, so class B/C labels are structurally unrepresentable.
    """

    feature: str
    op: Operator
    value: Any

    def __post_init__(self) -> None:
        if self.feature not in TASK_FEATURES_V1:
            raise ValueError(
                f"feature {self.feature!r} is not in the admissible "
                f"class-A schema {TASK_FEATURE_SCHEMA_VERSION}; refusing "
                "to construct a predicate over it"
            )
        if isinstance(self.value, bool) or isinstance(self.value, (int, str)):
            return
        raise ValueError(
            f"predicate values must be bool/int/str, got "
            f"{type(self.value).__name__}"
        )

    def holds(self, features: dict) -> bool:
        return _check_condition(features.get(self.feature), self.op,
                                self.value)

    def canonical(self) -> dict:
        return {"feature": self.feature, "op": self.op.value,
                "value": self.value}


# ---------------------------------------------------------------------------
# Canonical hashing. Every artifact hashes its canonical form so policies
# are reproducible and schema drift is detectable.
# ---------------------------------------------------------------------------

def canonical_hash(obj: Any) -> str:
    """sha256 over a canonical JSON serialization (sorted keys, stable
    scalar rendering). Deterministic across processes."""
    def norm(x: Any) -> Any:
        if isinstance(x, dict):
            return {k: norm(x[k]) for k in sorted(x)}
        if isinstance(x, (list, tuple)):
            return [norm(i) for i in x]
        if isinstance(x, (set, frozenset)):
            return sorted((norm(i) for i in x),
                          key=lambda v: json.dumps(v, sort_keys=True))
        if isinstance(x, Enum):
            return x.value
        if hasattr(x, "canonical"):
            return norm(x.canonical())
        if isinstance(x, (bool, int, float, str)) or x is None:
            return x
        raise TypeError(f"cannot canonicalize {type(x).__name__}")
    blob = json.dumps(norm(obj), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Organizational property schema v1: what attribution may condition on.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class OrganizationProperties:
    strategy: str
    roles: frozenset = field(default_factory=frozenset)
    capabilities: frozenset = field(default_factory=frozenset)
    topology: str = "unknown"

    def canonical(self) -> dict:
        return {
            "strategy": self.strategy,
            "roles": sorted(self.roles),
            "capabilities": sorted(self.capabilities),
            "topology": self.topology,
            "schema_version": ORG_PROPERTY_SCHEMA_VERSION,
        }


# ---------------------------------------------------------------------------
# Learning eligibility (docs/LEARNING_REDESIGN.md section 4).
# Binds the assurance verdict: an UNSOUND evaluator's verdicts, positive
# or negative, are excluded from learning.
#
# Schema v2.1: eligibility is derived from the interpreted LearningOutcome,
# not directly from the raw evaluator verdict. The evaluator verdict is
# preserved on the record; the outcome layer is a separate, deterministic
# interpretation (see interpret_learning_outcome).
# ---------------------------------------------------------------------------

class Eligibility(str, Enum):
    POSITIVE = "positive"            # directional success signal
    NEGATIVE = "negative"            # directional failure signal
    NONDIRECTIONAL = "nondirectional"  # no directional signal
    EXCLUDED = "excluded"            # anything else


class LearningOutcome(str, Enum):
    """The learning signal interpreted from a run's verified outputs.

    Distinct from the evaluator's verdict: the evaluator reports what the
    evidence establishes about the run's DECLARED outcomes; the learning
    outcome reports what the run establishes about ORGANIZATION-TASK FIT
    for the learner's contrast. Both are preserved on LearningEvidence.
    """

    POSITIVE = "POSITIVE"                # evaluator SUPPORTED + SOUND
    NEGATIVE = "NEGATIVE"                # evaluator FALSIFIED + SOUND
    NEGATIVE_OUTCOME = "NEGATIVE_OUTCOME"  # certified required-outcome
                                           # failure (evaluator verdict
                                           # preserved, typically
                                           # INCONCLUSIVE)
    EXCLUDED = "EXCLUDED"                # nondirectional, unsound, invalid


# Outcome basis: WHY the learning outcome was assigned. The basis is part
# of the auditable record; NEGATIVE_OUTCOME is only ever paired with
# BASIS_REQUIRED_EFFECT_NOT_SATISFIED.
BASIS_EVALUATOR_SUPPORTED = "EVALUATOR_SUPPORTED"
BASIS_EVALUATOR_FALSIFIED = "EVALUATOR_FALSIFIED"
BASIS_REQUIRED_EFFECT_NOT_SATISFIED = "REQUIRED_EFFECT_NOT_SATISFIED"
BASIS_UNSOUND_ASSURANCE = "UNSOUND_ASSURANCE"
BASIS_NO_EXPLICIT_REQUIREMENT = "NO_EXPLICIT_REQUIREMENT"
BASIS_INSUFFICIENT_EVIDENCE_BASE = "INSUFFICIENT_EVIDENCE_BASE"
BASIS_OUTCOME_SATISFIED_BUT_UNGROUNDED = "OUTCOME_SATISFIED_BUT_UNGROUNDED"
BASIS_INVALID_EVALUATION = "INVALID_EVALUATION"
BASIS_EVALUATOR_UNCERTAIN = "EVALUATOR_UNCERTAIN"


def interpret_learning_outcome(
    *,
    evaluation_verdict: str,
    assurance_verdict: str,
    required_effects: list[str],
    required_targets: list[str],
    observed_effects: list[str],
    targets_satisfied: dict[str, bool],
    run_completed: bool,
    agents_completed: int,
) -> tuple[LearningOutcome, str, dict | None]:
    """Deterministic learning-outcome interpretation (schema v2.1).

    Maps a run's verified outputs to the directional signal the learner
    may consume. The evaluator verdict is NEVER rewritten: it is preserved
    on the record and returned untouched by this function.

    INVARIANT (protocol v2.1): an evaluator INCONCLUSIVE verdict may
    generate negative learning evidence ONLY through the certified
    outcome-failure path below. Absence of evidence, absence of declared
    outcomes, or evaluator uncertainty alone can NEVER constitute
    negative learning evidence.

    The certified path requires ALL of:
      1. an explicitly specified required outcome (non-empty required
         effects AND non-empty required targets, from the task's claims);
      2. execution evidence (the run completed with at least one agent
         completed: the organization had the opportunity to produce);
      3. a sufficient evidence base (the run's evidence was examined;
         required targets were positively checked, not merely absent
         from the declared outcomes);
      4. mechanical non-satisfaction: the required effects are not a
         subset of the observed effects, or at least one required
         target is not satisfied.

    When the outcome IS satisfied but the evaluator remains INCONCLUSIVE
    (e.g. produced but ungrounded), the result is EXCLUDED, not negative:
    the organization succeeded at the outcome; the uncertainty is the
    evaluator's, not the organization's failure.
    """
    av = assurance_verdict.upper()
    ev = evaluation_verdict.upper()

    # Assurance binds everything: unsound verdicts carry no signal.
    if av != "SOUND":
        return (LearningOutcome.EXCLUDED, BASIS_UNSOUND_ASSURANCE, None)

    if ev == "SUPPORTED":
        return (LearningOutcome.POSITIVE, BASIS_EVALUATOR_SUPPORTED, None)
    if ev == "FALSIFIED":
        return (LearningOutcome.NEGATIVE, BASIS_EVALUATOR_FALSIFIED, None)
    if ev in ("INVALID", "UNTESTED"):
        return (LearningOutcome.EXCLUDED, BASIS_INVALID_EVALUATION, None)
    if ev != "INCONCLUSIVE":
        return (LearningOutcome.EXCLUDED, BASIS_EVALUATOR_UNCERTAIN, None)

    # --- Certified outcome-failure path (the ONLY negative path for
    # --- INCONCLUSIVE). Each guard below is load-bearing.
    if not required_effects or not required_targets:
        # No explicitly specified required outcome: nothing to certify.
        return (LearningOutcome.EXCLUDED,
                BASIS_NO_EXPLICIT_REQUIREMENT, None)
    if not run_completed or agents_completed < 1:
        # The organization never executed: absence of evidence, not
        # evidence of outcome failure.
        return (LearningOutcome.EXCLUDED,
                BASIS_INSUFFICIENT_EVIDENCE_BASE, None)

    # Every required target must have been POSITIVELY checked (present in
    # targets_satisfied). An unchecked target defaults to nothing: it
    # excludes, it never certifies. This is what keeps "zero declared
    # outcomes" from becoming negative evidence on its own.
    if not set(required_targets) <= set(targets_satisfied):
        return (LearningOutcome.EXCLUDED,
                BASIS_INSUFFICIENT_EVIDENCE_BASE, None)

    observed = set(observed_effects)
    effect_ok = all(e in observed for e in required_effects)
    targets_ok = all(targets_satisfied.get(t, False)
                     for t in required_targets)
    satisfied = effect_ok and targets_ok

    certificate: dict = {
        "required_effects": sorted(required_effects),
        "required_targets": sorted(required_targets),
        "observed_effects": sorted(observed_effects),
        "targets_satisfied": {t: bool(targets_satisfied.get(t, False))
                              for t in sorted(required_targets)},
        "effect_satisfied": effect_ok,
        "targets_all_satisfied": targets_ok,
        "required_effect_satisfied": satisfied,
        "run_completed": bool(run_completed),
        "agents_completed": int(agents_completed),
        "evaluator_verdict_preserved": "INCONCLUSIVE",
    }
    if not satisfied:
        return (LearningOutcome.NEGATIVE_OUTCOME,
                BASIS_REQUIRED_EFFECT_NOT_SATISFIED, certificate)
    # The required outcome was produced; the evaluator's INCONCLUSIVE is
    # about grounding/evidence, not about organizational failure.
    return (LearningOutcome.EXCLUDED,
            BASIS_OUTCOME_SATISFIED_BUT_UNGROUNDED, certificate)


def eligibility_for(learning_outcome: LearningOutcome,
                   outcome_basis: str = "") -> Eligibility:
    """Eligibility from the interpreted learning outcome (schema v2.1).

    NEGATIVE_OUTCOME is directional negative: it is certified
    organizational failure, not evaluator uncertainty. Within EXCLUDED,
    untrusted inputs (unsound assurance, invalid evaluation) stay
    EXCLUDED; the rest are nondirectional.
    """
    if learning_outcome is LearningOutcome.POSITIVE:
        return Eligibility.POSITIVE
    if learning_outcome in (LearningOutcome.NEGATIVE,
                            LearningOutcome.NEGATIVE_OUTCOME):
        return Eligibility.NEGATIVE
    if outcome_basis in (BASIS_UNSOUND_ASSURANCE, BASIS_INVALID_EVALUATION):
        return Eligibility.EXCLUDED
    return Eligibility.NONDIRECTIONAL


# ---------------------------------------------------------------------------
# LearningEvidence: the ONLY thing the learner may consume.
# Deliberately has no run-status/completion field: there is no shortcut
# back to run.status == COMPLETED. Reward comes from verdicts alone.
#
# Schema v2.1: carries BOTH the evaluator's verdict (preserved exactly as
# the evaluator returned it) AND the interpreted learning outcome (the
# directional signal). The learner consumes learning_outcome; the
# evaluator_verdict is retained for audit and must never be rewritten.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LearningEvidence:
    id: str
    task_features: dict                    # class-A features only
    allocation: dict                       # strategy, scores at decision time
    organization: OrganizationProperties
    resources: dict                        # cost, latency_ms, agent_count...
    evaluation_verdict: str                # preserved evaluator verdict
    assurance_verdict: str
    learning_outcome: LearningOutcome      # interpreted signal (v2.1)
    outcome_basis: str                     # why (BASIS_* constant)
    failure_certificate: dict | None      # mechanical cert, if any
    eligibility: Eligibility               # derived from learning_outcome
    feature_schema_version: str = TASK_FEATURE_SCHEMA_VERSION
    org_property_schema_version: str = ORG_PROPERTY_SCHEMA_VERSION
    learning_schema_version: str = LEARNING_SCHEMA_VERSION

    def __post_init__(self) -> None:
        unknown = set(self.task_features) - set(TASK_FEATURES_V1)
        if unknown:
            raise ValueError(
                f"LearningEvidence carries non-class-A features {sorted(unknown)}; "
                "the learner must never see them"
            )
        # The outcome layer is coherent: NEGATIVE_OUTCOME is only ever
        # paired with its certifying basis.
        if self.learning_outcome is LearningOutcome.NEGATIVE_OUTCOME:
            if self.outcome_basis != BASIS_REQUIRED_EFFECT_NOT_SATISFIED:
                raise ValueError(
                    "NEGATIVE_OUTCOME requires basis "
                    f"{BASIS_REQUIRED_EFFECT_NOT_SATISFIED}, got "
                    f"{self.outcome_basis!r}"
                )
            if not self.failure_certificate:
                raise ValueError(
                    "NEGATIVE_OUTCOME requires a mechanical failure "
                    "certificate; refusing to certify without one"
                )

    @property
    def is_positive(self) -> bool:
        return self.eligibility is Eligibility.POSITIVE

    @property
    def is_negative(self) -> bool:
        return self.eligibility is Eligibility.NEGATIVE

    def canonical(self) -> dict:
        return {
            "id": self.id,
            "task_features": self.task_features,
            "allocation": self.allocation,
            "organization": self.organization.canonical(),
            "resources": self.resources,
            "evaluation_verdict": self.evaluation_verdict,
            "assurance_verdict": self.assurance_verdict,
            "learning_outcome": self.learning_outcome.value,
            "outcome_basis": self.outcome_basis,
            "failure_certificate": self.failure_certificate,
            "eligibility": self.eligibility.value,
            "feature_schema_version": self.feature_schema_version,
            "org_property_schema_version": self.org_property_schema_version,
            "learning_schema_version": self.learning_schema_version,
        }


# ---------------------------------------------------------------------------
# AllocationRule: WHEN / THEN / WITH.
# ---------------------------------------------------------------------------

class RuleAction(str, Enum):
    REQUIRE_CAPABILITY = "require_capability"
    REQUIRE_ROLE = "require_role"
    BOOST_STRATEGY = "boost_strategy"
    PENALIZE_STRATEGY = "penalize_strategy"


MAX_STRATEGY_DELTA = 0.3  # hard constraint on scoring adjustments


@dataclass(frozen=True)
class RuleThen:
    action: RuleAction
    target: str          # capability / role / strategy name
    delta: float = 0.0   # only for boost/penalize

    def __post_init__(self) -> None:
        if self.action in (RuleAction.BOOST_STRATEGY,
                           RuleAction.PENALIZE_STRATEGY):
            if not -MAX_STRATEGY_DELTA <= self.delta <= MAX_STRATEGY_DELTA:
                raise ValueError(
                    f"strategy delta {self.delta} exceeds hard bound "
                    f"+/-{MAX_STRATEGY_DELTA}"
                )
            if self.action is RuleAction.BOOST_STRATEGY and self.delta < 0:
                raise ValueError("boost_strategy requires delta >= 0")
            if self.action is RuleAction.PENALIZE_STRATEGY and self.delta > 0:
                raise ValueError("penalize_strategy requires delta <= 0")
        elif self.delta != 0.0:
            raise ValueError("delta is only meaningful for strategy actions")

    def canonical(self) -> dict:
        return {"action": self.action.value, "target": self.target,
                "delta": self.delta}


@dataclass(frozen=True)
class AllocationRule:
    """WHEN conditions hold (and scope covers the task) THEN apply the
    action. WITH carries evidence, strength, uncertainty, constraints,
    priority. Immutable; identified by rule_hash."""

    id: str
    when: tuple            # tuple[AtomicCondition, ...], conjunction
    then: RuleThen
    scope: tuple           # tuple[AtomicCondition, ...]; empty = universal
    evidence: tuple        # tuple of LearningEvidence ids (eligible only)
    evidence_strength: float
    uncertainty: dict      # effect estimate, CI, n, overlap stats, p-value
    constraints: dict
    priority: int
    rule_schema_version: str = RULE_SCHEMA_VERSION
    feature_schema_version: str = TASK_FEATURE_SCHEMA_VERSION
    org_property_schema_version: str = ORG_PROPERTY_SCHEMA_VERSION
    policy_semantics_version: str = POLICY_SEMANTICS_VERSION

    def __post_init__(self) -> None:
        if not self.evidence:
            raise ValueError("a rule with no eligible evidence cannot exist")
        if not self.when:
            raise ValueError("a rule with an empty WHEN is unconditional; "
                             "refusing: every rule must be task-conditioned")

    def scope_covers(self, features: dict) -> bool:
        return all(c.holds(features) for c in self.scope)

    def fires(self, features: dict) -> bool:
        return self.scope_covers(features) and all(
            c.holds(features) for c in self.when)

    @property
    def is_hard_requirement(self) -> bool:
        return self.then.action in (RuleAction.REQUIRE_CAPABILITY,
                                   RuleAction.REQUIRE_ROLE)

    @property
    def rule_hash(self) -> str:
        return canonical_hash({
            "when": [c.canonical() for c in self.when],
            "then": self.then.canonical(),
            "scope": [c.canonical() for c in self.scope],
            "rule_schema_version": self.rule_schema_version,
            "feature_schema_version": self.feature_schema_version,
            "org_property_schema_version": self.org_property_schema_version,
            "policy_semantics_version": self.policy_semantics_version,
        })

    def canonical(self) -> dict:
        return {
            "id": self.id,
            "when": [c.canonical() for c in self.when],
            "then": self.then.canonical(),
            "scope": [c.canonical() for c in self.scope],
            "evidence": sorted(self.evidence),
            "evidence_strength": self.evidence_strength,
            "uncertainty": self.uncertainty,
            "constraints": self.constraints,
            "priority": self.priority,
            "rule_hash": self.rule_hash,
            "rule_schema_version": self.rule_schema_version,
            "feature_schema_version": self.feature_schema_version,
            "org_property_schema_version": self.org_property_schema_version,
            "policy_semantics_version": self.policy_semantics_version,
        }


def scope_specificity(rule: AllocationRule) -> int:
    """Narrower scope (more conditions) wins ties after priority."""
    return len(rule.scope) + len(rule.when)


def resolve_rules(rules: list[AllocationRule], features: dict) -> dict:
    """Deterministic conflict resolution for one allocation decision.

    Order: constraints (vetoes, enforced at construction) ->
    hard requirements -> scoring adjustments -> priority ->
    scope specificity. Returns the applicable requirements, the net
    score adjustments, and the resolution log (recorded on every
    allocation record for inspectability).
    """
    applicable = [r for r in rules if r.fires(features)]
    # Deterministic order: priority desc, then narrower scope first,
    # then rule id for full determinism.
    applicable.sort(key=lambda r: (-r.priority, -scope_specificity(r), r.id))
    requirements: list[RuleThen] = []
    adjustments: dict[str, float] = {}
    log: list[dict] = []
    for r in applicable:
        if r.is_hard_requirement:
            requirements.append(r.then)
            log.append({"rule": r.id, "rule_hash": r.rule_hash,
                        "applied": "requirement",
                        "action": r.then.action.value,
                        "target": r.then.target})
        else:
            adjustments[r.then.target] = round(
                adjustments.get(r.then.target, 0.0) + r.then.delta, 4)
            log.append({"rule": r.id, "rule_hash": r.rule_hash,
                        "applied": "score_adjustment",
                        "action": r.then.action.value,
                        "target": r.then.target, "delta": r.then.delta})
    return {"requirements": requirements, "adjustments": adjustments,
            "applicable_rules": [r.id for r in applicable], "log": log}


# ---------------------------------------------------------------------------
# PolicyCandidate: the unit the learner hands to evaluation. Never touches
# the allocator; promotion is a separate, gated step (promotion.py).
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PolicyCandidate:
    id: str
    parent_version: str
    rules: tuple                        # tuple[AllocationRule, ...]
    hypothesis: str
    source_experiences: tuple           # LearningEvidence ids
    attribution_ids: tuple
    candidate_hash: str = ""

    def __post_init__(self) -> None:
        if not self.rules:
            raise ValueError("a candidate with no rules is vacuous; "
                             "refusing to evaluate it")
        # Candidate hash pins the full rule set deterministically.
        object.__setattr__(
            self, "candidate_hash",
            canonical_hash({
                "rules": sorted(r.rule_hash for r in self.rules),
                "parent_version": self.parent_version,
                "rule_schema_version": RULE_SCHEMA_VERSION,
                "policy_semantics_version": POLICY_SEMANTICS_VERSION,
            }))

    def canonical(self) -> dict:
        return {
            "id": self.id,
            "parent_version": self.parent_version,
            "rules": [r.canonical() for r in self.rules],
            "hypothesis": self.hypothesis,
            "source_experiences": sorted(self.source_experiences),
            "attribution_ids": sorted(self.attribution_ids),
            "candidate_hash": self.candidate_hash,
        }


# ---------------------------------------------------------------------------
# ExperienceAttribution: conditional association, never a causal claim.
# INSUFFICIENT_OVERLAP is a first-class outcome, not an exception.
# ---------------------------------------------------------------------------

class AttributionOutcome(str, Enum):
    ASSOCIATION = "association"
    INSUFFICIENT_OVERLAP = "insufficient_overlap"
    NO_DIFFERENTIAL = "no_differential"


@dataclass(frozen=True)
class ExperienceAttribution:
    id: str
    stratum: tuple          # tuple[AtomicCondition, ...] describing the stratum
    property_dimension: str  # strategy | role | capability | topology
    property_value: str       # e.g. "parallel_agents", "producer", "WRITE"
    outcome: AttributionOutcome
    differential: float = 0.0        # treated verified rate - untreated
    treated_n: int = 0
    untreated_n: int = 0
    treated_positives: int = 0
    treated_negatives: int = 0
    untreated_positives: int = 0
    untreated_negatives: int = 0
    evidence_ids: tuple = ()
    robust: bool = True
    robustness_notes: str = ""
    notes: str = ""

    def canonical(self) -> dict:
        return {
            "id": self.id,
            "stratum": [c.canonical() for c in self.stratum],
            "property_dimension": self.property_dimension,
            "property_value": self.property_value,
            "outcome": self.outcome.value,
            "differential": self.differential,
            "treated_n": self.treated_n,
            "untreated_n": self.untreated_n,
            "treated_positives": self.treated_positives,
            "treated_negatives": self.treated_negatives,
            "untreated_positives": self.untreated_positives,
            "untreated_negatives": self.untreated_negatives,
            "evidence_ids": sorted(self.evidence_ids),
            "robust": self.robust,
            "robustness_notes": self.robustness_notes,
            "notes": self.notes,
        }


# ---------------------------------------------------------------------------
# DiscriminatingEvaluation and PromotionDecision (populated by
# evaluation.py / promotion.py; defined here so the contracts are one
# import).
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DiscriminatingEvaluation:
    id: str
    candidate_id: str
    parent_version: str
    sealed_pool_id: str
    decision_delta_task_ids: tuple   # tasks where parent/candidate differ
    paired_outcomes: tuple            # tuple of dicts, one per task
    statistics: dict                  # McNemar/Fisher, non-inferiority, costs
    verdict: str                      # PASS | FAIL | VACUOUS
    evaluator_version: str = "discriminating-eval/v1"

    def canonical(self) -> dict:
        return {
            "id": self.id,
            "candidate_id": self.candidate_id,
            "parent_version": self.parent_version,
            "sealed_pool_id": self.sealed_pool_id,
            "decision_delta_task_ids": sorted(self.decision_delta_task_ids),
            "paired_outcomes": [dict(sorted(p.items()))
                                for p in self.paired_outcomes],
            "statistics": self.statistics,
            "verdict": self.verdict,
            "evaluator_version": self.evaluator_version,
        }


@dataclass(frozen=True)
class PolicyVersionV2:
    """The promotable artifact. Built by promotion, activated ONLY by the
    allocator side. The learner never holds a reference to the active
    allocator and never calls activate: the path is Learner -> Candidate
    -> Evaluation -> Assurance -> Promotion -> PolicyVersion -> Allocator."""

    id: str
    version: str
    parent_version: str
    rules: tuple
    promotion_decision_id: str
    evaluation_id: str
    assurance_id: str
    source_experiences: tuple
    created_at: str = ""

    def canonical(self) -> dict:
        return {
            "id": self.id,
            "version": self.version,
            "parent_version": self.parent_version,
            "rules": [r.canonical() for r in self.rules],
            "promotion_decision_id": self.promotion_decision_id,
            "evaluation_id": self.evaluation_id,
            "assurance_id": self.assurance_id,
            "source_experiences": sorted(self.source_experiences),
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class PromotionDecision:
    id: str
    candidate_id: str
    decision: str                      # PROMOTE | REJECT
    reasons: tuple
    evaluation_id: str
    assurance_id: str
    assurance_verdict: str
    policy_version: PolicyVersionV2 | None = None  # set only on PROMOTE

    def __post_init__(self) -> None:
        if self.decision == "PROMOTE" and self.policy_version is None:
            raise ValueError("PROMOTE without a PolicyVersion is incoherent")
        if self.decision != "PROMOTE" and self.policy_version is not None:
            raise ValueError("REJECT must not carry a PolicyVersion")

    def canonical(self) -> dict:
        return {
            "id": self.id,
            "candidate_id": self.candidate_id,
            "decision": self.decision,
            "reasons": list(self.reasons),
            "evaluation_id": self.evaluation_id,
            "assurance_id": self.assurance_id,
            "assurance_verdict": self.assurance_verdict,
            "policy_version": (self.policy_version.canonical()
                               if self.policy_version else None),
        }
