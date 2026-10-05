"""Phase 2: verdict-aware experience extraction.

The learner consumes LearningEvidence records, never raw run status.
This module is the ONLY bridge from the runtime's evaluation/assurance
outputs to the learner, and its signature structurally excludes any
completion signal: there is no run_status parameter, no outcome field
that could smuggle run.status == COMPLETED back in as reward.

Verdict mapping (runtime -> contract domain):
- evaluation: air.evaluation.suites.Verdict values pass through
  (SUPPORTED/FALSIFIED/INCONCLUSIVE/INVALID/UNTESTED).
- assurance: air.assurance.probes.EvaluatorVerdict maps to the
  contract's SOUND | UNSOUND | INCONCLUSIVE | NOT_RUN:
      SOUND        -> SOUND
      EXPLOITABLE  -> UNSOUND   (the evaluator can be gamed)
      UNRELIABLE   -> UNSOUND   (the evaluator cannot be trusted)
      OVER_STRICT  -> INCONCLUSIVE (verdicts carry no directional signal)
      UNRESPONSIVE -> NOT_RUN
"""

from __future__ import annotations

from air.learning_v2.contracts import (
    Eligibility,
    LearningEvidence,
    LearningOutcome,
    OrganizationProperties,
    TASK_FEATURES_V1,
    eligibility_for,
    interpret_learning_outcome,
)

_ASSURANCE_MAP = {
    "SOUND": "SOUND",
    "EXPLOITABLE": "UNSOUND",
    "UNRELIABLE": "UNSOUND",
    "OVER_STRICT": "INCONCLUSIVE",
    "UNRESPONSIVE": "NOT_RUN",
    # Contract-domain values pass through unchanged.
    "UNSOUND": "UNSOUND",
    "INCONCLUSIVE": "INCONCLUSIVE",
    "NOT_RUN": "NOT_RUN",
}


def map_assurance_verdict(runtime_verdict: str) -> str:
    try:
        return _ASSURANCE_MAP[runtime_verdict.upper()]
    except KeyError:
        raise ValueError(
            f"unknown assurance verdict {runtime_verdict!r}; refusing to "
            "guess its learning semantics"
        )


def extract_experience(
    *,
    id: str,
    task_features: dict,
    allocation: dict,
    organization: dict,
    resources: dict,
    evaluation_verdict: str,
    assurance_verdict: str,
    # --- Schema v2.1 outcome-certification inputs. When omitted, the
    # --- certified path is unavailable and INCONCLUSIVE stays EXCLUDED.
    required_effects: list[str] | None = None,
    required_targets: list[str] | None = None,
    observed_effects: list[str] | None = None,
    targets_satisfied: dict[str, bool] | None = None,
    run_completed: bool = False,
    agents_completed: int = 0,
) -> LearningEvidence:
    """Build one LearningEvidence record from verified outputs.

    Parameters are the verified outcome of a run: class-A task features,
    the allocation decision, the organization that executed, measured
    resources, and the evaluation + assurance verdicts. Run completion
    status is not accepted and not represented.

    Schema v2.1: the optional certification inputs let the extractor
    mechanically establish required-outcome non-satisfaction (see
    interpret_learning_outcome). The evaluator verdict is preserved
    exactly; the learning outcome is a separate interpreted layer.
    """
    unknown = set(task_features) - set(TASK_FEATURES_V1)
    if unknown:
        raise ValueError(
            f"task carries non-class-A features {sorted(unknown)}; "
            "extraction refuses to launder benchmark labels into the "
            "learner's hypothesis space"
        )
    ev = evaluation_verdict.upper()
    if ev not in ("SUPPORTED", "FALSIFIED", "INCONCLUSIVE", "INVALID",
                  "UNTESTED"):
        raise ValueError(f"unknown evaluation verdict {evaluation_verdict!r}")
    av = map_assurance_verdict(assurance_verdict)
    org = OrganizationProperties(
        strategy=str(organization.get("strategy", "unknown")),
        roles=frozenset(organization.get("roles", ())),
        capabilities=frozenset(organization.get("capabilities", ())),
        topology=str(organization.get("topology", "unknown")),
    )
    outcome, basis, certificate = interpret_learning_outcome(
        evaluation_verdict=ev,
        assurance_verdict=av,
        required_effects=list(required_effects or []),
        required_targets=list(required_targets or []),
        observed_effects=list(observed_effects or []),
        targets_satisfied=dict(targets_satisfied or {}),
        run_completed=bool(run_completed),
        agents_completed=int(agents_completed),
    )
    return LearningEvidence(
        id=id,
        task_features=dict(task_features),
        allocation=dict(allocation),
        organization=org,
        resources=dict(resources),
        evaluation_verdict=ev,
        assurance_verdict=av,
        learning_outcome=outcome,
        outcome_basis=basis,
        failure_certificate=certificate,
        eligibility=eligibility_for(outcome, basis),
    )


def eligible_for_learning(records: list[LearningEvidence]) -> list[LearningEvidence]:
    """The learning dataset: only records with directional eligibility.
    INCONCLUSIVE without a certified outcome-failure is nondirectional;
    INVALID/UNTESTED and anything from an unsound evaluator are excluded
    outright. Certified NEGATIVE_OUTCOME records are directional negative."""
    return [r for r in records
            if r.eligibility in (Eligibility.POSITIVE, Eligibility.NEGATIVE)]
