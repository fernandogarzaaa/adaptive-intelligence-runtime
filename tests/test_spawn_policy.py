"""Spawn policy: gain/cost math, denials, determinism."""

from air.agents.spawn_policy import SpawnContext, decide


def test_spawn_approved_when_gain_exceeds_cost():
    ctx = SpawnContext(role="researcher", uncertainty=0.9, novelty=0.8,
                       parallelizable_fraction=0.8, current_agent_count=1,
                       estimated_tokens=2000, estimated_latency_s=30.0)
    d = decide(ctx)
    assert d.decision == "SPAWN"
    assert d.expected_gain > d.estimated_cost
    assert d.reason, "every decision must carry a reason"
    assert 0.0 <= d.risk <= 1.0


def test_spawn_denied_when_agent_budget_exhausted():
    ctx = SpawnContext(role="researcher", uncertainty=0.9,
                       budget_agents_remaining=0)
    d = decide(ctx)
    assert d.decision == "DENY"
    assert "budget" in d.reason.lower()


def test_spawn_denied_when_tokens_exceed_budget():
    ctx = SpawnContext(role="coder", uncertainty=0.5,
                       estimated_tokens=100_000, budget_tokens_remaining=1000)
    d = decide(ctx)
    assert d.decision == "DENY"


def test_spawn_denied_when_coordination_dominates():
    # 20 existing agents: coordination cost should kill the spawn.
    ctx = SpawnContext(role="researcher", uncertainty=0.2, novelty=0.1,
                       current_agent_count=20, parallelizable_fraction=0.1,
                       estimated_latency_s=600.0)
    d = decide(ctx)
    assert d.decision == "DENY"


def test_decision_is_deterministic():
    kw = dict(role="verifier", uncertainty=0.7, verification_need=0.9,
              failure_history_rate=0.4)
    d1 = decide(SpawnContext(**kw))
    d2 = decide(SpawnContext(**kw))
    assert d1 == d2


def test_verifier_preferred_under_high_verification_need():
    v = decide(SpawnContext(role="verifier", uncertainty=0.4,
                            verification_need=0.95, failure_history_rate=0.5))
    r = decide(SpawnContext(role="researcher", uncertainty=0.4,
                            verification_need=0.95, failure_history_rate=0.5))
    assert v.expected_gain >= r.expected_gain
