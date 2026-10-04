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
    id: str = Field(default_factory=lambda: "pv_" + uuid.uuid4().hex[:12])
    policy_id: str
    version: str
    parent_version: str | None = None
    changes: dict = Field(default_factory=dict)
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
        """Create the policy with a v1 if it does not exist. Returns policy id."""
        row = self._conn.execute("SELECT id FROM policies WHERE name=?",
                                 (name,)).fetchone()
        if row:
            return row[0]
        pid = "pol_" + uuid.uuid4().hex[:12]
        now = utcnow()
        self._conn.execute(
            "INSERT INTO policies (id, name, current_version, created_at)"
            " VALUES (?, ?, '1', ?)", (pid, name, now))
        self._persist_version(PolicyVersion(
            policy_id=pid, version="1", changes=initial_params or {},
            reason="initial policy version", status="PROMOTED"))
        self._conn.commit()
        return pid

    def current(self, name: str) -> PolicyVersion | None:
        pid = self.ensure(name)
        row = self._conn.execute(
            "SELECT current_version FROM policies WHERE id=?", (pid,)).fetchone()
        if not row:
            return None
        return self.get_version(pid, row[0])

    def get_version(self, policy_id: str, version: str) -> PolicyVersion | None:
        row = self._conn.execute(
            "SELECT id, policy_id, version, parent_version, changes, reason,"
            " evidence, evaluation, assurance, status, created_at"
            " FROM policy_versions WHERE policy_id=? AND version=?",
            (policy_id, version)).fetchone()
        return self._row(row) if row else None

    def history(self, name: str) -> list[PolicyVersion]:
        pid = self.ensure(name)
        rows = self._conn.execute(
            "SELECT id, policy_id, version, parent_version, changes, reason,"
            " evidence, evaluation, assurance, status, created_at"
            " FROM policy_versions WHERE policy_id=? ORDER BY created_at",
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
                      evidence: dict | None = None) -> PolicyVersion:
        """Propose a candidate version. Never auto-promotes."""
        pid = self.ensure(name)
        cur = self.current(name)
        parent = cur.version if cur else None
        try:
            nxt = str(int(parent) + 1) if parent and parent.isdigit() else "2"
        except ValueError:
            nxt = (parent or "1") + ".1"
        ver = PolicyVersion(policy_id=pid, version=nxt, parent_version=parent,
                            changes=params, reason=reason,
                            evidence=evidence or {}, status="CANDIDATE")
        self._persist_version(ver)
        self._conn.commit()
        await self._event("policy.proposed",
                          {"policy": name, "version": nxt, "reason": reason})
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
        """Roll back to the parent version. Immediate effect on new runs."""
        pid = self.ensure(name)
        cur = self.current(name)
        if cur is None or not cur.parent_version:
            raise GateBlocked([f"policy {name} has no parent version to"
                               " roll back to"])
        parent = self.get_version(pid, cur.parent_version)
        if parent is None:
            raise GateBlocked(["parent version row missing"])
        cur.status = "DEPRECATED"
        self._persist_version(cur)
        parent.status = "PROMOTED"
        self._persist_version(parent)
        self._conn.execute("UPDATE policies SET current_version=? WHERE id=?",
                           (parent.version, pid))
        self._conn.commit()
        await self._event("policy.rolled_back",
                          {"policy": name, "from": cur.version,
                           "to": parent.version, "decided_by": decided_by})
        return parent

    # ---------------------------------------------------------------- internal
    def _persist_version(self, ver: PolicyVersion) -> None:
        cur = self._conn.execute(
            "UPDATE policy_versions SET parent_version=?, changes=?, reason=?,"
            " evidence=?, evaluation=?, assurance=?, status=? WHERE id=?",
            (ver.parent_version, json.dumps(ver.changes), ver.reason,
             json.dumps(ver.evidence), json.dumps(ver.evaluation),
             json.dumps(ver.assurance), ver.status, ver.id))
        if cur.rowcount == 0:
            self._conn.execute(
                """INSERT INTO policy_versions (id, policy_id, version,
                   parent_version, changes, reason, evidence, evaluation,
                   assurance, status, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (ver.id, ver.policy_id, ver.version, ver.parent_version,
                 json.dumps(ver.changes), ver.reason,
                 json.dumps(ver.evidence), json.dumps(ver.evaluation),
                 json.dumps(ver.assurance), ver.status, ver.created_at))

    @staticmethod
    def _row(r) -> PolicyVersion:
        return PolicyVersion(
            id=r[0], policy_id=r[1], version=r[2], parent_version=r[3],
            changes=json.loads(r[4] or "{}"), reason=r[5] or "",
            evidence=json.loads(r[6] or "{}"),
            evaluation=json.loads(r[7] or "{}"),
            assurance=json.loads(r[8] or "{}"), status=r[9],
            created_at=r[10])
