"""Spawn policy: the runtime's decision rule for whether a new sibling agent
is worth creating.

Conceptually: spawn iff expected marginal utility >
(compute cost + coordination cost + latency cost), adjusted for risk.

This is a v1 heuristic policy: deterministic, inspectable, versioned, and
later learnable from experience. Every decision carries its reason and the
numbers behind it, so the operator can audit why an agent exists.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from air.agents.models import SpawnDecision

POLICY_VERSION = "spawn-policy v1.0.0"

# Role priors: expected usefulness per unit of uncertainty, from experience.
# These are initial priors; the learning engine updates them from outcomes.
ROLE_PRIORS: dict[str, dict[str, float]] = {
    "researcher": {"info_gain": 0.55, "success_gain": 0.20, "verify_gain": 0.05, "parallel_gain": 0.40},
    "planner": {"info_gain": 0.25, "success_gain": 0.35, "verify_gain": 0.05, "parallel_gain": 0.10},
    "coder": {"info_gain": 0.15, "success_gain": 0.50, "verify_gain": 0.05, "parallel_gain": 0.45},
    "critic": {"info_gain": 0.20, "success_gain": 0.25, "verify_gain": 0.45, "parallel_gain": 0.05},
    "verifier": {"info_gain": 0.10, "success_gain": 0.15, "verify_gain": 0.70, "parallel_gain": 0.05},
    "specialist": {"info_gain": 0.30, "success_gain": 0.40, "verify_gain": 0.10, "parallel_gain": 0.20},
    "synthesizer": {"info_gain": 0.15, "success_gain": 0.30, "verify_gain": 0.10, "parallel_gain": 0.05},
}


class SpawnContext(BaseModel):
    """Everything the policy may consider. All fields are measured or estimated
    by the allocator, never invented by the agent requesting the spawn."""

    role: str
    uncertainty: float = Field(ge=0.0, le=1.0)
    task_complexity: float = Field(ge=0.0, le=1.0, default=0.5)
    novelty: float = Field(ge=0.0, le=1.0, default=0.5)
    failure_history_rate: float = Field(ge=0.0, le=1.0, default=0.0)
    current_agent_count: int = Field(ge=0, default=0)
    parallelizable_fraction: float = Field(ge=0.0, le=1.0, default=0.5)
    verification_need: float = Field(ge=0.0, le=1.0, default=0.3)
    time_pressure: float = Field(ge=0.0, le=1.0, default=0.3)
    estimated_tokens: int = Field(ge=0, default=4000)
    estimated_latency_s: float = Field(ge=0.0, default=60.0)
    token_price_per_1k: float = Field(ge=0.0, default=0.002)
    budget_tokens_remaining: int | None = None
    budget_agents_remaining: int | None = None
    reason_hint: str | None = None
    evidence: list[str] = Field(default_factory=list)


def estimate_gains(ctx: SpawnContext) -> dict[str, float]:
    prior = ROLE_PRIORS.get(ctx.role, ROLE_PRIORS["specialist"])
    info = prior["info_gain"] * (0.4 + 0.6 * ctx.uncertainty) * (0.5 + 0.5 * ctx.novelty)
    success = prior["success_gain"] * (0.3 + 0.7 * ctx.task_complexity)
    # Verification gain matters most when past failures were caught late.
    verify = prior["verify_gain"] * (0.3 + 0.7 * ctx.verification_need) * (1.0 + ctx.failure_history_rate)
    parallel = prior["parallel_gain"] * ctx.parallelizable_fraction * (1.0 - ctx.time_pressure * 0.3)
    # Diminishing returns: the 5th agent helps less than the 2nd.
    crowding = 1.0 / (1.0 + 0.35 * max(0, ctx.current_agent_count - 1))
    total = (info + success + verify + parallel) * crowding
    return {
        "expected_information_gain": round(info * crowding, 4),
        "expected_success_gain": round(success * crowding, 4),
        "expected_verification_gain": round(verify * crowding, 4),
        "expected_parallelization_gain": round(parallel * crowding, 4),
        "total": round(total, 4),
    }


def estimate_costs(ctx: SpawnContext) -> dict[str, float]:
    compute = (ctx.estimated_tokens / 1000.0) * ctx.token_price_per_1k
    # Normalize latency into the same 0..1-ish utility scale: 10 min ~ 0.2.
    latency = min(1.0, ctx.estimated_latency_s / 3000.0)
    # Coordination grows superlinearly with existing agent count.
    coordination = 0.02 * ctx.current_agent_count + 0.005 * ctx.current_agent_count**2
    total = compute + latency + coordination
    return {
        "compute_cost": round(compute, 4),
        "latency_cost": round(latency, 4),
        "coordination_cost": round(coordination, 4),
        "total": round(total, 4),
    }


def decide(ctx: SpawnContext) -> SpawnDecision:
    gains = estimate_gains(ctx)
    costs = estimate_costs(ctx)
    risk = round(ctx.failure_history_rate * 0.5 + ctx.task_complexity * 0.3 + ctx.novelty * 0.2, 4)
    net = gains["total"] - costs["total"] - risk * 0.5

    reasons: list[str] = []
    if ctx.budget_agents_remaining is not None and ctx.budget_agents_remaining <= 0:
        return SpawnDecision(
            decision="DENY", role=ctx.role,
            reason="agent budget exhausted: no agent slots remaining",
            expected_gain=gains["total"], estimated_cost=costs["total"], risk=risk,
            evidence=ctx.evidence + ["budget_agents_remaining=0"],
        )
    if ctx.budget_tokens_remaining is not None and ctx.estimated_tokens > ctx.budget_tokens_remaining:
        return SpawnDecision(
            decision="DENY", role=ctx.role,
            reason="token budget exhausted: estimated tokens exceed remaining budget",
            expected_gain=gains["total"], estimated_cost=costs["total"], risk=risk,
            evidence=ctx.evidence + ["estimated_tokens > budget_tokens_remaining"],
        )

    dominant = max(
        ("information", gains["expected_information_gain"]),
        ("success", gains["expected_success_gain"]),
        ("verification", gains["expected_verification_gain"]),
        ("parallelization", gains["expected_parallelization_gain"]),
        key=lambda kv: kv[1],
    )[0]

    if net > 0.05:
        if ctx.reason_hint:
            reasons.append(ctx.reason_hint)
        reasons.append(f"dominant expected gain: {dominant}")
        reasons.append(f"net utility {net:.3f} above spawn threshold")
        if risk > 0.4:
            reasons.append(f"elevated risk {risk:.2f} accepted: gains justify it")
        return SpawnDecision(
            decision="SPAWN", role=ctx.role, reason="; ".join(reasons),
            expected_gain=gains["total"], estimated_cost=costs["total"], risk=risk,
            evidence=ctx.evidence,
        )

    if costs["coordination_cost"] > gains["total"] * 0.5:
        reasons.append("coordination cost dominates expected gain")
    elif gains["total"] < 0.1:
        reasons.append("expected gain too low for this role under current uncertainty")
    else:
        reasons.append("marginal expected utility below cost threshold")
    if risk > 0.5:
        reasons.append(f"risk {risk:.2f} too high relative to gain")
    reasons.append(f"net utility {net:.3f} did not clear spawn threshold 0.05")
    return SpawnDecision(
        decision="DENY", role=ctx.role, reason="; ".join(reasons),
        expected_gain=gains["total"], estimated_cost=costs["total"], risk=risk,
        evidence=ctx.evidence,
    )
