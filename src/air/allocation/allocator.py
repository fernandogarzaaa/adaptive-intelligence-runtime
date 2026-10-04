"""Cognitive allocator: the most important subsystem.

Given a goal, the allocator decides what cognitive organization should exist
to solve it. Strategies are executable configurations, not buttons: each one
produces a CognitivePlan describing agent count, roles, topology, budgets,
evaluation requirements, stopping criteria, and verification strategy.

v1 is a heuristic scoring engine: deterministic, inspectable, versioned.
The learning engine refines its weights from experience outcomes.
"""

from __future__ import annotations

import re
import uuid
from enum import Enum

from pydantic import BaseModel, Field

ALLOCATOR_VERSION = "cognitive-allocator v1.0.0"


class Strategy(str, Enum):
    DIRECT = "direct"                        # reason directly, no agents
    SINGLE_AGENT = "single_agent"            # one generalist agent
    PARALLEL_AGENTS = "parallel_agents"      # N independent specialists
    HIERARCHICAL_AGENTS = "hierarchical_agents"  # planner -> workers
    DEBATE = "debate"                        # competing agents + critic
    RESEARCH_THEN_EXECUTE = "research_then_execute"
    EXECUTE_THEN_VERIFY = "execute_then_verify"
    SIMULATION_FIRST = "simulation_first"
    ADAPTIVE_SPAWN = "adaptive_spawn"        # root decides spawns dynamically


class AgentSpec(BaseModel):
    role: str
    specialization: str | None = None
    objective: str
    model_tier: str = "standard"  # cheap | standard | strong | local
    capabilities: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    token_budget: int = 8000
    parent_role: str | None = None  # None => child of root


class CognitivePlan(BaseModel):
    plan_id: str = Field(default_factory=lambda: "plan_" + uuid.uuid4().hex[:12])
    goal: str
    strategy: Strategy
    rationale: list[str] = Field(default_factory=list)
    agent_specs: list[AgentSpec] = Field(default_factory=list)
    topology: str = "single"
    evaluation_required: bool = False
    verification_strategy: str = "none"
    stopping_criteria: list[str] = Field(default_factory=list)
    token_budget: int = 32000
    time_budget_s: int = 900
    cost_budget_usd: float = 2.0
    agent_budget: int = 4
    allocator_version: str = ALLOCATOR_VERSION
    scores: dict[str, float] = Field(default_factory=dict)


class GoalFeatures(BaseModel):
    uncertainty: float = 0.5
    complexity: float = 0.5
    needs_research: bool = False
    needs_verification: bool = False
    needs_competing_views: bool = False
    parallelizable: bool = False
    high_stakes: bool = False
    code_task: bool = False


_VERIFY_WORDS = {"verif", "audit", "review", "check", "validat", "secur", "correct"}
_RESEARCH_WORDS = {"research", "investigat", "survey", "find", "compar", "analyz", "identify"}
_DEBATE_WORDS = {"tradeoff", "trade-off", "debate", "argu", "pros and cons", "decide between", "versus", " vs "}
_CODE_WORDS = {"code", "implement", "fix", "bug", "refactor", "program", "script", "function"}
_PARALLEL_WORDS = {"each", "multiple", "several", "independently", "three", "3 ", "list of"}
_STAKES_WORDS = {"production", "security", "vulnerab", "payment", "medical", "legal", "risk"}


def _has(words: set[str], text: str) -> bool:
    return any(w in text for w in words)


def extract_features(goal: str, context: dict | None = None) -> GoalFeatures:
    """Estimate goal features from text signals + explicit context hints.

    These are estimates, recorded on the plan for audit. Uncertainty defaults
    to 0.5 (unknown), not 0.
    """
    text = goal.lower()
    ctx = context or {}
    uncertainty = float(ctx.get("uncertainty", 0.5))
    complexity = float(ctx.get("complexity", 0.5))
    # Longer, multi-clause goals tend to be more complex.
    clauses = len(re.split(r"[,;.]|\band\b", text))
    complexity = min(1.0, max(complexity, min(0.9, 0.2 + 0.1 * clauses)))
    return GoalFeatures(
        uncertainty=min(1.0, max(0.0, uncertainty)),
        complexity=min(1.0, max(0.0, complexity)),
        needs_research=_has(_RESEARCH_WORDS, text) or bool(ctx.get("needs_research")),
        needs_verification=_has(_VERIFY_WORDS, text) or bool(ctx.get("needs_verification")),
        needs_competing_views=_has(_DEBATE_WORDS, text),
        parallelizable=_has(_PARALLEL_WORDS, text) or bool(ctx.get("parallelizable")),
        high_stakes=_has(_STAKES_WORDS, text) or bool(ctx.get("high_stakes")),
        code_task=_has(_CODE_WORDS, text),
    )


