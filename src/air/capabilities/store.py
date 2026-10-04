"""Capability store: persistence, performance tracking, confidence.

Confidence is a Laplace-smoothed success rate: (successes + 1) / (uses + 2).
Documented heuristic, not a claim of calibration.
"""

from __future__ import annotations

import json
import uuid

from air.capabilities.models import Capability, CapabilityEffect, CapabilityStatus
from air.events.fabric import utcnow


class CapabilityStore:
    def __init__(self, conn) -> None:
        self._conn = conn

    def propose(self, name: str, description: str,
                effect: CapabilityEffect | None = None,
                created_from: str | None = None,
                provenance: dict | None = None, **kw) -> Capability:
        cap = Capability(name=name, description=description, effect=effect,
                         created_from=created_from,
                         provenance=provenance or {}, **kw)
        self._persist(cap)
        return cap

    def get(self, capability_id: str) -> Capability | None:
        row = self._conn.execute(
            "SELECT capability_id, name, description, version, requirements,"
            " tools, model_requirements, policy, effect, performance, confidence,"
            " provenance, validation_status, created_from, lineage,"
            " created_at, updated_at FROM capabilities WHERE capability_id=?",
            (capability_id,)).fetchone()
        return self._row(row) if row else None

    def list(self, status: CapabilityStatus | None = None) -> list[Capability]:
        q = ("SELECT capability_id, name, description, version, requirements,"
             " tools, model_requirements, policy, effect, performance, confidence,"
             " provenance, validation_status, created_from, lineage,"
             " created_at, updated_at FROM capabilities")
        params: list = []
        if status:
            q += " WHERE validation_status=?"
            params.append(status.value)
        q += " ORDER BY updated_at DESC"
        return [self._row(r) for r in self._conn.execute(q, params).fetchall()]

    def active_effects(self) -> list[dict]:
        """Effects of PROMOTED capabilities as plain dicts (data, not code),
        each tagged with its source capability for audit."""
        out = []
        for c in self.list(CapabilityStatus.PROMOTED):
            if c.effect is not None:
                d = c.effect.model_dump()
                d["source"] = c.capability_id
                out.append(d)
        return out

    def record_use(self, capability_id: str, success: bool) -> Capability | None:
        cap = self.get(capability_id)
        if cap is None:
            return None
        perf = cap.performance
        perf["uses"] += 1
        perf["successes" if success else "failures"] += 1
        cap.performance = perf
        cap.confidence = round((perf["successes"] + 1) / (perf["uses"] + 2), 4)
        cap.updated_at = utcnow()
        self._persist(cap)
        return cap

    def set_status(self, capability_id: str, status: CapabilityStatus,
                   version_note: str | None = None) -> Capability | None:
        cap = self.get(capability_id)
        if cap is None:
            return None
        cap.validation_status = status
        cap.updated_at = utcnow()
        if version_note:
            cap.lineage = cap.lineage + [version_note]
        self._persist(cap)
        # Version history row (hash-chained assurance record lives in events).
        self._conn.execute(
            "INSERT INTO capability_versions (id, capability_id, version, changes,"
            " status, created_at) VALUES (?,?,?,?,?,?)",
            ("cv_" + uuid.uuid4().hex[:12],
             capability_id, cap.version, json.dumps({"status": status.value}),
             status.value, utcnow()),
        )
        self._conn.commit()
        return cap

    def _persist(self, cap: Capability) -> None:
        effect_json = cap.effect.model_dump_json() if cap.effect else None
        cur = self._conn.execute(
            "UPDATE capabilities SET name=?, description=?, version=?,"
            " requirements=?, tools=?, model_requirements=?, policy=?,"
            " effect=?, performance=?, confidence=?, provenance=?,"
            " validation_status=?, created_from=?, lineage=?, updated_at=?"
            " WHERE capability_id=?",
            (cap.name, cap.description, cap.version,
             json.dumps(cap.requirements), json.dumps(cap.tools),
             json.dumps(cap.model_requirements), json.dumps(cap.policy),
             effect_json, json.dumps(cap.performance), cap.confidence,
             json.dumps(cap.provenance), cap.validation_status.value,
             cap.created_from, json.dumps(cap.lineage), cap.updated_at,
             cap.capability_id))
        if cur.rowcount == 0:
            self._conn.execute(
                """INSERT INTO capabilities (capability_id, name, description,
                   version, requirements, tools, model_requirements, policy,
                   effect, performance, confidence, provenance,
                   validation_status, created_from, lineage, created_at,
                   updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (cap.capability_id, cap.name, cap.description, cap.version,
                 json.dumps(cap.requirements), json.dumps(cap.tools),
                 json.dumps(cap.model_requirements), json.dumps(cap.policy),
                 effect_json, json.dumps(cap.performance), cap.confidence,
                 json.dumps(cap.provenance), cap.validation_status.value,
                 cap.created_from, json.dumps(cap.lineage), cap.created_at,
                 cap.updated_at))
        self._conn.commit()

    @staticmethod
    def _row(r) -> Capability:
        return Capability(
            capability_id=r[0], name=r[1], description=r[2], version=r[3],
            requirements=json.loads(r[4] or "{}"),
            tools=json.loads(r[5] or "[]"),
            model_requirements=json.loads(r[6] or "{}"),
            policy=json.loads(r[7] or "{}"),
            effect=(CapabilityEffect.model_validate_json(r[8])
                    if r[8] else None),
            performance=json.loads(r[9] or "{}"),
            confidence=r[10], provenance=json.loads(r[11] or "{}"),
            validation_status=CapabilityStatus(r[12]), created_from=r[13],
            lineage=json.loads(r[14] or "[]"), created_at=r[15],
            updated_at=r[16],
        )
