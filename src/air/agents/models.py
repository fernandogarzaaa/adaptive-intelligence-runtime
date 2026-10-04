"""Agent identity model.

Agents are runtime entities with persistent structured identity, not prompts.
"""

from __future__ import annotations

import uuid
from enum import Enum

from pydantic import BaseModel, Field

from air.events.fabric import utcnow


class AgentStatus(str, Enum):
    CREATED = "CREATED"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    BLOCKED = "BLOCKED"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    TERMINATED = "TERMINATED"
    VERIFICATION_PENDING = "VERIFICATION_PENDING"


TERMINAL_STATUSES = {
    AgentStatus.COMPLETED,
    AgentStatus.FAILED,
    AgentStatus.CANCELLED,
    AgentStatus.TERMINATED,
}


class Budget(BaseModel):
    token_limit: int | None = None
    time_limit_s: int | None = None
    cost_limit_usd: float | None = None
    agent_limit: int | None = None
    tool_call_limit: int | None = None
    depth_limit: int | None = None
    consumed_tokens: int = 0
    consumed_cost_usd: float = 0.0
    consumed_tool_calls: int = 0
    consumed_agents: int = 0


class Agent(BaseModel):
    id: str = Field(default_factory=lambda: "ag_" + uuid.uuid4().hex[:12])
    parent_id: str | None = None
    root_run_id: str
    generation: int = 0
    role: str
    specialization: str | None = None
    objective: str
    model: str | None = None
    provider: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    memory_scope: str = "task"
    belief_scope: str = "task"
    policy_scope: str = "task"
    budget: Budget = Field(default_factory=Budget)
    status: AgentStatus = AgentStatus.CREATED
    status_reason: str | None = None
    created_at: str = Field(default_factory=utcnow)
    terminated_at: str | None = None
    lineage: list[str] = Field(default_factory=list)
    capability_version: str | None = None
    policy_version: str | None = None


class SpawnDecision(BaseModel):
    decision: str  # SPAWN | DENY
    role: str
    reason: str
    expected_gain: float
    estimated_cost: float
    risk: float
    evidence: list[str] = Field(default_factory=list)
