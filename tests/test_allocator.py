"""Cognitive allocator: strategy selection is real scoring, not buttons."""

from air.allocation.allocator import Strategy, allocate


def test_verification_goal_selects_verify_strategy():
    plan = allocate("Audit this repository for authentication vulnerabilities and verify each finding")
    assert plan.strategy in (Strategy.EXECUTE_THEN_VERIFY, Strategy.RESEARCH_THEN_EXECUTE,
                             Strategy.ADAPTIVE_SPAWN, Strategy.HIERARCHICAL_AGENTS)
    assert plan.verification_strategy != "none"
    assert plan.rationale, "plan must carry inspectable rationale"
    assert plan.scores, "plan must carry strategy scores"


def test_debate_goal_selects_debate():
    plan = allocate("Decide between microservices versus monolith: argue the tradeoffs and pros and cons")
    assert plan.strategy == Strategy.DEBATE


def test_tiny_budget_forces_direct():
    plan = allocate("Research the market and compare three vendors independently",
                    agent_budget=0)
    assert plan.strategy == Strategy.DIRECT
    assert plan.agent_specs == []


def test_parallel_goal_spawns_independent_agents():
    plan = allocate("Independently research each of these three topics and compare them")
    assert plan.strategy in (Strategy.PARALLEL_AGENTS, Strategy.ADAPTIVE_SPAWN,
                             Strategy.RESEARCH_THEN_EXECUTE)
    if plan.strategy == Strategy.PARALLEL_AGENTS:
        roles = [s.role for s in plan.agent_specs]
        assert roles.count("researcher") >= 2
        assert "synthesizer" in roles


def test_plan_budgets_are_bounded():
    plan = allocate("Write a parser", token_budget=16000, agent_budget=2)
    assert plan.token_budget == 16000
    assert plan.agent_budget == 2
    total = sum(s.token_budget for s in plan.agent_specs)
    assert total <= 16000 or not plan.agent_specs
