"""Memory v2: typed, scoped, provenance-aware, versionable.

Invariants under test:
- OBSERVED requires an evidence_ref (LLM output can never silently become fact)
- EXPERIENCE/CAPABILITY memories require validated_by (no run->memory shortcut)
- Retrieval returns evidence metadata (why + trust + flags)
- Scopes are enforced: task memories never leak across namespaces
- Updates are versioned, never silent overwrites
"""

import pytest

from pathlib import Path

from air.experience.provenance import Provenance
from air.memory.store import (
    MemoryStatus, MemoryStore, MemoryType, Scope, trust_of,
)
from air.persistence.db import Database, find_migrations_dir


def _store(tmp_path):
    db = Database(tmp_path / "m.db")
    db.migrate(find_migrations_dir())
    return MemoryStore(db.conn), db


def test_observed_requires_evidence_ref(tmp_path):
    store, db = _store(tmp_path)
    with pytest.raises(ValueError, match="evidence_ref"):
        store.store("ns", MemoryType.EPISODIC, {"x": 1},
                    provenance=Provenance.OBSERVED)
    # A fabricated evidence_ref is refused: OBSERVED must ground in a
    # real ledger event (Invariant #12).
    with pytest.raises(ValueError, match="does not resolve"):
        store.store("ns", MemoryType.EPISODIC, {"x": 1},
                    provenance=Provenance.OBSERVED,
                    provenance_detail={"evidence_ref": "tool_call_123"})
    # With real evidence it works.
    db.conn.execute(
        "INSERT INTO events (event_id, timestamp, run_id, agent_id, type,"
        " payload, causation_id, correlation_id, schema_version, prev_hash,"
        " hash) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ("ev_real_1", "2026-10-04T00:00:00Z", None, None, "tool.completed",
         "{}", None, None, 1, "0" * 64, "1" * 64))
    db.conn.commit()
    mem = store.store("ns", MemoryType.EPISODIC, {"x": 1},
                      provenance=Provenance.OBSERVED,
                      provenance_detail={"evidence_ref": "ev_real_1"})
    assert mem.provenance == Provenance.OBSERVED


def test_inferred_is_the_honest_default_for_llm_output(tmp_path):
    store, _ = _store(tmp_path)
    mem = store.store("ns", MemoryType.SEMANTIC,
                      {"claim": "the model said this"},
                      provenance=Provenance.INFERRED)
    assert mem.provenance == Provenance.INFERRED
    trust, flags = trust_of(mem)
    assert trust <= 0.6
    assert any("INFERRED" in f for f in flags)


def test_validated_knowledge_requires_evaluation_ref(tmp_path):
    store, _ = _store(tmp_path)
    with pytest.raises(ValueError, match="validated_by"):
        store.store("global", MemoryType.EXPERIENCE, {"x": 1},
                    scope=Scope.GLOBAL)
    mem = store.store("global", MemoryType.EXPERIENCE, {"x": 1},
                      scope=Scope.GLOBAL,
                      provenance_detail={"validated_by": ["eval_1"]})
    assert mem.type == MemoryType.EXPERIENCE


def test_retrieval_returns_evidence_metadata(tmp_path):
    store, _ = _store(tmp_path)
    store.store("ns", MemoryType.SEMANTIC, {"fact": "spawning is costly"},
                scope=Scope.GLOBAL, importance=0.9,
                provenance=Provenance.DERIVED)
    store.store("ns", MemoryType.SEMANTIC, {"guess": "maybe faster"},
                scope=Scope.GLOBAL, importance=0.9,
                provenance=Provenance.SIMULATED, confidence=0.95)
    results = store.retrieve("ns", query="spawning")
    assert len(results) == 1
    r = results[0]
    assert r.why_retrieved.startswith("query match")
    assert 0.0 <= r.trust <= 1.0
    # Simulated memory: trust capped at 0.3 despite 0.95 confidence.
    sim = store.retrieve("ns", query="maybe")[0]
    assert sim.trust <= 0.3
    assert any("SIMULATED" in f for f in sim.trust_flags)


def test_task_scope_never_leaks(tmp_path):
    store, _ = _store(tmp_path)
    store.store("run_aaa", MemoryType.EPISODIC, {"secret": "plan"},
                scope=Scope.TASK)
    # Default retrieval (global only) sees nothing.
    assert store.retrieve("run_aaa") == []
    assert store.retrieve("other_ns", scopes=("global", "task")) == []
    # Explicit task scope + matching namespace sees it.
    got = store.retrieve("run_aaa", scopes=("task",))
    assert len(got) == 1
    assert "scope=task" in got[0].why_retrieved


def test_versioned_update_preserves_history(tmp_path):
    store, _ = _store(tmp_path)
    v1 = store.store("ns", MemoryType.SEMANTIC, {"v": 1}, scope=Scope.GLOBAL)
    v2 = store.update(v1.id, {"v": 2})
    assert v2.version == 2
    assert v2.supersedes == v1.id
    assert store.get(v1.id).status == MemoryStatus.SUPERSEDED
    chain = store.history(v2.id)
    assert [m.version for m in chain] == [2, 1]


def test_secrets_redacted_before_persistence(tmp_path):
    store, db = _store(tmp_path)
    mem = store.store("ns", MemoryType.EPISODIC,
                      {"note": "api_key=sk-abcdef1234567890"})
    raw = db.conn.execute("SELECT content FROM memories WHERE id=?",
                          (mem.id,)).fetchone()[0]
    assert "sk-abcdef1234567890" not in raw
    assert "REDACTED" in raw


def test_forget_is_hard_delete(tmp_path):
    store, _ = _store(tmp_path)
    mem = store.store("ns", MemoryType.SEMANTIC, {"x": 1},
                      scope=Scope.GLOBAL)
    assert store.forget(mem.id) is True
    assert store.get(mem.id) is None


def test_decay_only_touches_active_episodic(tmp_path):
    store, db = _store(tmp_path)
    mem = store.store("ns", MemoryType.EPISODIC, {"x": 1}, importance=0.8)
    store.store("ns", MemoryType.SEMANTIC, {"y": 2}, importance=0.8,
                scope=Scope.GLOBAL)
    db.conn.execute(
        "UPDATE memories SET updated_at='2020-01-01T00:00:00+00:00'")
    db.conn.commit()
    n = store.decay("ns", half_life_days=30.0)
    assert n == 1  # only the episodic one
    assert store.get(mem.id).importance < 0.3
