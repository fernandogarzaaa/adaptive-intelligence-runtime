-- 0008_toolcall_replace: the 0001 scaffold created a legacy tool_calls
-- table (tool/args/result/status) that shadowed 0006's CREATE TABLE IF NOT
-- EXISTS. Nothing ever wrote to the legacy columns; replace it with the
-- full auditable ToolCall schema.

DROP TABLE IF EXISTS tool_calls;

CREATE TABLE tool_calls (
    id              TEXT PRIMARY KEY,
    run_id          TEXT NOT NULL,
    agent_id        TEXT NOT NULL,
    parent_agent_id TEXT,
    tool_name       TEXT NOT NULL,
    tool_version    TEXT,
    server_id       TEXT,                 -- MCP server or connector id, if any
    capability      TEXT NOT NULL,        -- CapabilityClass value
    capability_id   TEXT,
    capability_version TEXT,
    state           TEXT NOT NULL,        -- REQUESTED|VALIDATED|RESERVED|DISPATCHED|OBSERVED|VERIFIED|COMMITTED|DENIED|FAILED|APPROVAL_PENDING
    args_redacted   TEXT,                 -- JSON, secrets redacted
    args_hash       TEXT,
    sanitized_request TEXT,               -- JSON
    authorization_decision TEXT,          -- JSON
    policy_version  TEXT,
    budget_reservation TEXT,              -- JSON
    result_redacted TEXT,                 -- JSON, secrets redacted + framed
    result_hash     TEXT,
    provenance      TEXT,                 -- JSON
    verification_status TEXT,             -- UNCHECKED|PASSED|FAILED
    error           TEXT,
    latency_ms      INTEGER,
    approval_id     TEXT,
    created_at      TEXT NOT NULL,
    started_at      TEXT,
    completed_at    TEXT
);
CREATE INDEX idx_tool_calls_run ON tool_calls(run_id);
CREATE INDEX idx_tool_calls_agent ON tool_calls(agent_id);
