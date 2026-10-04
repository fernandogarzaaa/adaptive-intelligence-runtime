"""Capability model.

A capability is not merely a tool: it is a validated, versioned unit of
cognitive know-how (e.g. "verify_claims", "spawn_policy_v2") that the
allocator can apply to future runs. Nothing is promoted on a single success.
"""

from __future__ import annotations

import uuid
from enum import Enum

from pydantic import BaseModel, Field

from air.events.fabric import utcnow


class CapabilityStatus(str, Enum):
    CANDIDATE = "CANDIDATE"
    VALIDATING = "VALIDATING"
    VALIDATED = "VALIDATED"
    PROMOTED = "PROMOTED"
    DEPRECATED = "DEPRECATED"
    REJECTED = "REJECTED"


class CapabilityEffect(BaseModel):
    """How a promoted capability changes future allocation. Data, not code."""
    type: str  # spawn_threshold | strategy_boost | role_prior
    strategy: str | None = None
    role: str | None = None
    value: float = 0.0


class Capability(BaseModel):
    capability_id: str = Field(default_factory=lambda: "cap_" + uuid.uuid4().hex[:12])
    name: str
    description: str
    version: str = "0.1.0"
    requirements: dict = Field(default_factory=dict)
    tools: list[str] = Field(default_factory=list)
    model_requirements: dict = Field(default_factory=dict)
    policy: dict = Field(default_factory=dict)
    effect: CapabilityEffect | None = None
    performance: dict = Field(default_factory=lambda: {"uses": 0, "successes": 0,
                                                        "failures": 0})
    confidence: float = 0.0
    provenance: dict = Field(default_factory=dict)
    validation_status: CapabilityStatus = CapabilityStatus.CANDIDATE
    created_from: str | None = None
    lineage: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=utcnow)
    updated_at: str = Field(default_factory=utcnow)
