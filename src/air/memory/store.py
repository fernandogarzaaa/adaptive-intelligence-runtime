"""Persistent memory substrate (ADAM-like, owned interfaces).

Memory types: episodic | semantic | procedural | self | experience | capability.
Every memory carries provenance, confidence, importance, visibility, and a
content hash. Secrets are redacted before persistence, never after the fact.
Namespace isolation is enforced at read time; task-scoped memories never leak
across runs unless explicitly shared.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from enum import Enum

from pydantic import BaseModel, Field

from air.events.fabric import canonical, utcnow
from air.security.policy import redact_secrets


class MemoryType(str, Enum):
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    PROCEDURAL = "procedural"
    SELF = "self"
    EXPERIENCE = "experience"
    CAPABILITY = "capability"


class Visibility(str, Enum):
    PRIVATE = "private"
    SHARED = "shared"
    TASK = "task"


class Memory(BaseModel):
    id: str = Field(default_factory=lambda: "mem_" + uuid.uuid4().hex[:12])
    namespace: str
    type: MemoryType
    content: dict
    importance: float = 0.5
    confidence: float = 0.5
    provenance: dict = Field(default_factory=dict)
    created_at: str = Field(default_factory=utcnow)
    updated_at: str = Field(default_factory=utcnow)
    source_run: str | None = None
    source_agent: str | None = None
    visibility: Visibility = Visibility.PRIVATE
    hash: str = ""


def memory_hash(namespace: str, type: str, content: dict) -> str:
    return hashlib.sha256(
        (namespace + "|" + type + "|" + canonical(content)).encode()
    ).hexdigest()


class MemoryStore:
    def __init__(self, conn) -> None:
        self._conn = conn

    def store(self, namespace: str, type: MemoryType | str,
              content: dict, importance: float = 0.5,
              confidence: float = 0.5, provenance: dict | None = None,
              source_run: str | None = None,
              source_agent: str | None = None,
              visibility: Visibility | str = Visibility.PRIVATE) -> Memory:
        # Redact before persistence: secrets never land in the DB.
        clean = json.loads(redact_secrets(json.dumps(content)))
        mem = Memory(
            namespace=namespace,
            type=MemoryType(type),
            content=clean,
            importance=max(0.0, min(1.0, importance)),
            confidence=max(0.0, min(1.0, confidence)),
            provenance=provenance or {},
            source_run=source_run,
            source_agent=source_agent,
            visibility=Visibility(visibility),
        )
        mem.hash = memory_hash(namespace, mem.type.value, clean)
        self._conn.execute(
            """INSERT INTO memories (id, namespace, type, content, importance,
               confidence, provenance, created_at, updated_at, source_run,
               source_agent, visibility, hash)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (mem.id, namespace, mem.type.value, json.dumps(clean),
             mem.importance, mem.confidence, json.dumps(mem.provenance),
             mem.created_at, mem.updated_at, source_run, source_agent,
             mem.visibility.value, mem.hash),
        )
        self._conn.commit()
        return mem

    def get(self, memory_id: str) -> Memory | None:
        row = self._conn.execute(
            "SELECT id, namespace, type, content, importance, confidence,"
            " provenance, created_at, updated_at, source_run, source_agent,"
            " visibility, hash FROM memories WHERE id=?", (memory_id,)).fetchone()
        return self._row(row) if row else None

    def retrieve(self, namespace: str, type: MemoryType | str | None = None,
                 query: str | None = None, include_shared: bool = False,
                 limit: int = 50) -> list[Memory]:
        """Namespace-isolated retrieval. Task-scoped memories of other runs are
        excluded unless include_shared is set and visibility is shared."""
        clauses = ["namespace = ?"]
        params: list = [namespace]
        if type:
            clauses.append("type = ?")
            params.append(MemoryType(type).value)
        if not include_shared:
            clauses.append("visibility != 'task'")
        if query:
            clauses.append("content LIKE ?")
            params.append(f"%{query}%")
        q = ("SELECT id, namespace, type, content, importance, confidence,"
             " provenance, created_at, updated_at, source_run, source_agent,"
             " visibility, hash FROM memories WHERE " + " AND ".join(clauses) +
             " ORDER BY importance DESC, updated_at DESC LIMIT ?")
        params.append(limit)
        return [self._row(r) for r in self._conn.execute(q, params).fetchall()]

    def promote(self, memory_id: str, to_namespace: str,
                to_visibility: Visibility = Visibility.SHARED) -> Memory | None:
        """Promote a memory into a shared namespace (e.g. task -> global).
        The original is kept; promotion is a copy with new provenance."""
        mem = self.get(memory_id)
        if mem is None:
            return None
        return self.store(
            to_namespace, mem.type, mem.content,
            importance=min(1.0, mem.importance + 0.1),
            confidence=mem.confidence,
            provenance={**mem.provenance, "promoted_from": mem.id},
            source_run=mem.source_run, source_agent=mem.source_agent,
            visibility=to_visibility,
        )

    def decay(self, namespace: str, half_life_days: float = 30.0) -> int:
        """Exponential importance decay for episodic memories. Returns count."""
        import math
        now = utcnow()
        rows = self._conn.execute(
            "SELECT id, importance, updated_at FROM memories"
            " WHERE namespace=? AND type='episodic'", (namespace,)).fetchall()
        n = 0
        for mid, imp, updated in rows:
            age_days = max(0.0, (self._ts(now) - self._ts(updated)) / 86400.0)
            new_imp = imp * (0.5 ** (age_days / half_life_days))
            if abs(new_imp - imp) > 1e-9:
                self._conn.execute(
                    "UPDATE memories SET importance=?, updated_at=? WHERE id=?",
                    (new_imp, now, mid))
                n += 1
        self._conn.commit()
        return n

    def delete(self, memory_id: str) -> bool:
        cur = self._conn.execute("DELETE FROM memories WHERE id=?", (memory_id,))
        self._conn.commit()
        return cur.rowcount > 0

    @staticmethod
    def _ts(iso: str) -> float:
        from datetime import datetime
        return datetime.fromisoformat(iso).timestamp()

    @staticmethod
    def _row(r) -> Memory:
        return Memory(
            id=r[0], namespace=r[1], type=MemoryType(r[2]),
            content=json.loads(r[3]), importance=r[4], confidence=r[5],
            provenance=json.loads(r[6] or "{}"), created_at=r[7],
            updated_at=r[8], source_run=r[9], source_agent=r[10],
            visibility=Visibility(r[11]), hash=r[12],
        )
