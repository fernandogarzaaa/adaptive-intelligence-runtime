-- 0009_approvals_replace: the 0001 scaffold created a legacy approvals
-- table (run_id/agent_id/subject-JSON) that shadowed 0006's CREATE TABLE IF
-- NOT EXISTS. Nothing wrote to the legacy shape; replace it with the
-- persistent operator-approval schema.

DROP TABLE IF EXISTS approvals;

CREATE TABLE approvals (
    id          TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,            -- tool_call|rollback|capability_promote|...
    subject     TEXT NOT NULL,            -- what is being approved
    payload     TEXT,                     -- JSON, redacted
    requested_by TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'PENDING',  -- PENDING|APPROVED|DENIED|EXPIRED
    decided_by  TEXT,
    decided_at  TEXT,
    created_at  TEXT NOT NULL
);
CREATE INDEX idx_approvals_status ON approvals(status);
