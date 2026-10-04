-- 0006_tools_mcp: tool execution ledger, persistent approvals, MCP servers,
-- generic connectors, fencing-token task claims, agent security grants.

-- Agent security grants are separate from skill-tag capabilities.
ALTER TABLE agents ADD COLUMN granted_capabilities TEXT;

CREATE TABLE IF NOT EXISTS tool_calls (
    id              TEXT PRIMARY KEY,
    run_id          TEXT NOT NULL,
    agent_id        TEXT NOT NULL,
    tool_name       TEXT NOT NULL,
    server_id       TEXT,                 -- MCP server or connector id, if any
    capability      TEXT NOT NULL,        -- CapabilityClass value
    state           TEXT NOT NULL,        -- REQUESTED|VALIDATED|RESERVED|DISPATCHED|OBSERVED|VERIFIED|COMMITTED|DENIED|FAILED|APPROVAL_PENDING
    args_redacted   TEXT,                 -- JSON, secrets redacted
    args_hash       TEXT,
    result_redacted TEXT,                 -- JSON, secrets redacted + framed
    provenance      TEXT,                 -- JSON
    error           TEXT,
    latency_ms      INTEGER,
    approval_id     TEXT,
    created_at      TEXT NOT NULL,
    completed_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_tool_calls_run ON tool_calls(run_id);
CREATE INDEX IF NOT EXISTS idx_tool_calls_agent ON tool_calls(agent_id);

CREATE TABLE IF NOT EXISTS approvals (
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
CREATE INDEX IF NOT EXISTS idx_approvals_status ON approvals(status);

CREATE TABLE IF NOT EXISTS mcp_servers (
    id          TEXT PRIMARY KEY,
    transport   TEXT NOT NULL,            -- stdio|http
    config      TEXT NOT NULL,            -- JSON, secrets redacted (env values never stored)
    status      TEXT NOT NULL DEFAULT 'REGISTERED',  -- REGISTERED|CONNECTED|ERROR
    last_error  TEXT,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS connectors (
    id          TEXT PRIMARY KEY,
    base_url    TEXT NOT NULL,
    config      TEXT NOT NULL,            -- JSON: auth refs, allowlists, schemas, rate limits
    created_at  TEXT NOT NULL
);

-- Fencing-token task claims (Skein pattern): a claim is valid only while the
-- lease is held, and the fencing token is monotonic per task key.
CREATE TABLE IF NOT EXISTS task_claims (
    task_key        TEXT PRIMARY KEY,
    owner           TEXT NOT NULL,
    fencing_token   INTEGER NOT NULL,
    lease_expires_at TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'HELD',  -- HELD|RELEASED|EXPIRED
    created_at      TEXT NOT NULL
);
