"""Capability acquisition pipeline: experience -> candidate -> validation ->
independent evaluation -> assurance -> regression -> PROMOTE / REJECT.

The promotion gate is a governance choke point (ADAM GovernanceGate shape):
a single authorize path, every decision recorded with evidence. A candidate
is NEVER promoted on self-reported metrics or a single success.
"""

from __future__ import annotations

import json

from air.assurance.probes import EvaluatorVerdict, SystemVerdict
from air.capabilities.models import Capability, CapabilityEffect, CapabilityStatus
from air.capabilities.store import CapabilityStore
from air.evaluation.suites import Verdict
from air.events.fabric import utcnow


class GateBlocked(Exception):
    def __init__(self, reasons: list[str]) -> None:
        super().__init__("; ".join(reasons))
        self.reasons = reasons


class CapabilityPipeline:
    def __init__(self, conn, emit=None) -> None:
        self._conn = conn
        self._store = CapabilityStore(conn)
        self._emit = emit  # async callable(type, payload) or None

    async def _event(self, type: str, capability_id: str,
                     payload: dict) -> None:
        if self._emit:
            await self._emit(type, capability_id=capability_id, payload=payload)

    # ------------------------------------------------------------- acquisition
    async def propose(self, name: str, description: str,
                      effect: CapabilityEffect | None = None,
                      created_from: str | None = None,
                      provenance: dict | None = None) -> Capability:
        cap = self._store.propose(name, description, effect=effect,
                                  created_from=created_from,
                                  provenance=provenance or {})
        await self._event("capability.proposed", cap.capability_id,
                          {"name": name})
        return cap

    async def validate(self, capability_id: str, task_run_ids: list[str],
                       suite=None,
                       evaluator_name: str = "air-default-evaluator"):
        """Validation stage: run the evaluation suite over representative
        task runs executed WITH the candidate capability, then aggregate to a
        capability-level verdict. The candidate is never validated on one run.
        """
        from air.evaluation.suites import (EvalCase, EvalSuite, Evaluator,
                                           Verdict)
        cap = self._store.get(capability_id)
        if cap is None:
            raise ValueError(f"capability not found: {capability_id}")
        if not task_run_ids:
            raise GateBlocked(["validation needs at least one task run"])
        suite = suite or EvalSuite(name="capability-validation", cases=[
            EvalCase(id="c1", name="execution evidence", check="event_evidence",
                     params={"required": ["tool.completed"]}),
            EvalCase(id="c2", name="no failures", check="no_failures",
                     params={}),
        ])
        evaluator = Evaluator(self._conn, name=evaluator_name)
        evaluator.save_suite(suite)
        verdicts = []
        for run_id in task_run_ids:
            r = evaluator.evaluate_run(run_id, suite)
            verdicts.append(r.verdict)
        if all(v == Verdict.SUPPORTED for v in verdicts):
            agg = Verdict.SUPPORTED
        elif any(v == Verdict.FALSIFIED for v in verdicts):
            agg = Verdict.FALSIFIED
        elif all(v == Verdict.INVALID for v in verdicts):
            agg = Verdict.INVALID
        else:
            agg = Verdict.INCONCLUSIVE
        # Capability-level evaluation row: the subject is the capability.
        import hashlib
        import uuid as _uuid
        ev_id = "eval_" + _uuid.uuid4().hex[:12]
        subject = {"kind": "capability", "capability_id": capability_id,
                   "task_runs": task_run_ids,
                   "per_run": [v.value for v in verdicts]}
        self._conn.execute(
            """INSERT INTO evaluation_runs (id, suite_id, subject, evaluator,
               evaluator_version, metrics, verdict, evidence, started_at,
               completed_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (ev_id, suite.id, json.dumps(subject), evaluator.name,
             evaluator.version,
             json.dumps({"runs": len(task_run_ids),
                         "supported": sum(1 for v in verdicts
                                          if v == Verdict.SUPPORTED)}),
             agg.value,
             json.dumps({"evidence_hash": hashlib.sha256(
                 json.dumps(subject, sort_keys=True).encode()).hexdigest()}),
             utcnow(), utcnow()),
        )
        self._conn.commit()
        self._store.set_status(capability_id, CapabilityStatus.VALIDATING)
        await self._event("capability.validated", capability_id,
                          {"evaluation_id": ev_id,
                           "verdict": agg.value})
        return ev_id, agg

    def latest_evaluation(self, capability_id: str) -> dict | None:
        row = self._conn.execute(
            """SELECT id, verdict, evaluator, evaluator_version FROM evaluation_runs
               WHERE json_extract(subject, '$.kind') = 'capability'
                 AND json_extract(subject, '$.capability_id') = ?
               ORDER BY completed_at DESC LIMIT 1""",
            (capability_id,)).fetchone()
        if not row:
            return None
        return {"id": row[0], "verdict": row[1], "evaluator": row[2],
                "evaluator_version": row[3]}

    def latest_assurance(self, evaluation_id: str) -> dict | None:
        row = self._conn.execute(
            """SELECT id, evaluator_verdict, system_verdict, false_accepts
               FROM assurance_runs
               WHERE json_extract(target, '$.id') = ?
               ORDER BY completed_at DESC LIMIT 1""",
            (evaluation_id,)).fetchone()
        if not row:
            return None
        return {"id": row[0], "evaluator_verdict": row[1],
                "system_verdict": row[2], "false_accepts": row[3]}

    def promotion_gate(self, capability_id: str) -> tuple[bool, list[str]]:
        """The gate. Returns (ok, reasons). All reasons are recorded."""
        reasons: list[str] = []
        cap = self._store.get(capability_id)
        if cap is None:
            return False, [f"capability not found: {capability_id}"]
        if cap.validation_status == CapabilityStatus.PROMOTED:
            return False, ["already promoted"]
        if cap.validation_status == CapabilityStatus.REJECTED:
            return False, ["rejected capabilities cannot be promoted; propose anew"]

        ev = self.latest_evaluation(capability_id)
        if ev is None:
            reasons.append("no evaluation run for this capability")
        elif ev["verdict"] != Verdict.SUPPORTED.value:
            reasons.append(f"evaluation verdict is {ev['verdict']}, need SUPPORTED")
        else:
            reasons.append(f"evaluation {ev['id']}: SUPPORTED by "
                           f"{ev['evaluator']} {ev['evaluator_version']}")

        assurance_ok = False
        if ev is not None:
            ar = self.latest_assurance(ev["id"])
            if ar is None:
                reasons.append("no assurance run for the latest evaluation")
            elif ar["evaluator_verdict"] != EvaluatorVerdict.SOUND.value:
                reasons.append(f"evaluator verdict is {ar['evaluator_verdict']}:"
                               " evaluator is not trustworthy")
            elif ar["system_verdict"] != SystemVerdict.SUPPORTED.value:
                reasons.append(f"assurance system verdict is {ar['system_verdict']}")
            else:
                assurance_ok = True
                reasons.append(f"assurance {ar['id']}: evaluator SOUND,"
                               " system SUPPORTED")

        # Regression: any FALSIFIED evaluation after the supporting one blocks.
        if ev is not None:
            reg = self._conn.execute(
                """SELECT COUNT(*) FROM evaluation_runs
                   WHERE json_extract(subject, '$.kind') = 'capability'
                     AND json_extract(subject, '$.capability_id') = ?
                     AND verdict = 'FALSIFIED' AND completed_at > (
                       SELECT completed_at FROM evaluation_runs WHERE id = ?)""",
                (capability_id, ev["id"])).fetchone()
            if reg and reg[0] > 0:
                reasons.append(f"{reg[0]} falsifying evaluation(s) after the"
                               " supporting one: regression")

        ok = (ev is not None and ev["verdict"] == Verdict.SUPPORTED.value
              and assurance_ok)
        return ok, reasons

    async def promote(self, capability_id: str, decided_by: str = "operator") -> Capability:
        ok, reasons = self.promotion_gate(capability_id)
        await self._event("capability.promotion_reviewed", capability_id,
                          {"decided_by": decided_by, "reasons": reasons,
                           "approved": ok})
        if not ok:
            raise GateBlocked(reasons)
        cap = self._store.set_status(capability_id, CapabilityStatus.PROMOTED,
                                     version_note=f"promoted by {decided_by}")
        await self._event("capability.promoted", capability_id,
                          {"decided_by": decided_by, "reasons": reasons})
        return cap

    async def reject(self, capability_id: str, reason: str,
                     decided_by: str = "operator") -> Capability:
        cap = self._store.set_status(
            capability_id, CapabilityStatus.REJECTED,
            version_note=f"rejected by {decided_by}: {reason}")
        await self._event("capability.rejected", capability_id,
                          {"decided_by": decided_by, "reason": reason})
        return cap

    async def rollback(self, capability_id: str,
                       decided_by: str = "operator") -> Capability:
        """Rollback a promoted capability. The allocator stops applying its
        effect immediately; history is preserved."""
        cap = self._store.get(capability_id)
        if cap is None or cap.validation_status != CapabilityStatus.PROMOTED:
            raise GateBlocked([f"cannot rollback {capability_id}: not promoted"])
        cap = self._store.set_status(
            capability_id, CapabilityStatus.DEPRECATED,
            version_note=f"rolled back by {decided_by} at {utcnow()}")
        await self._event("capability.rolled_back", capability_id,
                          {"decided_by": decided_by})
        return cap

    def record_use(self, capability_id: str, success: bool) -> None:
        self._store.record_use(capability_id, success)
