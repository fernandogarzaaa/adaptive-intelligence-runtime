"""Layer 3 — Outcome Evaluation: did the objective actually succeed?

Collects the run's claimed outcomes, binds each to its cited evidence
(the bidirectional claim <-> evidence binding), and runs validity
(layer 1) then relevance (layer 2) for each. SUPPORTED requires every
claimed outcome grounded through both layers.

Claim extraction: behaviors declare outcomes in the result they
return from the agent loop, which the runtime persists as the
``agent.completed`` payload under ``result``::

    {"ok": True,
     "outcomes": [{"id": "o1",
                   "description": "quarterly report written",
                   "artifacts": ["quarterly_report.md"],
                   "effects": ["create"],
                   "evidence_refs": ["ev_evt_abc123"]}]}

``run.completed`` payloads may also carry ``outcomes``. A legacy bare
``claimed`` string (or ``result["claimed"]``) is extracted as an
outcome with no artifacts, effects, or citations: it can never be
grounded, fail-closed, because vague prose is not evidence.
"""

from __future__ import annotations

import json

from pydantic import BaseModel, Field

from air.evidence.model import ClaimOutcome, Evidence
from air.evidence.relevance import check_relevance
from air.evidence.validity import build_evidence


class ClaimGrounding(BaseModel):
    claim_id: str
    description: str
    grounded: bool
    reasons: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    source: str = ""


def extract_claims(conn, run_id: str) -> list[ClaimOutcome]:
    """Pull claimed outcomes from the run's completion events."""
    claims: list[ClaimOutcome] = []
    rows = conn.execute(
        "SELECT event_id, type, payload FROM events"
        " WHERE run_id=? AND type IN ('agent.completed', 'run.completed')"
        " ORDER BY rowid", (run_id,)).fetchall()
    for event_id, event_type, payload_raw in rows:
        try:
            payload = json.loads(payload_raw)
        except (json.JSONDecodeError, TypeError):
            continue
        outcomes: list[dict] = []
        if event_type == "agent.completed":
            result = payload.get("result") or {}
            outcomes = result.get("outcomes") or []
            claimed = result.get("claimed") or payload.get("claimed")
            if not outcomes and isinstance(claimed, str) and claimed:
                outcomes = [{"description": claimed}]
        else:
            outcomes = payload.get("outcomes") or []
        for i, o in enumerate(outcomes):
            if not isinstance(o, dict):
                continue
            claims.append(ClaimOutcome(
                claim_id=str(o.get("id") or f"{event_id}#outcome{i}"),
                description=str(o.get("description") or ""),
                artifacts=[str(a) for a in (o.get("artifacts") or [])],
                effects=[str(e) for e in (o.get("effects") or [])],
                evidence_refs=[str(r) for r in
                               (o.get("evidence_refs") or [])],
                source=f"{event_type}:{event_id}",
            ))
    return claims


def ground_claims(conn, run_id: str) -> list[ClaimGrounding]:
    """Ground every claimed outcome of a run. Fail-closed throughout."""
    grounded: list[ClaimGrounding] = []
    for outcome in extract_claims(conn, run_id):
        reasons: list[str] = []
        valid_cited: list[Evidence] = []
        for ref in outcome.evidence_refs:
            ev, ev_reasons = build_evidence(conn, run_id, ref)
            if ev is None:
                reasons.extend(ev_reasons)
            else:
                # Bidirectional binding: the evidence now cites its claim.
                ev.claim_ref = outcome.claim_id
                valid_cited.append(ev)
        relevant, rel_reasons = check_relevance(outcome, valid_cited)
        reasons.extend(rel_reasons)
        grounded.append(ClaimGrounding(
            claim_id=outcome.claim_id,
            description=outcome.description,
            grounded=relevant,
            reasons=reasons,
            evidence_ids=[ev.evidence_id for ev in valid_cited],
            source=outcome.source,
        ))
    return grounded
