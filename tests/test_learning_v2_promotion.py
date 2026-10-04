"""Promotion gate + the learner/allocator isolation invariant."""

import pytest

from air.learning_v2 import promotion
from air.learning_v2.contracts import (
    AllocationRule,
    AtomicCondition,
    DiscriminatingEvaluation,
    Operator,
    PolicyCandidate,
    RuleAction,
    RuleThen,
)
from air.learning_v2.promotion import (
    PromotionLog,
    decide_promotion,
    next_version,
)


def _rule():
    return AllocationRule(
        id="rule_0001",
        when=(AtomicCondition("requested_write_effect", Operator.EQ, True),),
        then=RuleThen(RuleAction.REQUIRE_CAPABILITY, "WRITE"),
        scope=(), evidence=("e1",), evidence_strength=0.8,
        uncertainty={}, constraints={}, priority=0)


def _candidate():
    return PolicyCandidate(id="cand_1", parent_version="v1",
                           rules=(_rule(),), hypothesis="h",
                           source_experiences=("e1",),
                           attribution_ids=("a1",))


def _evaluation(verdict="PASS"):
    return DiscriminatingEvaluation(
        id="eval_1", candidate_id="cand_1", parent_version="v1",
        sealed_pool_id="pool_1", decision_delta_task_ids=("t1",),
        paired_outcomes=(), statistics={}, verdict=verdict)


def test_promote_requires_pass_and_sound():
    d = decide_promotion(id="d1", candidate=_candidate(),
                         evaluation=_evaluation("PASS"),
                         assurance_verdict="SOUND", assurance_id="a1")
    assert d.decision == "PROMOTE"
    assert d.policy_version is not None
    assert d.policy_version.parent_version == "v1"
    assert d.policy_version.version == "v2"
    assert d.policy_version.rules[0].rule_hash == _rule().rule_hash


def test_reject_on_failed_evaluation():
    d = decide_promotion(id="d1", candidate=_candidate(),
                         evaluation=_evaluation("FAIL"),
                         assurance_verdict="SOUND", assurance_id="a1")
    assert d.decision == "REJECT"
    assert d.policy_version is None
    assert any("FAIL" in r for r in d.reasons)


def test_reject_on_vacuous_evaluation():
    d = decide_promotion(id="d1", candidate=_candidate(),
                         evaluation=_evaluation("VACUOUS"),
                         assurance_verdict="SOUND", assurance_id="a1")
    assert d.decision == "REJECT"


def test_reject_on_unsound_assurance():
    d = decide_promotion(id="d1", candidate=_candidate(),
                         evaluation=_evaluation("PASS"),
                         assurance_verdict="UNSOUND", assurance_id="a1")
    assert d.decision == "REJECT"
    assert d.policy_version is None


def test_rejections_are_kept():
    log = PromotionLog()
    log.append(decide_promotion(id="d1", candidate=_candidate(),
                                evaluation=_evaluation("FAIL"),
                                assurance_verdict="SOUND",
                                assurance_id="a1"))
    log.append(decide_promotion(id="d2", candidate=_candidate(),
                                evaluation=_evaluation("PASS"),
                                assurance_verdict="SOUND",
                                assurance_id="a1"))
    assert len(log) == 2
    assert [d.decision for d in log.decisions()] == ["REJECT", "PROMOTE"]


def test_next_version():
    assert next_version("v1") == "v2"
    assert next_version("v12") == "v13"
    assert next_version("custom") == "custom.1"


def test_promotion_module_never_activates():
    # The isolation invariant, white-box: promotion builds the artifact.
    # Activation is someone else's job; no activation entrypoint exists
    # here and none is reachable from the learner.
    assert not hasattr(promotion, "activate")
    assert not hasattr(promotion, "apply_policy")
    assert not hasattr(promotion, "set_active_policy")
    src = open(promotion.__file__).read()
    for token in ("air.allocation", "air.policy", "air.learning.engine",
                  "activate(", "set_current", "current_version"):
        assert token not in src, f"isolation breach: {token}"