def score_strategies(f: GoalFeatures, agent_budget: int) -> dict[Strategy, float]:
    """Score each executable strategy. Higher is better. Weights are v1 priors."""
    s: dict[Strategy, float] = {}
    s[Strategy.DIRECT] = 0.55 - 0.6 * f.uncertainty - 0.5 * f.complexity
    s[Strategy.SINGLE_AGENT] = 0.60 - 0.25 * f.uncertainty + 0.1 * (1 - f.complexity)
    s[Strategy.PARALLEL_AGENTS] = (
        0.35 + 0.5 * f.parallelizable + 0.2 * f.uncertainty - 0.15 * (not f.parallelizable)
    )
    s[Strategy.HIERARCHICAL_AGENTS] = 0.30 + 0.55 * f.complexity - 0.2 * (1 - f.complexity)
    s[Strategy.DEBATE] = 0.25 + 0.6 * f.needs_competing_views + 0.25 * f.uncertainty
    s[Strategy.RESEARCH_THEN_EXECUTE] = 0.30 + 0.55 * f.needs_research + 0.15 * f.uncertainty
    s[Strategy.EXECUTE_THEN_VERIFY] = (
        0.30 + 0.5 * f.needs_verification + 0.3 * f.high_stakes + 0.2 * f.code_task
    )
    s[Strategy.SIMULATION_FIRST] = 0.20 + 0.45 * f.high_stakes + 0.2 * f.uncertainty
    s[Strategy.ADAPTIVE_SPAWN] = (
        0.40 + 0.35 * f.uncertainty + 0.25 * f.complexity - 0.1 * (agent_budget <= 1)
    )
    # Hard constraints: tiny budgets cannot support multi-agent strategies.
    if agent_budget <= 1:
        for st in (Strategy.PARALLEL_AGENTS, Strategy.HIERARCHICAL_AGENTS,
                   Strategy.DEBATE, Strategy.ADAPTIVE_SPAWN):
            s[st] = -1.0
    if agent_budget <= 0:
        for st in list(s):
            if st not in (Strategy.DIRECT,):
                s[st] = -1.0
    return s


