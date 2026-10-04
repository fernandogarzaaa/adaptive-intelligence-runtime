-- 0003_memory_v2: typed, scoped, provenance-aware, versionable memories.
-- Adds the fields the v2 memory substrate requires. Backfills from v1 columns.

ALTER TABLE memories ADD COLUMN scope TEXT;
ALTER TABLE memories ADD COLUMN source_event_id TEXT;
ALTER TABLE memories ADD COLUMN observed_at TEXT;
ALTER TABLE memories ADD COLUMN status TEXT DEFAULT 'ACTIVE';
ALTER TABLE memories ADD COLUMN version INTEGER DEFAULT 1;
ALTER TABLE memories ADD COLUMN supersedes TEXT;
ALTER TABLE memories ADD COLUMN provenance_kind TEXT;

-- Backfill scope from the v1 visibility column.
UPDATE memories SET scope = CASE visibility
    WHEN 'task' THEN 'task'
    WHEN 'shared' THEN 'global'
    ELSE 'run'
END WHERE scope IS NULL;

UPDATE memories SET observed_at = created_at WHERE observed_at IS NULL;
UPDATE memories SET status = 'ACTIVE' WHERE status IS NULL;
UPDATE memories SET version = 1 WHERE version IS NULL;
UPDATE memories SET provenance_kind = 'DERIVED' WHERE provenance_kind IS NULL;

CREATE INDEX IF NOT EXISTS idx_memories_scope ON memories(scope);
CREATE INDEX IF NOT EXISTS idx_memories_status ON memories(status);
