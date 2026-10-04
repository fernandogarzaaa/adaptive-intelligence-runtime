-- 0010_idempotency: API-layer idempotency keys. The API is a projection and
-- control surface over the runtime, never an alternative execution path.
-- Repeated mutation requests carrying the same Idempotency-Key must not
-- double-execute. The stored response is replayed verbatim so a retried
-- POST /runs (or approve/cancel/rollback/promote) is observably identical
-- to the first execution.
--
-- The key is scoped to (method, path): a key issued for POST /runs cannot
-- replay a POST /approvals/.../decide. Different runtimes share nothing;
-- the table lives in the runtime's own database.

CREATE TABLE IF NOT EXISTS idempotency_keys (
    key         TEXT NOT NULL,
    method      TEXT NOT NULL,
    path        TEXT NOT NULL,
    status_code INTEGER NOT NULL,
    response    TEXT NOT NULL,            -- JSON response body, verbatim
    created_at  TEXT NOT NULL,
    PRIMARY KEY (key, method, path)
);
