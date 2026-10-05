"""Phase 1 contracts: schemas, AllocationRule, canonical hashing."""

import pytest

from air.learning_v2.contracts import (
    AllocationRule,
    AtomicCondition,
    Eligibility,
    LearningEvidence,
    LearningOutcome,
    Operator,
    OrganizationProperties,
    PolicyCandidate,
    PromotionDecision,
    RuleAction,
    RuleThen,
    canonical_hash,
    eligibility_for,
    interpret_learning_outcome,
    resolve_rules,
)


def _cond(feature="requested_write_effect", op=Operator.EQ, value=True):
    return AtomicCondition(feature, op, value)


def _rule(rule_id="rule_0001", when=None, action=RuleAction.REQUIRE_CAPABILITY,
          target="WRITE", delta=0.0, priority=0):
    return AllocationRule(
        id=rule_id,
        when=tuple(when or [_cond()]),
        then=RuleThen(action, target, delta=delta),
        scope=(),
        evidence=("exp_1", "exp_2"),
        evidence_strength=0.5,
        uncertainty={},
        constraints={},
        priority=priority,
    )


def test_predicate_rejects_class_b_and_c_features():
    # declared_kind is class B: not a legitimate pre-allocation observable
    # in our benchmarks. kind/condition are class C benchmark labels.
    for bad in ("declared_kind", "declared_constraints", "declared_output_type",
                "production_task", "correct_strategy", "expected_role",
                "condition", "phase", "kind"):
        with pytest.raises(ValueError):
            AtomicCondition(bad, Operator.EQ, "write_multi")


def test_predicate_accepts_all_class_a_features():
    feats = {
        "requires_output_artifact": True, "output_artifact_count": 3,
        "has_named_output_targets": True, "requested_write_effect": True,
        "requested_read_effect": False, "requested_execute_effect": False,
        "independent_work_unit_count": 3, "dependency_depth": 1,
        "multi_step": True,
    }
    for f, v in feats.items():
        assert AtomicCondition(f, Operator.EQ, v).holds(feats)


def test_rule_requires_evidence():
    with pytest.raises(ValueError):
        AllocationRule(id="r", when=(_cond(),),
                       then=RuleThen(RuleAction.REQUIRE_ROLE, "producer"),
                       scope=(), evidence=(), evidence_strength=0.1,
                       uncertainty={}, constraints={}, priority=0)


def test_rule_rejects_unconditional_when():
    with pytest.raises(ValueError):
        AllocationRule(id="r", when=(),
                       then=RuleThen(RuleAction.REQUIRE_ROLE, "producer"),
                       scope=(), evidence=("e1",), evidence_strength=0.1,
                       uncertainty={}, constraints={}, priority=0)


def test_rule_hash_deterministic_and_version_pinned():
    # Content hash, not identity: same content -> same hash even with
    # different ids; different content -> different hash.
    assert _rule("rule_0001").rule_hash == _rule("rule_0002").rule_hash
    assert _rule().rule_hash == _rule().rule_hash
    assert _rule().rule_hash != _rule(
        "rule_0001", action=RuleAction.REQUIRE_ROLE,
        target="producer").rule_hash


def test_strategy_delta_hard_bound():
    with pytest.raises(ValueError):
        RuleThen(RuleAction.BOOST_STRATEGY, "parallel_agents", delta=0.5)
    with pytest.raises(ValueError):
        RuleThen(RuleAction.BOOST_STRATEGY, "parallel_agents", delta=-0.1)
    with pytest.raises(ValueError):
        RuleThen(RuleAction.PENALIZE_STRATEGY, "parallel_agents", delta=0.1)
    RuleThen(RuleAction.BOOST_STRATEGY, "parallel_agents", delta=0.3)


def test_resolve_rules_deterministic_and_ordered():
    feats = {"requested_write_effect": True, "output_artifact_count": 3}
    req = _rule("rule_0001", action=RuleAction.REQUIRE_CAPABILITY,
                target="WRITE", priority=1)
    boost = _rule("rule_0002", action=RuleAction.BOOST_STRATEGY,
                  target="direct", delta=0.1, priority=5)
    out1 = resolve_rules([req, boost], feats)
    out2 = resolve_rules([boost, req], feats)  # input order irrelevant
    assert out1 == out2
    assert [t.target for t in out1["requirements"]] == ["WRITE"]
    assert out1["adjustments"] == {"direct": 0.1}
    assert out1["applicable_rules"] == ["rule_0002", "rule_0001"]  # priority


def test_candidate_rejects_empty_rules():
    with pytest.raises(ValueError):
        PolicyCandidate(id="c", parent_version="v1", rules=(),
                        hypothesis="h", source_experiences=(),
                        attribution_ids=())


def test_candidate_hash_pins_rule_set():
    c1 = PolicyCandidate(id="c1", parent_version="v1", rules=(_rule(),),
                         hypothesis="h", source_experiences=("e1",),
                         attribution_ids=("a1",))
    c2 = PolicyCandidate(id="c2", parent_version="v1", rules=(_rule(),),
                         hypothesis="h", source_experiences=("e1",),
                         attribution_ids=("a1",))
    assert c1.candidate_hash == c2.candidate_hash


def test_promotion_decision_coherence():
    with pytest.raises(ValueError):
        PromotionDecision(id="d", candidate_id="c", decision="PROMOTE",
                          reasons=("r",), evaluation_id="e",
                          assurance_id="a", assurance_verdict="SOUND",
                          policy_version=None)
    with pytest.raises(ValueError):
        PromotionDecision(id="d", candidate_id="c", decision="REJECT",
                          reasons=("r",), evaluation_id="e",
                          assurance_id="a", assurance_verdict="SOUND",
                          policy_version="not-none")


def test_eligibility_matrix():
    # Schema v2.1: eligibility derives from the interpreted learning
    # outcome, not the raw evaluator verdict.
    assert eligibility_for(LearningOutcome.POSITIVE) is Eligibility.POSITIVE
    assert eligibility_for(LearningOutcome.NEGATIVE) is Eligibility.NEGATIVE
    # Certified outcome-failure is directional negative.
    assert eligibility_for(LearningOutcome.NEGATIVE_OUTCOME) is \
        Eligibility.NEGATIVE
    # Untrusted inputs stay EXCLUDED; the rest are nondirectional.
    assert eligibility_for(LearningOutcome.EXCLUDED,
                           "UNSOUND_ASSURANCE") is Eligibility.EXCLUDED
    assert eligibility_for(LearningOutcome.EXCLUDED,
                           "INVALID_EVALUATION") is Eligibility.EXCLUDED
    assert eligibility_for(LearningOutcome.EXCLUDED,
                           "EVALUATOR_UNCERTAIN") is \
        Eligibility.NONDIRECTIONAL


def test_canonical_hash_stable_across_key_order():
    assert canonical_hash({"b": 1, "a": [3, 2]}) == \
        canonical_hash({"a": [3, 2], "b": 1})
