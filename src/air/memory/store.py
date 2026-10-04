"""Persistent memory substrate v2 (ADAM-like, owned interfaces).

Memory is typed, scoped, provenance-aware, and versionable — not a vector
store. Every memory carries its provenance KIND (observed/derived/inferred/
forecast/simulated/...), and retrieval returns evidence metadata so a
consumer knows WHY a memory was retrieved and whether it is trustworthy.

Non-negotiable invariants (enforced in code, not documentation):
1. An LLM statement must never silently become an observed fact: storing with
   provenance OBSERVED requires an evidence_ref.
2. EXPERIENCE and CAPABILITY memories require a validated_by evaluation ref:
   the path run -> experience -> evidence -> evaluation -> knowledge is the
   ONLY way validated knowledge enters memory. There is no run -> memory
   shortcut.
3. Memory NEVER decides whether the system improved. It records; evaluation
   and assurance determine trust. No method here assesses improvement.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from enum import Enum

from pydantic import BaseModel, Field

from air.events.fabric import canonical, utcnow
from air.experience.provenance import TRUST_CAP, Provenance
from air.security.policy import redact_secrets


class MemoryType(str, Enum):
    EPISODIC = "episodic"      # what happened
    SEMANTIC = "semantic"      # what is believed/known
    PROCEDURAL = "procedural"  # how to perform something
    SELF = "self"              # runtime/agent self-model
    EXPERIENCE = "experience"  # validated observations/outcomes
    CAPABILITY = "capability"  # reusable learned competence


class Scope(str, Enum):
    TASK = "task"      # visible only within the task namespace that created it
    AGENT = "agent"    # visible to the agent's namespace
    RUN = "run"        # visible within the run
    GLOBAL = "global"  # shared across runs


class MemoryStatus(str, Enum):
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
    DEPRECATED = "DEPRECATED"


class Memory(BaseModel):
    id: str = Field(default_factory=lambda: "mem_" + uuid.uuid4().hex[:12])
    namespace: str
    scope: Scope = Scope.RUN
    type: MemoryType
    content: dict
    importance: float = 0.5
    confidence: float = 0.5
    provenance: Provenance = Provenance.DERIVED
    provenance_detail: dict = Field(default_factory=dict)
    source_run_id: str | None = None
    source_agent_id: str | None = None
    source_event_id: str | None = None
    created_at: str = Field(default_factory=utcnow)
    observed_at: str = Field(default_factory=utcnow)
    updated_at: str = Field(default_factory=utcnow)
    status: MemoryStatus = MemoryStatus.ACTIVE
    version: int = 1
    supersedes: str | None = None
    content_hash: str = ""


class RetrievedMemory(BaseModel):
    """A memory plus the evidence metadata for why it was retrieved."""
    memory: Memory
    why_retrieved: str
    trust: float
    trust_flags: list[str] = Field(default_factory=list)


def content_hash(namespace: str, type: str, content: dict) -> str:
    return hashlib.sha256(
        (namespace + "|" + type + "|" + canonical(content)).encode()
    ).hexdigest()


def trust_of(mem: Memory) -> tuple[float, list[str]]:
    """Compute retrieval-time trust. Caps by provenance; never invents trust."""
    trust = mem.confidence
    flags: list[str] = []
    cap = TRUST_CAP.get(mem.provenance)
    if cap is not None:
        flags.append(f"provenance={mem.provenance.value} caps trust at {cap}")
        trust = min(trust, cap)
    if mem.provenance == Provenance.USER_ASSERTED:
        flags.append("user-asserted: trusted as assertion, not measurement")
    if mem.status != MemoryStatus.ACTIVE:
        flags.append(f"status={mem.status.value}")
        trust *= 0.5
    if mem.version > 1:
        flags.append(f"version {mem.version} (supersedes earlier)")
    return round(trust, 4), flags


class MemoryStore:
    def __init__(self, conn) -> None:
        self._conn = conn

    # ------------------------------------------------------------------ write
    def store(self, namespace: str, type: MemoryType | str,
              content: dict, scope: Scope | str = Scope.RUN,
              provenance: Provenance | str = Provenance.DERIVED,
              provenance_detail: dict | None = None,
              importance: float = 0.5, confidence: float = 0.5,
              source_run_id: str | None = None,
              source_agent_id: str | None = None,
              source_event_id: str | None = None,
              observed_at: str | None = None) -> Memory:
        provenance = Provenance(provenance)
        detail = provenance_detail or {}
        # Invariant 1: OBSERVED requires evidence.
        if provenance == Provenance.OBSERVED and not detail.get("evidence_ref"):
            raise ValueError(
                "provenance OBSERVED requires provenance_detail['evidence_ref']:"
                " an LLM statement must never silently become an observed fact")
        # Invariant 2: validated knowledge only via evaluation.
        if MemoryType(type) in (MemoryType.EXPERIENCE, MemoryType.CAPABILITY) \
                and not detail.get("validated_by"):
            raise ValueError(
                f"memory type {MemoryType(type).value} requires"
                " provenance_detail['validated_by'] (evaluation id): the only"
                " path is run -> experience -> evidence -> evaluation")
        clean = json.loads(redact_secrets(json.dumps(content)))
        mem = Memory(
            namespace=namespace, scope=Scope(scope), type=MemoryType(type),
            content=clean, importance=max(0.0, min(1.0, importance)),
            confidence=max(0.0, min(1.0, confidence)),
            provenance=provenance, provenance_detail=detail,
            source_run_id=source_run_id, source_agent_id=source_agent_id,
            source_event_id=source_event_id,
            observed_at=observed_at or utcnow(),
        )
        mem.content_hash = content_hash(namespace, mem.type.value, clean)
        self._insert(mem)
        return mem

    def update(self, memory_id: str, content: dict,
               confidence: float | None = None) -> Memory | None:
        """Versioned update: the old row is SUPERSEDED, a new version is born.
        History is preserved; nothing is silently overwritten."""
        old = self.get(memory_id)
        if old is None or old.status != MemoryStatus.ACTIVE:
            return None
        clean = json.loads(redact_secrets(json.dumps(content)))
        new = Memory(
            namespace=old.namespace, scope=old.scope, type=old.type,
            content=clean, importance=old.importance,
            confidence=old.confidence if confidence is None else confidence,
            provenance=old.provenance, provenance_detail=old.provenance_detail,
            source_run_id=old.source_run_id, source_agent_id=old.source_agent_id,
            source_event_id=old.source_event_id, observed_at=old.observed_at,
            version=old.version + 1, supersedes=old.id,
        )
        new.content_hash = content_hash(new.namespace, new.type.value, clean)
        self._conn.execute("UPDATE memories SET status='SUPERSEDED',"
                           " updated_at=? WHERE id=?", (utcnow(), old.id))
        self._insert(new)
        self._conn.commit()
        return new

    def deprecate(self, memory_id: str) -> bool:
        cur = self._conn.execute(
            "UPDATE memories SET status='DEPRECATED', updated_at=?"
            " WHERE id=? AND status='ACTIVE'", (utcnow(), memory_id))
        self._conn.commit()
        return cur.rowcount > 0

    def forget(self, memory_id: str) -> bool:
        """Hard delete. Forgetting is real deletion, not a flag."""
        cur = self._conn.execute("DELETE FROM memories WHERE id=?",
                                 (memory_id,))
        self._conn.commit()
        return cur.rowcount > 0

    # ------------------------------------------------------------------- read
    def get(self, memory_id: str) -> Memory | None:
        row = self._conn.execute(
            "SELECT id, namespace, scope, type, content, importance,"
            " confidence, provenance_kind, provenance, created_at, observed_at,"
            " updated_at, source_run, source_agent, source_event_id, status,"
            " version, supersedes, hash FROM memories WHERE id=?",
            (memory_id,)).fetchone()
        return self._row(row) if row else None

    def history(self, memory_id: str) -> list[Memory]:
        """Version chain, newest first."""
        chain, cur = [], memory_id
        while cur:
            mem = self.get(cur)
            if mem is None:
                break
            chain.append(mem)
            cur = mem.supersedes
        return chain

    def retrieve(self, namespace: str,
                 scopes: tuple[str, ...] = ("global",),
                 type: MemoryType | str | None = None,
                 query: str | None = None,
                 include_deprecated: bool = False,
                 limit: int = 50) -> list[RetrievedMemory]:
        """Scoped retrieval with evidence metadata.

        By default only GLOBAL memories are visible: task/agent/run memories
        never leak across namespaces unless the caller explicitly opts into
        those scopes AND the namespace matches.
        """
        scope_list = [Scope(s).value for s in scopes]
        clauses = ["namespace = ?",
                   f"scope IN ({','.join('?' * len(scope_list))})"]
        params: list = [namespace, *scope_list]
        if type:
            clauses.append("type = ?")
            params.append(MemoryType(type).value)
        if not include_deprecated:
            clauses.append("status = 'ACTIVE'")
        if query:
            clauses.append("content LIKE ?")
            params.append(f"%{query}%")
        q = ("SELECT id, namespace, scope, type, content, importance,"
             " confidence, provenance_kind, provenance, created_at, observed_at,"
             " updated_at, source_run, source_agent, source_event_id, status,"
             " version, supersedes, hash FROM memories WHERE "
             + " AND ".join(clauses) +
             " ORDER BY importance DESC, updated_at DESC LIMIT ?")
        params.append(limit)
        out = []
        for r in self._conn.execute(q, params).fetchall():
            mem = self._row(r)
            trust, flags = trust_of(mem)
            why = "query match" if query else "top importance"
            if mem.scope != Scope.GLOBAL:
                why += f" (scope={mem.scope.value})"
            out.append(RetrievedMemory(memory=mem, why_retrieved=why,
                                       trust=trust, trust_flags=flags))
        return out

    def decay(self, namespace: str, half_life_days: float = 30.0) -> int:
        import math
        now = utcnow()
        rows = self._conn.execute(
            "SELECT id, importance, updated_at FROM memories"
            " WHERE namespace=? AND type='episodic' AND status='ACTIVE'",
            (namespace,)).fetchall()
        n = 0
        for mid, imp, updated in rows:
            age_days = max(0.0, (self._ts(now) - self._ts(updated)) / 86400.0)
            new_imp = imp * (0.5 ** (age_days / half_life_days))
            if abs(new_imp - imp) > 1e-9:
                self._conn.execute(
                    "UPDATE memories SET importance=?, updated_at=?"
                    " WHERE id=?", (new_imp, now, mid))
                n += 1
        self._conn.commit()
        return n

    # ---------------------------------------------------------------- internal
    def _insert(self, mem: Memory) -> None:
        self._conn.execute(
            """INSERT INTO memories (id, namespace, scope, type, content,
               importance, confidence, provenance_kind, provenance, created_at,
               observed_at, updated_at, source_run, source_agent,
               source_event_id, status, version, supersedes, hash)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (mem.id, mem.namespace, mem.scope.value, mem.type.value,
             json.dumps(mem.content), mem.importance, mem.confidence,
             mem.provenance.value, json.dumps(mem.provenance_detail),
             mem.created_at, mem.observed_at, mem.updated_at,
             mem.source_run_id, mem.source_agent_id, mem.source_event_id,
             mem.status.value, mem.version, mem.supersedes,
             mem.content_hash),
        )
        self._conn.commit()

    @staticmethod
    def _ts(iso: str) -> float:
        from datetime import datetime
        return datetime.fromisoformat(iso).timestamp()

    @staticmethod
    def _row(r) -> Memory:
        return Memory(
            id=r[0], namespace=r[1], scope=Scope(r[2] or "run"),
            type=MemoryType(r[3]), content=json.loads(r[4]),
            importance=r[5], confidence=r[6],
            provenance=Provenance(r[7] or "DERIVED"),
            provenance_detail=json.loads(r[8] or "{}"),
            created_at=r[9], observed_at=r[10] or r[9], updated_at=r[11],
            source_run_id=r[12], source_agent_id=r[13],
            source_event_id=r[14],
            status=MemoryStatus(r[15] or "ACTIVE"), version=r[16] or 1,
            supersedes=r[17], content_hash=r[18] or "",
        )
