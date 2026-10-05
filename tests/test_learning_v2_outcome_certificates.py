"""Schema v2.1: outcome-failure certificates.

Tests the protocol v2.1 invariant: an evaluator INCONCLUSIVE verdict may
generate negative learning evidence ONLY through a separately defined,
deterministic outcome-failure certificate. Absence of evidence, absence
of declared outcomes, or evaluator uncertainty alone can NEVER
constitute negative learning evidence.
"""

import pytest

from air.learning_v2 import extraction
from air.learning_v2.contracts import (
    BASIS_REQUIRED_EFFECT_NOT_SATISFIED,
    Eligibility,
    LearningEvidence,
    LearningOutcome,
    interpret_learning_outcome,
)

FEATS = {
    "requires_output_artifact": True, "output_artifact_count": 3,
    "has_named_output_targets": True, "requested_write_effect": True,
    "requested_read_effect": False, "requested_execute_effect": False,
    "independent_work_unit_count": 3, "dependency_depth": 1,
    "multi_step": True,
}
ORG = {"strategy": "parallel_agents", "roles": ["researcher", "synthesizer"],
       "capabilities": ["READ"], "topology": "parallel"}


def _certify(**kw):
    """interpret_learning_outcome with the parallel-on-production shape."""
    args = dict(
        evaluation_verdict="INCONCLUSIVE",
        assurance_verdict="SOUND",
        required_effects=["create"],
        required_targets=["q1.txt", "q2.txt", "q3.txt"],
        observed_effects=[],
        targets_satisfied={"q1.txt": False, "q2.txt": False,
                           "q3.txt": False},
        run_completed=True,
        agents_completed=4,
    )
    args.update(kw)
    return interpret_learning_outcome(**args)


def test_certified_outcome_failure_is_negative():
    outcome, basis, cert = _certify()
    assert outcome is LearningOutcome.NEGATIVE_OUTCOME
    assert basis == BASIS_REQUIRED_EFFECT_NOT_SATISFIED
    assert cert is not None
    assert cert["required_effect_satisfied"] is False
    assert cert["evaluator_verdict_preserved"] == "INCONCLUSIVE"


def test_evaluator_verdict_never_rewritten():
    # The interpretation returns the outcome separately; the evaluator's
    # verdict is preserved on the record, not mutated.
    ev = extraction.extract_experience(
        id="e1", task_features=dict(FEATS), allocation={"strategy": "x"},
        organization=dict(ORG), resources={},
        evaluation_verdict="INCONCLUSIVE", assurance_verdict="SOUND",
        required_effects=["create"],
        required_targets=["q1.txt"],
        observed_effects=[],
        targets_satisfied={"q1.txt": False},
        run_completed=True, agents_completed=2)
    assert ev.evaluation_verdict == "INCONCLUSIVE"
    assert ev.learning_outcome is LearningOutcome.NEGATIVE_OUTCOME
    assert ev.eligibility is Eligibility.NEGATIVE
    assert ev.failure_certificate is not None


def test_zero_outcomes_alone_is_insufficient():
    # No execution evidence: the run never completed. Absence of evidence
    # must not become negative evidence.
    outcome, basis, cert = _certify(run_completed=False,
                                    agents_completed=0)
    assert outcome is LearningOutcome.EXCLUDED
    assert cert is None


def test_unchecked_targets_cannot_certify():
    # The harness did not positively check the required targets.
    # targets_satisfied missing a target -> EXCLUDED, not negative.
    outcome, basis, cert = _certify(
        targets_satisfied={"q1.txt": False})  # q2, q3 unchecked
    assert outcome is LearningOutcome.EXCLUDED
    assert cert is None


def test_no_explicit_requirement_cannot_certify():
    # Read-only task: no required production targets. INCONCLUSIVE stays
    # nondirectional.
    outcome, basis, cert = _certify(required_effects=[],
                                    required_targets=[])
    assert outcome is LearningOutcome.EXCLUDED
    assert cert is None


