"""Promotion: the gated step from candidate to PolicyVersion.

ISOLATION INVARIANT (architectural, tested in
tests/test_learning_v2_isolation.py):

    Learner -> Candidate -> Evaluation -> Assurance -> Promotion
        -> PolicyVersion -> Allocator

    NEVER: Learner -> Allocator.

This module builds the PromotionDecision and, on PROMOTE, the
immutable PolicyVersionV2 artifact. It does NOT activate the
policy: activation is the allocator's job, owned by allocator-side
code that this package never imports and never calls. The learner
is structurally incapable of mutating the active allocator; the
only thing that crosses the boundary is the versioned artifact,
after evaluation and assurance have both cleared it.

Promotion gate (all required):
- the discriminating evaluation verdict is PASS (never VACUOUS,
  never FAIL)
- the assurance verdict on the candidate is SOUND
Anything else -> REJECT with recorded reasons. Rejections are
first-class records: they are logged, never silently dropped.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from air.learning_v2.contracts import (
    DiscriminatingEvaluation,
    PolicyCandidate,
    PolicyVersionV2,
    PromotionDecision,
)


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def next_version(parent_version: str) -> str:
    """Deterministic version increment: v3 -> v4; anything else gets
    a .1 suffix chain (v1-experiment -> v1-experiment.1)."""
    import re
    m = re.fullmatch(r"v(\d+)", parent_version)
    if m:
        return f"v{int(m.group(1)) + 1}"
    return f"{parent_version}.1"


def decide_promotion(*, id: str, candidate: PolicyCandidate,
                     evaluation: DiscriminatingEvaluation,
                     assurance_verdict: str, assurance_id: str,
                     ) -> PromotionDecision:
    """Apply the promotion gate. Returns PROMOTE with a PolicyVersionV2
    or REJECT with reasons. Never activates: see module docstring."""
    reasons: list[str] = []
    av = assurance_verdict.upper()
    if evaluation.verdict != "PASS":
        reasons.append(
            f"discriminating evaluation {evaluation.verdict}: "
            f"{evaluation.statistics.get('verdict_reason', '')}")
    if av != "SOUND":
        reasons.append(f"assurance verdict {av} is not SOUND")
    if reasons:
        return PromotionDecision(
            id=id, candidate_id=candidate.id, decision="REJECT",
            reasons=tuple(reasons), evaluation_id=evaluation.id,
            assurance_id=assurance_id, assurance_verdict=av)
    version = next_version(candidate.parent_version)
    policy_version = PolicyVersionV2(
        id=f"pv2_{candidate.id}", version=version,
        parent_version=candidate.parent_version,
        rules=candidate.rules, promotion_decision_id=id,
        evaluation_id=evaluation.id, assurance_id=assurance_id,
        source_experiences=candidate.source_experiences,
        created_at=utcnow())
    return PromotionDecision(
        id=id, candidate_id=candidate.id, decision="PROMOTE",
        reasons=("discriminating evaluation PASS; assurance SOUND; "
                 "candidate promoted to versioned policy artifact; "
                 "activation is the allocator's responsibility",),
        evaluation_id=evaluation.id, assurance_id=assurance_id,
        assurance_verdict=av, policy_version=policy_version)


class PromotionLog:
    """Append-only record of promotion decisions. Rejections are kept
    permanently: a rejected candidate is evidence, not garbage."""

    def __init__(self, path: str | None = None) -> None:
        self._records: list[PromotionDecision] = []
        self._path = path

    def append(self, decision: PromotionDecision) -> None:
        self._records.append(decision)
        if self._path:
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(json.dumps(decision.canonical(),
                                   sort_keys=True) + "\n")

    def __len__(self) -> int:
        return len(self._records)

    def decisions(self) -> list[PromotionDecision]:
        return list(self._records)
