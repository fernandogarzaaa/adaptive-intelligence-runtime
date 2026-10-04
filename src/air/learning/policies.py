"""Policy evolution: versioned, gated, rollback-able policy changes.

A policy is a named set of parameters (e.g. "cognitive-allocation" holds the
spawn threshold and strategy boosts the allocator applies). Changes follow:

    Policy v1 -> experience -> candidate v2 -> evaluation -> assurance ->
    held-out validation -> promote | reject; rollback any time.

No silent self-modification: every version records parent, changes, reason,
evidence, evaluation, assurance, and status. Promotion requires an
independent SUPPORTED evaluation and SOUND assurance, same gate as
capabilities.
"""

from __future__ import annotations

import json
import uuid

from pydantic import BaseModel, Field

from air.events.fabric import utcnow


class PolicyVersion(BaseModel):
    """Immutable version. A candidate never mutates the active policy; only
    promote() flips policies.current_version, and only through the gate."""
    id: str = Field(default_factory=lambda: "pv_" + uuid.uuid4().hex[:12])
    policy_id: str
    version: str
    parent_version: str | None = None
    # Full candidate lineage (Inan's contract):
    source_experiences: list[str] = Field(default_factory=list)
    hypothesis: str = ""
    changes: dict = Field(default_factory=dict)          # proposed_changes
    rationale: str = ""
    expected_effect: dict = Field(default_factory=dict)
    constraints: dict = Field(default_factory=dict)     # guardrails
    generated_by: str = "unknown"
    reason: str = ""
    evidence: dict = Field(default_factory=dict)
    evaluation: dict = Field(default_factory=dict)
    assurance: dict = Field(default_factory=dict)
    status: str = "CANDIDATE"  # CANDIDATE | PROMOTED | REJECTED | DEPRECATED
    created_at: str = Field(default_factory=utcnow)


class GateBlocked(Exception):
    def __init__(self, reasons: list[str]) -> None:
        super().__init__("; ".join(reasons))
        self.reasons = reasons


