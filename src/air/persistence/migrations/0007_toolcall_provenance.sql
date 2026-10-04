-- 0007_toolcall_provenance: the ToolCall becomes a full auditable object.
-- Every field Inan's spec requires: who asked, what authorized it, under
-- which policy and budget, what happened, and verifiable result hashes.

ALTER TABLE tool_calls ADD COLUMN parent_agent_id TEXT;
ALTER TABLE tool_calls ADD COLUMN capability_id TEXT;
ALTER TABLE tool_calls ADD COLUMN capability_version TEXT;
ALTER TABLE tool_calls ADD COLUMN tool_version TEXT;
ALTER TABLE tool_calls ADD COLUMN sanitized_request TEXT;      -- JSON
ALTER TABLE tool_calls ADD COLUMN authorization_decision TEXT; -- JSON
ALTER TABLE tool_calls ADD COLUMN budget_reservation TEXT;     -- JSON
ALTER TABLE tool_calls ADD COLUMN result_hash TEXT;
ALTER TABLE tool_calls ADD COLUMN verification_status TEXT;   -- UNCHECKED|PASSED|FAILED
ALTER TABLE tool_calls ADD COLUMN started_at TEXT;
