"""Phase 2: verdict-aware extraction. The learner must be structurally
incapable of consuming run completion status as reward."""

import pytest

from air.learning_v2 import extraction
from air.learning_v2.contracts import Eligibility

FEATS = {
    "requires_output_artifact": True, "output_artifact_count": 3,
    "has_named_output_targets": True, "requested_write_effect": True,
    "requested_read_effect": False, "requested_execute_effect": False,
    "independent_work_unit_count": 3, "dependency_depth": 1,
    "multi_step": True,
}
ORG = {"strategy": "direct", "roles": ["generalist"],
       "capabilities": ["READ", "WRITE"], "topology": "single"}


def _extract(ev="SUPPORTED", av="SOUND", feats=None):
    return extraction.extract_experience(
        id="exp_1", task_features=feats or dict(FEATS),
        allocation={"strategy": "direct"}, organization=dict(ORG),
        resources={"cost": 1.0}, evaluation_verdict=ev,
        assurance_verdict=av)


def test_full_eligibility_matrix():
    assert _extract("SUPPORTED", "SOUND").eligibility is Eligibility.POSITIVE
    assert _extract("FALSIFIED", "SOUND").eligibility is Eligibility.NEGATIVE
    assert _extract("SUPPORTED", "UNSOUND").eligibility is Eligibility.EXCLUDED
    assert _extract("FALSIFIED", "UNSOUND").eligibility is Eligibility.EXCLUDED
    assert _extract("INCONCLUSIVE", "SOUND").eligibility is \
        Eligibility.NONDIRECTIONAL
    assert _extract("INVALID", "SOUND").eligibility is Eligibility.EXCLUDED
    assert _extract("UNTESTED", "SOUND").eligibility is Eligibility.EXCLUDED


def test_assurance_verdict_mapping():
    # Runtime EvaluatorVerdict -> contract domain.
    assert _extract("SUPPORTED", "EXPLOITABLE").eligibility is \
        Eligibility.EXCLUDED
    assert _extract("SUPPORTED", "UNRELIABLE").eligibility is \
        Eligibility.EXCLUDED
    assert _extract("SUPPORTED", "OVER_STRICT").eligibility is \
        Eligibility.EXCLUDED  # INCONCLUSIVE assurance: no directional signal
    assert _extract("SUPPORTED", "UNRESPONSIVE").eligibility is \
        Eligibility.EXCLUDED
    with pytest.raises(ValueError):
        _extract("SUPPORTED", "MAYBE")


def test_rejects_benchmark_labels_in_features():
    bad = dict(FEATS, kind="write_multi")
    with pytest.raises(ValueError):
        _extract(feats=bad)
    bad2 = dict(FEATS, declared_kind="write_multi")
    with pytest.raises(ValueError):
        _extract(feats=bad2)


def test_no_completion_shortcut_structurally():
    # LearningEvidence has no run-status field at all: there is no
    # shortcut back to run.status == COMPLETED.
    ev = _extract()
    assert not hasattr(ev, "run_status")
    assert not hasattr(ev, "status")
    assert not hasattr(ev, "outcome")
    assert not hasattr(ev, "completed")
    with pytest.raises(TypeError):
        extraction.extract_experience(
            id="x", task_features=dict(FEATS), allocation={},
            organization=dict(ORG), resources={},
            evaluation_verdict="SUPPORTED", assurance_verdict="SOUND",
            run_status="COMPLETED")


def test_eligible_for_learning_filters():
    recs = [_extract("SUPPORTED", "SOUND"),
            _extract("FALSIFIED", "SOUND"),
            _extract("INCONCLUSIVE", "SOUND"),
            _extract("SUPPORTED", "UNSOUND"),
            _extract("INVALID", "SOUND")]
    kept = extraction.eligible_for_learning(recs)
    assert [r.eligibility for r in kept] == [Eligibility.POSITIVE,
                                            Eligibility.NEGATIVE]