class PolicyStore:
    def __init__(self, conn, emit=None) -> None:
        self._conn = conn
        self._emit = emit

    async def _event(self, type: str, payload: dict) -> None:
        if self._emit:
            await self._emit(type, payload=payload)

    def ensure(self, name: str, initial_params: dict | None = None) -> str:
        """Create the policy with a v1 if it does not exist. Returns policy id.

        Race-safe: concurrent creators use INSERT OR IGNORE, then the
        winner's row is read back. Exactly one v1 exists per name.
        """
        pid = "pol_" + uuid.uuid4().hex[:12]
        now = utcnow()
        with self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO policies (id, name, current_version,"
                " created_at) VALUES (?, ?, '1', ?)", (pid, name, now))
            row = self._conn.execute(
                "SELECT id FROM policies WHERE name=?", (name,)).fetchone()
            assert row is not None
            if row[0] == pid:
                self._persist_version(PolicyVersion(
                    policy_id=pid, version="1",
                    changes=initial_params or {},
                    reason="initial policy version", status="PROMOTED"))
        self._conn.commit()
        return row[0]

    def current(self, name: str) -> PolicyVersion | None:
        pid = self.ensure(name)
        row = self._conn.execute(
            "SELECT current_version FROM policies WHERE id=?", (pid,)).fetchone()
        if not row:
            return None
        return self.get_version(pid, row[0])

    _VERSION_COLS = ("id, policy_id, version, parent_version, changes,"
                     " hypothesis, expected_effect, constraints, generated_by,"
                     " source_experiences, reason, evidence, evaluation,"
                     " assurance, status, created_at")

    def get_version(self, policy_id: str, version: str) -> PolicyVersion | None:
        row = self._conn.execute(
            f"SELECT {self._VERSION_COLS} FROM policy_versions"
            " WHERE policy_id=? AND version=?",
            (policy_id, version)).fetchone()
        return self._row(row) if row else None

    def history(self, name: str) -> list[PolicyVersion]:
        pid = self.ensure(name)
        rows = self._conn.execute(
            f"SELECT {self._VERSION_COLS} FROM policy_versions"
            " WHERE policy_id=? ORDER BY created_at",
            (pid,)).fetchall()
        return [self._row(r) for r in rows]

    def current_effects(self, name: str) -> list[dict]:
        """Current policy params as allocator effect dicts (data, not code)."""
        ver = self.current(name)
        if ver is None:
            return []
        params = ver.changes
        effects: list[dict] = []
        for strategy, boost in (params.get("strategy_boosts") or {}).items():
            effects.append({"type": "strategy_boost", "strategy": strategy,
                            "value": float(boost),
                            "source": f"policy:{name}@v{ver.version}"})
        if "spawn_threshold" in params:
            effects.append({"type": "spawn_threshold",
                            "value": float(params["spawn_threshold"]),
                            "source": f"policy:{name}@v{ver.version}"})
        return effects

    async def propose(self, name: str, params: dict, reason: str,
                      evidence: dict | None = None,
                      hypothesis: str = "",
                      expected_effect: dict | None = None,
                      constraints: dict | None = None,
                      generated_by: str = "unknown",
                      source_experiences: list[str] | None = None) -> PolicyVersion:
        """Propose a candidate version. Never mutates the active policy:
        policies.current_version is untouched until promote() passes the gate."""
        pid = self.ensure(name)
        cur = self.current(name)
        parent = cur.version if cur else None
        # Version numbers come from the max existing version, not from the
        # active one: multiple open candidates must each get a fresh number.
        maxv = 0
        for (v,) in self._conn.execute(
                "SELECT version FROM policy_versions WHERE policy_id=?",
                (pid,)).fetchall():
            try:
                maxv = max(maxv, int(v))
            except (TypeError, ValueError):
                continue
        nxt = str(maxv + 1) if maxv else "1"
        ver = PolicyVersion(
            policy_id=pid, version=nxt, parent_version=parent,
            changes=params, reason=reason, evidence=evidence or {},
            hypothesis=hypothesis,
            expected_effect=expected_effect or {},
            constraints=constraints or {}, generated_by=generated_by,
            source_experiences=source_experiences or [],
            status="CANDIDATE")
        self._persist_version(ver)
        self._conn.commit()
        await self._event("policy.proposed",
                          {"policy": name, "version": nxt, "reason": reason,
                           "generated_by": generated_by,
                           "parent_version": parent})
        return ver

    def _gate(self, ver: PolicyVersion) -> tuple[bool, list[str]]:
        reasons: list[str] = []
        if ver.status == "PROMOTED":
            return False, ["already promoted"]
        ev = ver.evaluation
        if not ev or ev.get("verdict") != "SUPPORTED":
            reasons.append("needs an independent evaluation with verdict"
                           " SUPPORTED recorded on the version")
        ar = ver.assurance
        if not ar or ar.get("evaluator_verdict") != "SOUND":
            reasons.append("needs assurance with evaluator verdict SOUND")
        elif ar.get("system_verdict") != "SUPPORTED":
            reasons.append(f"assurance system verdict is {ar.get('system_verdict')}")
        ok = not any("needs " in r for r in reasons)
        return ok, reasons

    async def promote(self, name: str, version: str,
                      decided_by: str = "operator",
                      evaluation: dict | None = None,
                      assurance: dict | None = None) -> PolicyVersion:
        pid = self.ensure(name)
        ver = self.get_version(pid, version)
        if ver is None:
            raise GateBlocked([f"version {version} not found"])
        if evaluation:
            ver.evaluation = evaluation
        if assurance:
            ver.assurance = assurance
        # Persist the attached evidence first: the gate reads the version.
        self._persist_version(ver)
        self._conn.commit()
        ok, reasons = self._gate(ver)
        if not ok:
            raise GateBlocked(reasons)
        old = self.current(name)
        if old and old.version != version:
            old.status = "DEPRECATED"
            self._persist_version(old)
        ver.status = "PROMOTED"
        self._persist_version(ver)
        self._conn.execute("UPDATE policies SET current_version=? WHERE id=?",
                           (version, pid))
        self._conn.commit()
        await self._event("policy.promoted",
                          {"policy": name, "version": version,
                           "decided_by": decided_by})
        return ver

    async def reject(self, name: str, version: str, reason: str) -> PolicyVersion:
        pid = self.ensure(name)
        ver = self.get_version(pid, version)
        if ver is None:
            raise ValueError(f"version {version} not found")
        ver.status = "REJECTED"
        ver.reason = ver.reason + f" | rejected: {reason}"
        self._persist_version(ver)
        self._conn.commit()
        await self._event("policy.rejected",
                          {"policy": name, "version": version,
                           "reason": reason})
        return ver

    async def rollback(self, name: str,
                       decided_by: str = "operator") -> PolicyVersion:
        """Legacy single-step rollback (kept for API compat). Prefer the
        auditable request/approve flow below."""
        rb = await self.request_rollback(
            name, reason="operator rollback", evidence={},
            requested_by=decided_by)
        return await self.approve_rollback(rb["id"], approved_by=decided_by)

    async def request_rollback(self, name: str, reason: str, evidence: dict,
                               requested_by: str) -> dict:
        """POLICY_ROLLBACK_REQUESTED. Records intent; changes nothing."""
        pid = self.ensure(name)
        cur = self.current(name)
        if cur is None or not cur.parent_version:
            raise GateBlocked([f"policy {name} has no parent version to"
                               " roll back to"])
        rb_id = "rb_" + uuid.uuid4().hex[:12]
        now = utcnow()
        self._conn.execute(
            """INSERT INTO policy_rollbacks (id, policy_id, policy_name,
               from_version, to_version, reason, evidence, requested_by,
               status, requested_at)
               VALUES (?,?,?,?,?,?,?,?, 'REQUESTED', ?)""",
            (rb_id, pid, name, cur.version, cur.parent_version, reason,
             json.dumps(evidence), requested_by, now))
        self._conn.commit()
        await self._event("policy.rollback_requested",
                          {"policy": name, "rollback_id": rb_id,
                           "from_version": cur.version,
                           "to_version": cur.parent_version,
                           "reason": reason, "requested_by": requested_by})
        return {"id": rb_id, "policy": name, "from_version": cur.version,
                "to_version": cur.parent_version, "status": "REQUESTED"}

    async def approve_rollback(self, rollback_id: str,
                               approved_by: str) -> PolicyVersion:
        """POLICY_ROLLBACK_APPROVED then POLICY_ACTIVATED. The version flip
        happens here, with affected runs computed from the ledger."""
        row = self._conn.execute(
            "SELECT policy_id, policy_name, from_version, to_version, reason,"
            " evidence, requested_by, status FROM policy_rollbacks WHERE id=?",
            (rollback_id,)).fetchone()
        if not row:
            raise GateBlocked([f"rollback {rollback_id} not found"])
        (pid, name, from_v, to_v, reason, evidence_json,
         requested_by, status) = row
        if status != "REQUESTED":
            raise GateBlocked([f"rollback {rollback_id} is {status},"
                               " not REQUESTED"])
        cur = self.current(name)
        if cur is None or cur.version != from_v:
            raise GateBlocked(
                [f"active version is now {cur.version if cur else None},"
                 f" not {from_v}: rollback is stale"])
        failed = self.get_version(pid, from_v)
        parent = self.get_version(pid, to_v)
        if failed is None or parent is None:
            raise GateBlocked(["version rows missing"])
        # Affected runs: created while the failed version was active.
        # runs.policy_version stores the "name@vN" tag.
        affected = [r[0] for r in self._conn.execute(
            "SELECT id FROM runs WHERE policy_version=?",
            (f"{name}@v{from_v}",)).fetchall()]
        failed.status = "DEPRECATED"
        self._persist_version(failed)
        parent.status = "PROMOTED"
        self._persist_version(parent)
        self._conn.execute("UPDATE policies SET current_version=? WHERE id=?",
                           (to_v, pid))
        now = utcnow()
        self._conn.execute(
            "UPDATE policy_rollbacks SET status='APPROVED', approved_by=?,"
            " decided_at=?, affected_runs=? WHERE id=?",
            (approved_by, now, json.dumps(affected), rollback_id))
        self._conn.commit()
        await self._event("policy.rollback_approved",
                          {"policy": name, "rollback_id": rollback_id,
                           "from_version": from_v, "to_version": to_v,
                           "approved_by": approved_by,
                           "affected_runs": affected})
        await self._event("policy.activated",
                          {"policy": name, "version": to_v,
                           "previous_version": from_v,
                           "reason": reason,
                           "triggering_evidence": json.loads(evidence_json or "{}"),
                           "actor": approved_by,
                           "evaluation_refs": failed.evaluation,
                           "assurance_refs": failed.assurance})
        return parent

    def provenance_chain(self, name: str) -> dict:
        """Answer: why is this policy active? The complete auditable
        history: every version in lineage order, parent links, the
        evaluation/assurance each one carried, and which one is active."""
        pid = self.ensure(name)
        cur = self.current(name)
        if cur is None:
            return {"policy": name, "active_version": None, "chain": []}
        rows = self._conn.execute(
            f"SELECT {self._VERSION_COLS} FROM policy_versions"
            " WHERE policy_id=? ORDER BY created_at", (pid,)).fetchall()
        chain = [{
            "version": v.version, "status": v.status,
            "parent_version": v.parent_version,
            "hypothesis": v.hypothesis,
            "generated_by": v.generated_by,
            "source_experiences": v.source_experiences,
            "changes": v.changes,
            "expected_effect": v.expected_effect,
            "constraints": v.constraints,
            "reason": v.reason,
            "evaluation": v.evaluation,
            "assurance": v.assurance,
            "created_at": v.created_at,
        } for v in (self._row(r) for r in rows)]
        return {"policy": name, "active_version": cur.version, "chain": chain}

    # ---------------------------------------------------------------- internal
    def _persist_version(self, ver: PolicyVersion) -> None:
        cur = self._conn.execute(
            "UPDATE policy_versions SET parent_version=?, changes=?,"
            " hypothesis=?, expected_effect=?, constraints=?, generated_by=?,"
            " source_experiences=?, reason=?, evidence=?, evaluation=?,"
            " assurance=?, status=? WHERE id=?",
            (ver.parent_version, json.dumps(ver.changes), ver.hypothesis,
             json.dumps(ver.expected_effect), json.dumps(ver.constraints),
             ver.generated_by, json.dumps(ver.source_experiences), ver.reason,
             json.dumps(ver.evidence), json.dumps(ver.evaluation),
             json.dumps(ver.assurance), ver.status, ver.id))
        if cur.rowcount == 0:
            self._conn.execute(
                """INSERT INTO policy_versions (id, policy_id, version,
                   parent_version, changes, hypothesis, expected_effect,
                   constraints, generated_by, source_experiences, reason,
                   evidence, evaluation, assurance, status, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (ver.id, ver.policy_id, ver.version, ver.parent_version,
                 json.dumps(ver.changes), ver.hypothesis,
                 json.dumps(ver.expected_effect), json.dumps(ver.constraints),
                 ver.generated_by, json.dumps(ver.source_experiences),
                 ver.reason, json.dumps(ver.evidence),
                 json.dumps(ver.evaluation), json.dumps(ver.assurance),
                 ver.status, ver.created_at))

    @staticmethod
    def _row(r) -> PolicyVersion:
        return PolicyVersion(
            id=r[0], policy_id=r[1], version=r[2], parent_version=r[3],
            changes=json.loads(r[4] or "{}"), hypothesis=r[5] or "",
            expected_effect=json.loads(r[6] or "{}"),
            constraints=json.loads(r[7] or "{}"),
            generated_by=r[8] or "unknown",
            source_experiences=json.loads(r[9] or "[]"),
            reason=r[10] or "", evidence=json.loads(r[11] or "{}"),
            evaluation=json.loads(r[12] or "{}"),
            assurance=json.loads(r[13] or "{}"), status=r[14],
            created_at=r[15])
