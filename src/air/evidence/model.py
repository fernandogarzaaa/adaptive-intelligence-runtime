"""Evidence data model.

An Evidence is a deterministic projection of one ``tool.completed``
ledger event. ``evidence_id`` is derived, never assigned: two
reconstructions of the same event yield the same object.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class VerificationScope(BaseModel):
    """What the tool execution mechanically establishes.

    ``tool``: the tool name as executed (e.g. ``fs.read``).
    ``targets``: artifact identifiers the tool acted on, taken from
    the redacted call args (e.g. ``path`` for fs tools). Empty for
    opaque tools (``shell.exec``, unknown tools): such evidence can
    never be relevant for an artifact claim, fail-closed.
    ``effect``: the tool-declared, mechanical effect. One of
    ``observe`` (fs.read), ``create/modify`` (fs.write),
    ``execute`` (shell.exec and unknown tools), ``delete``.
    This is what the tool *does*, not what the claim *means*.
    """

    tool: str
    targets: list[str] = Field(default_factory=list)
    effect: str = "execute"


class Evidence(BaseModel):
    """One eligible evidence source, projected from the ledger."""

    evidence_id: str  # deterministic: "ev_<source_event_id>"
    source_event_id: str
    tool_call_id: str
    result_hash: str  # verified by recomputation, not trusted from payload
    provenance: str  # the producing agent's epistemic_kind value
    observed_at: str  # the source event's timestamp
    verification_scope: VerificationScope
    # Populated at evaluation time with the citing claim's id:
    # the bidirectional binding between claim and evidence.
    claim_ref: str | None = None


class ClaimOutcome(BaseModel):
    """One claimed outcome, as declared by the agent that produced it.

    Declared in the behavior's result under ``outcomes`` (see
    ``grounding.extract_claims``). ``evidence_refs`` cite evidence by
    ``evidence_id`` (``ev_<event_id>``), raw event id, or tool call id
    (``tc_...``). A claim that cites nothing, or whose citations do
    not resolve to valid evidence covering its artifacts/effects, is
    not grounded: fail-closed.
    """

    claim_id: str
    description: str
    artifacts: list[str] = Field(default_factory=list)
    effects: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    source: str = ""  # e.g. "agent.completed:<event_id>"
