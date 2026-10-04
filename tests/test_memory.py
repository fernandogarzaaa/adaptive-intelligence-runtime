"""Memory substrate: isolation, redaction, promotion, decay."""

from pathlib import Path

from air.config import AirConfig
from air.memory.store import MemoryStore, MemoryType, Visibility
from air.persistence.db import Database


def _store(tmp_path):
    db = Database(tmp_path / "m.db")
    db.migrate(Path(__file__).resolve().parents[1] / "migrations")
    return MemoryStore(db.conn), db


def test_store_and_retrieve(tmp_path):
    store, _ = _store(tmp_path)
    mem = store.store("global", MemoryType.SEMANTIC,
                      {"fact": "spawning more agents is not always better"},
                      importance=0.9, confidence=0.8)
    assert mem.hash, "every memory must have a content hash"
    got = store.get(mem.id)
    assert got.content["fact"].startswith("spawning more")
    results = store.retrieve("global", query="spawning")
    assert len(results) == 1


def test_secrets_redacted_before_persistence(tmp_path):
    store, db = _store(tmp_path)
    mem = store.store("global", MemoryType.EPISODIC,
                      {"note": "api_key=sk-abcdef1234567890"})
    raw = db.conn.execute("SELECT content FROM memories WHERE id=?",
                          (mem.id,)).fetchone()[0]
    assert "sk-abcdef1234567890" not in raw
    assert "REDACTED" in raw


def test_task_scoped_memories_are_isolated(tmp_path):
    store, _ = _store(tmp_path)
    store.store("run_aaa", MemoryType.EPISODIC, {"x": 1},
                visibility=Visibility.TASK)
    # Same-namespace retrieval without include_shared excludes task memories.
    assert store.retrieve("run_aaa") == []
    assert len(store.retrieve("run_aaa", include_shared=True)) == 1
    # Other namespaces never see them.
    assert store.retrieve("run_bbb", include_shared=True) == []


def test_promote_copies_with_provenance(tmp_path):
    store, _ = _store(tmp_path)
    mem = store.store("run_aaa", MemoryType.PROCEDURAL, {"step": "verify first"},
                      visibility=Visibility.TASK)
    promoted = store.promote(mem.id, "global")
    assert promoted.namespace == "global"
    assert promoted.visibility == Visibility.SHARED
    assert promoted.provenance["promoted_from"] == mem.id
    # Original untouched.
    assert store.get(mem.id).namespace == "run_aaa"


def test_decay_reduces_importance(tmp_path):
    store, db = _store(tmp_path)
    mem = store.store("global", MemoryType.EPISODIC, {"x": 1}, importance=0.8)
    # Age the memory 60 days with a 30-day half-life -> ~0.2.
    db.conn.execute(
        "UPDATE memories SET updated_at='2020-01-01T00:00:00+00:00' WHERE id=?",
        (mem.id,))
    db.conn.commit()
    n = store.decay("global", half_life_days=30.0)
    assert n == 1
    assert store.get(mem.id).importance < 0.3


def test_delete(tmp_path):
    store, _ = _store(tmp_path)
    mem = store.store("global", MemoryType.SEMANTIC, {"x": 1})
    assert store.delete(mem.id) is True
    assert store.get(mem.id) is None
    assert store.delete(mem.id) is False
