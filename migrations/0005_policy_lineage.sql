-- 0005_policy_lineage: full candidate lineage + auditable rollback records.
-- A policy candidate must never directly mutate the active policy; rollback
-- is a first-class auditable operation, not an assignment.

ALTER TABLE policy_versions ADD COLUMN hypothesis TEXT;
ALTER TABLE policy_versions ADD COLUMN expected_effect TEXT;    -- JSON
ALTER TABLE policy_versions ADD COLUMN constraints TEXT;       -- JSON guardrails
ALTER TABLE policy_versions ADD COLUMN generated_by TEXT;
ALTER TABLE policy_versions ADD COLUMN source_experiences TEXT; -- JSON list

CREATE TABLE IF NOT EXISTS policy_rollbacks (
    id              TEXT PRIMARY KEY,
    policy_id       TEXT NOT NULL REFERENCES policies(id),
    policy_name     TEXT NOT NULL,
    from_version    TEXT NOT NULL,   -- failed version being rolled back
    to_version      TEXT NOT NULL,   -- version being reactivated
    reason          TEXT NOT NULL,
    evidence        TEXT,            -- JSON triggering evidence
    requested_by    TEXT NOT NULL,
    approved_by     TEXT,
    status          TEXT NOT NULL DEFAULT 'REQUESTED',  -- REQUESTED|APPROVED|REJECTED
    affected_runs   TEXT,            -- JSON list of run ids
    evaluation_refs TEXT,            -- JSON
    assurance_refs  TEXT,            -- JSON
    requested_at    TEXT NOT NULL,
    decided_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_rollbacks_policy ON policy_rollbacks(policy_id);
