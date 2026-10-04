"""Validated learning bridge: the ONLY path from experience to memory.

Enforced pipeline:

    successful run
         ↓
    experience (from event history, not self-report)
         ↓
    evidence (tool/test/observation records)
         ↓
    evaluation (verdict SUPPORTED, independent evaluator)
         ↓
    assurance (evaluator SOUND, system SUPPORTED)
         ↓
    confidence / validity assessment
         ↓
    candidate reusable knowledge  →  memory (DERIVED, never OBSERVED)

There is deliberately NO run -> memory shortcut. Memory records what the
system knows; evaluation and assurance determine what deserves trust. Memory
must never become the authority that decides whether the system improved —
that authority lives in the evaluation/assurance verdicts, which this bridge
checks before writing anything.
"""

from __future__ import annotations

import json

from air.experience.provenance import Provenance
from air.memory.store import MemoryStore, MemoryType, Scope


class BridgeBlocked(Exception):
    def __init__(self, reasons: list[str]) -> None:
        super().__init__("; ".join(reasons))
        self.reasons = reasons


class LearningBridge:
    def __init__(self, conn) -> None:
        self._conn = conn
        self._memory = MemoryStore(conn)

    def assess(self, experience_id: str) -> tuple[bool, list[str]]:
        """Confidence/validity assessment for an experience. Returns
        (ok, reasons). Nothing is written by assessment."""
        reasons: list[str] = []
        row = self._conn.execute(
            "SELECT run_id, goal, outcomes, evaluation_refs, assurance_refs,"
            " dimensions FROM experiences WHERE id=?", (experience_id,)).fetchone()
        if not row:
            return False, [f"experience not found: {experience_id}"]
        _, goal, outcomes_json, eval_refs_json, assur_refs_json, _ = row
        outcomes = json.loads(outcomes_json or "{}")
        if outcomes.get("status") != "COMPLETED":
            return False, [f"experience outcome is {outcomes.get('status')},"
                            " not COMPLETED: nothing to learn"]
        eval_refs = json.loads(eval_refs_json or "[]")
        assur_refs = json.loads(assur_refs_json or "[]")
        if not eval_refs:
            reasons.append("no evaluation refs: experience was never"
                           " independently evaluated")
        else:
            for eid in eval_refs:
                r = self._conn.execute(
                    "SELECT verdict, evaluator FROM evaluation_runs WHERE id=?",
                    (eid,)).fetchone()
                if not r:
                    reasons.append(f"evaluation {eid} not found")
                elif r[0] != "SUPPORTED":
                    reasons.append(f"evaluation {eid} verdict is {r[0]},"
                                   " need SUPPORTED")
                else:
                    reasons.append(f"evaluation {eid}: SUPPORTED by {r[1]}")
        if not assur_refs:
            reasons.append("no assurance refs: the evaluator was never"
                           " independently assured")
        else:
            for aid in assur_refs:
                r = self._conn.execute(
                    "SELECT evaluator_verdict, system_verdict FROM assurance_runs"
                    " WHERE id=?", (aid,)).fetchone()
                if not r:
                    reasons.append(f"assurance {aid} not found")
                elif r[0] != "SOUND":
                    reasons.append(f"assurance {aid}: evaluator verdict is"
                                   f" {r[0]}, need SOUND: evaluator untrustworthy")
                elif r[1] != "SUPPORTED":
                    reasons.append(f"assurance {aid}: system verdict is {r[1]}")
                else:
                    reasons.append(f"assurance {aid}: evaluator SOUND,"
                                   " system SUPPORTED")
        ok = (bool(eval_refs) and bool(assur_refs)
              and not any("need " in r or "not found" in r or "never" in r
                          for r in reasons))
        return ok, reasons

    def promote_to_knowledge(self, experience_id: str, namespace: str,
                             knowledge: dict, memory_type: str = "semantic",
                             confidence: float | None = None) -> str:
        """Write validated knowledge to memory. Blocked unless assess() passes.

        The memory is stored with provenance DERIVED (computed from validated
        experience) — never OBSERVED — with validated_by pointing at the
        evaluation and assurance that earned it.
        """
        ok, reasons = self.assess(experience_id)
        if not ok:
            raise BridgeBlocked(reasons)
        row = self._conn.execute(
            "SELECT evaluation_refs, assurance_refs FROM experiences"
            " WHERE id=?", (experience_id,)).fetchone()
        eval_refs = json.loads(row[0] or "[]")
        assur_refs = json.loads(row[1] or "[]")
        mem = self._memory.store(
            namespace, MemoryType(memory_type), knowledge,
            scope=Scope.GLOBAL, provenance=Provenance.DERIVED,
            provenance_detail={"validated_by": eval_refs,
                               "assurance": assur_refs,
                               "source_experience": experience_id},
            confidence=confidence if confidence is not None else 0.7,
            importance=0.8, source_run_id=None)
        return mem.id

    def propose_capability_from_experience(
            self, experience_id: str, name: str, description: str,
            effect: dict | None = None) -> str:
        """Candidate capability from a validated experience. The candidate
        still has to pass the capability pipeline's own evaluation +
        assurance before promotion — this only creates the CANDIDATE."""
        ok, reasons = self.assess(experience_id)
        if not ok:
            raise BridgeBlocked(reasons)
        from air.capabilities.models import CapabilityEffect
        from air.capabilities.pipeline import CapabilityPipeline
        pipeline = CapabilityPipeline(self._conn)
        import asyncio
        eff = CapabilityEffect(**effect) if effect else None
        cap = asyncio.run(pipeline.propose(
            name, description, effect=eff, created_from=experience_id,
            provenance={"source_experience": experience_id,
                        "assessment": reasons}))
        return cap.capability_id