def test_satisfied_outcome_with_inconclusive_is_excluded():
    # The organization PRODUCED the required outcome (files exist, effect
    # observed) but the evaluator is INCONCLUSIVE about grounding. That
    # is evaluator uncertainty, not organizational failure.
    outcome, basis, cert = _certify(
        observed_effects=["create"],
        targets_satisfied={"q1.txt": True, "q2.txt": True,
                           "q3.txt": True})
    assert outcome is LearningOutcome.EXCLUDED
    assert basis == "OUTCOME_SATISFIED_BUT_UNGROUNDED"
    assert cert is not None
    assert cert["required_effect_satisfied"] is True


def test_soundness_still_binds():
    # UNSOUND assurance blocks even a perfect certificate.
    outcome, basis, cert = _certify(assurance_verdict="UNSOUND")
    assert outcome is LearningOutcome.EXCLUDED
    assert cert is None


def test_supported_and_falsified_paths_unchanged():
    o, b, c = interpret_learning_outcome(
        evaluation_verdict="SUPPORTED", assurance_verdict="SOUND",
        required_effects=[], required_targets=[], observed_effects=[],
        targets_satisfied={}, run_completed=True, agents_completed=1)
    assert o is LearningOutcome.POSITIVE
    o, b, c = interpret_learning_outcome(
        evaluation_verdict="FALSIFIED", assurance_verdict="SOUND",
        required_effects=[], required_targets=[], observed_effects=[],
        targets_satisfied={}, run_completed=True, agents_completed=1)
    assert o is LearningOutcome.NEGATIVE


def test_negative_outcome_requires_certifying_basis():
    # Structural coherence: NEGATIVE_OUTCOME cannot be constructed with
    # any other basis, and cannot exist without a certificate.
    with pytest.raises(ValueError):
        LearningEvidence(
            id="bad", task_features=dict(FEATS), allocation={},
            organization={"strategy": "x"}, resources={},
            evaluation_verdict="INCONCLUSIVE", assurance_verdict="SOUND",
            learning_outcome=LearningOutcome.NEGATIVE_OUTCOME,
            outcome_basis="EVALUATOR_UNCERTAIN",
            failure_certificate={"required_effect_satisfied": False},
            eligibility=Eligibility.NEGATIVE)
    with pytest.raises(ValueError):
        LearningEvidence(
            id="bad2", task_features=dict(FEATS), allocation={},
            organization={"strategy": "x"}, resources={},
            evaluation_verdict="INCONCLUSIVE", assurance_verdict="SOUND",
            learning_outcome=LearningOutcome.NEGATIVE_OUTCOME,
            outcome_basis=BASIS_REQUIRED_EFFECT_NOT_SATISFIED,
            failure_certificate=None,
            eligibility=Eligibility.NEGATIVE)


def test_certified_negatives_are_eligible_for_learning():
    pos = extraction.extract_experience(
        id="p", task_features=dict(FEATS), allocation={}, 
        organization=dict(ORG), resources={},
        evaluation_verdict="SUPPORTED", assurance_verdict="SOUND")
    neg = extraction.extract_experience(
        id="n", task_features=dict(FEATS), allocation={},
        organization=dict(ORG), resources={},
        evaluation_verdict="INCONCLUSIVE", assurance_verdict="SOUND",
        required_effects=["create"], required_targets=["q1.txt"],
        observed_effects=[], targets_satisfied={"q1.txt": False},
        run_completed=True, agents_completed=2)
    excl = extraction.extract_experience(
        id="x", task_features=dict(FEATS), allocation={},
        organization=dict(ORG), resources={},
        evaluation_verdict="INCONCLUSIVE", assurance_verdict="SOUND")
    kept = extraction.eligible_for_learning([pos, neg, excl])
    assert [r.id for r in kept] == ["p", "n"]
    assert kept[1].learning_outcome is LearningOutcome.NEGATIVE_OUTCOME
