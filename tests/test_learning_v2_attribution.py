"""Phase 3: contrastive attribution. Associations only; causal claims
never. INSUFFICIENT_OVERLAP is first-class."""

from air.learning_v2 import attribution, extraction
from air.learning_v2.contracts import AttributionOutcome


def _feats(**kw):
    base = {
        "requires_output_artifact": True, "output_artifact_count": 3,
        "has_named_output_targets": True, "requested_write_effect": True,
        "requested_read_effect": False, "requested_execute_effect": False,
        "independent_work_unit_count": 3, "dependency_depth": 1,
        "multi_step": True,
    }
    base.update(kw)
    return base


def _org(strategy, caps):
    roles = {"direct": ["generalist"], "parallel_agents": ["researcher"],
             "hierarchical": ["coordinator"]}[strategy]
    return {"strategy": strategy, "roles": roles,
            "capabilities": caps, "topology": "t"}


def _rec(rid, feats, strategy, caps, ev, av="SOUND"):
    return extraction.extract_experience(
        id=rid, task_features=feats,
        allocation={"strategy": strategy},
        organization=_org(strategy, caps),
        resources={"cost": 1.0},
        evaluation_verdict=ev, assurance_verdict=av)


def _contrast_records():
    # Production stratum: WRITE present -> SUPPORTED, absent -> FALSIFIED.
    recs = []
    for i in range(4):
        recs.append(_rec(f"p_dir_{i}", _feats(), "direct",
                         ["READ", "WRITE"], "SUPPORTED"))
        recs.append(_rec(f"p_par_{i}", _feats(), "parallel_agents",
                         ["READ"], "FALSIFIED"))
    # Research stratum: everything succeeds (no contrast possible there).
    for i in range(4):
        recs.append(_rec(f"r_dir_{i}", _feats(output_artifact_count=0,
                                             requested_write_effect=False),
                         "direct", ["READ", "WRITE"], "SUPPORTED"))
        recs.append(_rec(f"r_par_{i}", _feats(output_artifact_count=0,
                                             requested_write_effect=False),
                         "parallel_agents", ["READ"], "SUPPORTED"))
    return recs


def test_known_contrast_yields_association():
    attrs = attribution.attribute(_contrast_records())
    by_key = {(a.stratum[0].feature, a.stratum[0].value,
               a.property_dimension, a.property_value): a for a in attrs}
    a = by_key[("output_artifact_count", 1, "capability", "WRITE")]
    assert a.outcome is AttributionOutcome.ASSOCIATION
    assert a.differential == 1.0
    assert a.treated_n == 4 and a.untreated_n == 4
    assert a.robust


def test_one_sided_data_is_insufficient_overlap_not_causal():
    # D1-like: parallel chosen for every production task, all fail.
    # No untreated observations -> no contrastive claim licensed.
    recs = [_rec(f"d1_{i}", _feats(), "parallel_agents", ["READ"],
                 "FALSIFIED") for i in range(6)]
    attrs = attribution.attribute(recs)
    assert attrs, "absence of evidence must be recorded, not dropped"
    for a in attrs:
        assert a.outcome is AttributionOutcome.INSUFFICIENT_OVERLAP
        assert "P CAUSED" not in a.notes.upper()


def test_confounded_association_marked_not_robust():
    # multi_step is perfectly confounded with the production stratum
    # here: the WRITE association under multi_step does not survive
    # conditioning on output_artifact_count.
    recs = _contrast_records()
    attrs = attribution.attribute(recs)
    by_key = {(a.stratum[0].feature, a.property_dimension,
               a.property_value): a for a in attrs}
    a = by_key[("multi_step", "capability", "WRITE")]
    assert a.outcome is AttributionOutcome.ASSOCIATION
    assert not a.robust
    assert "CONFOUNDED" in a.robustness_notes


def test_no_differential_recorded():
    # Research stratum with both outcomes present but identical rates
    # across the property: a genuine null, recorded as such.
    recs = []
    for i in range(2):
        recs.append(_rec(f"r_dir_pos_{i}", _feats(output_artifact_count=0),
                         "direct", ["READ", "WRITE"], "SUPPORTED"))
        recs.append(_rec(f"r_par_pos_{i}", _feats(output_artifact_count=0),
                         "parallel_agents", ["READ"], "SUPPORTED"))
    recs.append(_rec("r_dir_neg", _feats(output_artifact_count=0),
                     "direct", ["READ", "WRITE"], "FALSIFIED"))
    recs.append(_rec("r_par_neg", _feats(output_artifact_count=0),
                     "parallel_agents", ["READ"], "FALSIFIED"))
    attrs = attribution.attribute(recs)
    by_key = {(a.stratum[0].feature, a.property_dimension,
               a.property_value): a for a in attrs}
    a = by_key[("output_artifact_count", "capability", "WRITE")]
    assert a.outcome is AttributionOutcome.NO_DIFFERENTIAL
    assert a.differential == 0.0


def test_attribution_deterministic():
    recs = _contrast_records()
    first = [(a.id, a.outcome.value, a.differential, a.robust)
             for a in attribution.attribute(recs)]
    second = [(a.id, a.outcome.value, a.differential, a.robust)
              for a in attribution.attribute(recs)]
    assert first == second