def build_plan(goal: str, strategy: Strategy, f: GoalFeatures,
               token_budget: int, time_budget_s: int,
               cost_budget_usd: float, agent_budget: int) -> CognitivePlan:
    specs: list[AgentSpec] = []
    topology = "single"
    verification = "none"
    eval_required = False
    stopping = ["objective complete", "budget exhausted", "no progress for 3 turns"]

    def spec(role: str, objective: str, **kw) -> AgentSpec:
        return AgentSpec(role=role, objective=objective, **kw)

    if strategy == Strategy.DIRECT:
        topology = "none"
    elif strategy == Strategy.SINGLE_AGENT:
        specs = [spec("specialist", goal, capabilities=["reason", "act"])]
    elif strategy == Strategy.PARALLEL_AGENTS:
        n = min(3, agent_budget)
        topology = "parallel"
        specs = [spec("researcher", f"Independent investigation {i+1} of: {goal}",
                      capabilities=["research_web", "summarize"]) for i in range(n)]
        specs.append(spec("synthesizer", f"Synthesize {n} independent investigations into one answer: {goal}",
                          capabilities=["summarize"]))
    elif strategy == Strategy.HIERARCHICAL_AGENTS:
        topology = "hierarchical"
        specs = [
            spec("planner", f"Decompose into subtasks: {goal}", capabilities=["plan_project"]),
            spec("researcher", "Gather evidence for subtasks", parent_role="planner",
                 capabilities=["research_web"]),
            spec("coder", "Execute subtasks", parent_role="planner",
                 capabilities=["write_code"] if f.code_task else ["act"]),
            spec("synthesizer", "Assemble final result", capabilities=["summarize"]),
        ]
    elif strategy == Strategy.DEBATE:
        topology = "debate"
        specs = [
            spec("specialist", f"Argue the strongest case FOR the proposed approach: {goal}",
                 specialization="proponent", capabilities=["reason"]),
            spec("specialist", f"Argue the strongest case AGAINST the proposed approach: {goal}",
                 specialization="opponent", capabilities=["reason"]),
            spec("critic", "Adjudicate the debate and produce a justified conclusion",
                 capabilities=["verify_claims"]),
        ]
        eval_required = True
    elif strategy == Strategy.RESEARCH_THEN_EXECUTE:
        topology = "sequential"
        specs = [
            spec("researcher", f"Research and gather evidence: {goal}",
                 capabilities=["research_web", "summarize"]),
            spec("specialist", "Execute based on research findings", parent_role="researcher",
                 capabilities=["write_code"] if f.code_task else ["act"]),
        ]
    elif strategy == Strategy.EXECUTE_THEN_VERIFY:
        topology = "sequential"
        verification = "independent_verifier"
        eval_required = True
        specs = [
            spec("specialist", f"Execute: {goal}",
                 capabilities=["write_code"] if f.code_task else ["act"]),
            spec("verifier", "Independently verify the result with evidence, not self-report",
                 capabilities=["verify_claims", "evaluate_code"]),
        ]
    elif strategy == Strategy.SIMULATION_FIRST:
        topology = "sequential"
        verification = "simulation_then_verify"
        specs = [
            spec("specialist", f"Simulate candidate strategies for: {goal} (mark all outputs SIMULATED)",
                 specialization="simulator", capabilities=["simulate_strategy"]),
            spec("specialist", f"Execute the best simulated strategy for: {goal}",
                 capabilities=["write_code"] if f.code_task else ["act"]),
            spec("verifier", "Verify against real observations", capabilities=["verify_claims"]),
        ]
    elif strategy == Strategy.ADAPTIVE_SPAWN:
        topology = "adaptive"
        verification = "adaptive"
        specs = [spec("planner", f"Coordinate; spawn siblings only when marginal utility exceeds cost: {goal}",
                      capabilities=["plan_project", "delegate"])]

    if f.high_stakes or f.needs_verification:
        verification = verification if verification != "none" else "independent_verifier"
        eval_required = True

    per_agent = max(4000, token_budget // max(1, len(specs))) if specs else token_budget
    for sp in specs:
        sp.token_budget = per_agent

    return CognitivePlan(
        goal=goal, strategy=strategy, agent_specs=specs, topology=topology,
        evaluation_required=eval_required, verification_strategy=verification,
        stopping_criteria=stopping, token_budget=token_budget,
        time_budget_s=time_budget_s, cost_budget_usd=cost_budget_usd,
        agent_budget=agent_budget,
    )


def allocate(goal: str, context: dict | None = None,
             token_budget: int = 32000, time_budget_s: int = 900,
             cost_budget_usd: float = 2.0, agent_budget: int = 4,
             force_strategy: Strategy | None = None) -> CognitivePlan:
    """Produce an inspectable CognitivePlan for a goal."""
    f = extract_features(goal, context)
    scores = score_strategies(f, agent_budget)
    strategy = force_strategy or max(scores, key=lambda k: scores[k])
    plan = build_plan(goal, strategy, f, token_budget, time_budget_s,
                      cost_budget_usd, agent_budget)
    plan.scores = {st.value: round(v, 3) for st, v in scores.items()}
    plan.rationale = [
        f"uncertainty={f.uncertainty:.2f}, complexity={f.complexity:.2f}",
        f"needs_research={f.needs_research}, needs_verification={f.needs_verification}",
        f"parallelizable={f.parallelizable}, high_stakes={f.high_stakes}, code_task={f.code_task}",
        f"selected {strategy.value} with score {scores[strategy]:.3f}",
        f"runner-up: {sorted(scores, key=lambda k: scores[k], reverse=True)[1].value}"
        if len(scores) > 1 else "only candidate",
    ]
    return plan
