"""Phase 4: rule generation by enumeration. Fixed mapping, no search."""

import pytest

from air.learning_v2 import attribution, extraction, generation
from air.learning_v2.contracts import (
    AttributionOutcome,
    Operator,
    RuleAction,
)


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


def _rec(rid, feats, strategy, caps, roles, ev):
    return extraction.extract_experience(
        id=rid, task_features=feats, allocation={"strategy": strategy},
        organization={"strategy": strategy, "roles": roles,
                      "capabilities": caps, "topology": "t"},
        resources={"cost": 1.0},
        evaluation_verdict=ev, assurance_verdict="SOUND")


def _records():
    recs = []
    for i in range(4):
        recs.append(_rec(f"d_{i}", _feats(), "direct",
                         ["READ", "WRITE"], ["generalist"], "SUPPORTED"))
        recs.append(_rec(f"p_{i}", _feats(), "parallel_agents",
                         ["READ"], ["researcher"], "FALSIFIED"))
    return recs


def test_mapping_capability_role_strategy():
    attrs = attribution.attribute(_records())
    rules = generation.generate_rules(attrs)
    by_action = {}
    for r in rules:
        by_action.setdefault((r.then.action, r.then.target), r)
    # capability WRITE positive -> require_capability
    assert (RuleAction.REQUIRE_CAPABILITY, "WRITE") in by_action
    # strategy parallel_agents negative -> penalize
    r = by_action[(RuleAction.PENALIZE_STRATEGY, "parallel_agents")]
    assert r.then.delta == -generation.STRATEGY_DELTA
    # strategy direct positive -> boost
    r = by_action[(RuleAction.BOOST_STRATEGY, "direct")]
    assert r.then.delta == generation.STRATEGY_DELTA
    # every rule cites eligible evidence
    for r in rules:
        assert len(r.evidence) > 0
        assert r.evidence_strength > 0


def _rec_topo(rid, feats, strategy, caps, roles, topo, ev):
    return extraction.extract_experience(
        id=rid, task_features=feats, allocation={"strategy": strategy},
        organization={"strategy": strategy, "roles": roles,
                      "capabilities": caps, "topology": topo},
        resources={"cost": 1.0},
        evaluation_verdict=ev, assurance_verdict="SOUND")


def test_topology_and_negative_role_generate_no_rule():
    recs = []
    for i in range(4):
        recs.append(_rec_topo(f"d_{i}", _feats(), "direct",
                              ["READ", "WRITE"], ["generalist"],
                              "single", "SUPPORTED"))
        recs.append(_rec_topo(f"p_{i}", _feats(), "parallel_agents",
                              ["READ"], ["researcher"], "flat",
                              "FALSIFIED"))
    attrs = attribution.attribute(recs)
    topo = [a for a in attrs
            if a.outcome is AttributionOutcome.ASSOCIATION
            and a.property_dimension == "topology"]
    assert topo, "expected topology associations to exist"
    rules = generation.generate_rules(attrs)
    # v1 has no topology action: associations are reported, never rules.
    assert not [r for r in rules if r.constraints["attribution_id"] in
                {a.id for a in topo}]
    # negative role associations (researcher worse) -> no forbid action.
    assert not [r for r in rules if r.then.action is
                RuleAction.REQUIRE_ROLE and r.then.target == "researcher"]


def test_confounded_attributions_generate_no_rule():
    attrs = attribution.attribute(_records())
    confounded = [a for a in attrs
                  if a.outcome is AttributionOutcome.ASSOCIATION
                  and not a.robust]
    rules = generation.generate_rules(attrs)
    rule_attr_ids = {r.constraints["attribution_id"] for r in rules}
    for a in confounded:
        assert a.id not in rule_attr_ids


def test_evidence_strength_deterministic_versioned():
    s1 = generation.evidence_strength(1.0, 8)
    s2 = generation.evidence_strength(1.0, 8)
    assert s1 == s2
    assert generation.evidence_strength(1.0, 80) > s1  # more evidence
    assert generation.evidence_strength(0.5, 8) < s1   # smaller effect


def test_fisher_exact_sanity():
    # Perfect association: small p.
    assert generation.fisher_exact_2x2(8, 0, 0, 8) < 0.001
    # No association: p = 1.
    assert generation.fisher_exact_2x2(4, 4, 4, 4) == 1.0


def test_build_candidate_refuses_vacuous():
    with pytest.raises(ValueError):
        generation.build_candidate(id="c", parent_version="v1",
                                   hypothesis="h", rules=[],
                                   attributions=[])


def test_generation_deterministic():
    attrs = attribution.attribute(_records())
    r1 = [r.rule_hash for r in generation.generate_rules(attrs)]
    r2 = [r.rule_hash for r in generation.generate_rules(attrs)]
    assert r1 == r2
