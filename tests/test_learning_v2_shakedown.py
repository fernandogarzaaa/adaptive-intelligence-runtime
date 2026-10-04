"""The shakedown gate: behavioral equivalence, not rule syntax."""

from air.learning_v2 import shakedown
from air.learning_v2.contracts import TASK_FEATURES_V1


def test_shakedown_passes_with_expected_statement():
    rep = shakedown.run_shakedown()
    assert rep.passed, rep.failures
    assert rep.statement == (
        "The learner generated a rule whose behavior is equivalent to "
        "the prespecified target over the shakedown population while "
        "preserving the research behavior and rejecting the lexical "
        "confounders.")


def test_shakedown_checks_cover_pairs_and_confounders():
    rep = shakedown.run_shakedown()
    ids = {c["task_id"] for c in rep.checks}
    assert {"pair_research", "pair_production", "conf_three_researchers",
            "conf_three_sources", "conf_three_files",
            "conf_three_sections"} <= ids
    assert all(c["passed"] for c in rep.checks)


def test_research_behavior_preserved_exactly():
    rep = shakedown.run_shakedown()
    for c in rep.checks:
        if c["kind"] == "research":
            assert c["candidate_strategy"] == "parallel_agents", c
            assert c["parent_strategy"] == "parallel_agents", c


def test_production_gets_production_capable_organization():
    rep = shakedown.run_shakedown()
    for c in rep.checks:
        if c["kind"] in ("production", "mixed"):
            assert c["candidate_strategy"] != "parallel_agents", c


def test_rules_reference_only_class_a_features():
    rep = shakedown.run_shakedown()
    assert rep.n_rules > 0
    for r in rep.rules:
        for cond in r["when"] + r["scope"]:
            assert cond["feature"] in TASK_FEATURES_V1, cond
    # No lexical proxy can appear: the schema cannot express it.
    blob = str(rep.rules)
    for token in ("artifact_mentions", "three", "word_count",
                  "declared_kind", "production_task"):
        assert token not in blob


def test_training_and_population_disjoint():
    records = shakedown.build_training_records()
    pop_ids = {t.id for t in shakedown.shakedown_population()}
    assert not ({r.id for r in records} & pop_ids)


def test_shakedown_deterministic():
    r1 = shakedown.run_shakedown()
    r2 = shakedown.run_shakedown()
    assert r1.passed == r2.passed
    assert [r["rule_hash"] for r in r1.rules] == \
        [r["rule_hash"] for r in r2.rules]
    assert [(c["task_id"], c["passed"]) for c in r1.checks] == \
        [(c["task_id"], c["passed"]) for c in r2.checks]


def test_feasibility_before_topology():
    # Hard requirements veto before scoring: even with parallel as the
    # highest base score, a WRITE requirement removes it.
    from air.learning_v2.contracts import (
        AllocationRule, AtomicCondition, Operator, RuleAction, RuleThen)
    rule = AllocationRule(
        id="r1",
        when=(AtomicCondition("requested_write_effect", Operator.EQ, True),),
        then=RuleThen(RuleAction.REQUIRE_CAPABILITY, "WRITE"),
        scope=(), evidence=("e1",), evidence_strength=0.9,
        uncertainty={}, constraints={}, priority=0)
    feats = dict(shakedown.production_features(0))
    org = shakedown.choose_with_policy(feats, [rule])
    assert "WRITE" in org.capabilities
    assert org.strategy != "parallel_agents"
