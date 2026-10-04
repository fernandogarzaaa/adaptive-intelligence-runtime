-- 0001_initial: core AIR schema
-- SQLite, WAL mode. All timestamps are ISO-8601 UTC strings.

CREATE TABLE IF NOT EXISTS schema_migrations (
    version     TEXT PRIMARY KEY,
    applied_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id                  TEXT PRIMARY KEY,
    goal                TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'CREATED',
    strategy            TEXT,
    cognitive_plan      TEXT,  -- JSON
    seed                INTEGER,
    policy_version      TEXT,
    capability_versions TEXT,  -- JSON
    runtime_version     TEXT NOT NULL,
    total_cost          REAL NOT NULL DEFAULT 0,
    total_tokens        INTEGER NOT NULL DEFAULT 0,
    error               TEXT,
    final_result        TEXT,  -- JSON
    created_at          TEXT NOT NULL,
    started_at          TEXT,
    completed_at        TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status);
CREATE INDEX IF NOT EXISTS idx_runs_created ON runs(created_at);

CREATE TABLE IF NOT EXISTS agents (
    id                  TEXT PRIMARY KEY,
    parent_id           TEXT REFERENCES agents(id),
    root_run_id         TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    generation          INTEGER NOT NULL DEFAULT 0,
    role                TEXT NOT NULL,
    specialization      TEXT,
    objective           TEXT NOT NULL,
    model               TEXT,
    provider            TEXT,
    capabilities        TEXT,  -- JSON list
    tools               TEXT,  -- JSON list
    memory_scope        TEXT NOT NULL DEFAULT 'task',
    belief_scope        TEXT NOT NULL DEFAULT 'task',
    policy_scope        TEXT NOT NULL DEFAULT 'task',
    budget              TEXT,  -- JSON
    status              TEXT NOT NULL DEFAULT 'CREATED',
    status_reason       TEXT,
    created_at          TEXT NOT NULL,
    terminated_at       TEXT,
    lineage             TEXT,  -- JSON
    capability_version  TEXT,
    policy_version      TEXT
);
CREATE INDEX IF NOT EXISTS idx_agents_run ON agents(root_run_id);
CREATE INDEX IF NOT EXISTS idx_agents_parent ON agents(parent_id);
CREATE INDEX IF NOT EXISTS idx_agents_status ON agents(status);

CREATE TABLE IF NOT EXISTS agent_messages (
    id            TEXT PRIMARY KEY,
    run_id        TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    from_agent_id TEXT,
    to_agent_id   TEXT,
    channel       TEXT NOT NULL,  -- parent_child | sibling | broadcast | evidence | result
    kind          TEXT NOT NULL,  -- message | result | evidence | spawn_request | termination_request
    payload       TEXT NOT NULL,  -- JSON
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_messages_run ON agent_messages(run_id);
CREATE INDEX IF NOT EXISTS idx_agent_messages_to ON agent_messages(to_agent_id);

CREATE TABLE IF NOT EXISTS agent_lineage (
    child_id    TEXT PRIMARY KEY REFERENCES agents(id) ON DELETE CASCADE,
    parent_id   TEXT NOT NULL REFERENCES agents(id),
    relation    TEXT NOT NULL,  -- spawned | delegated
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    event_id        TEXT PRIMARY KEY,
    timestamp       TEXT NOT NULL,
    run_id          TEXT REFERENCES runs(id) ON DELETE CASCADE,
    agent_id        TEXT REFERENCES agents(id) ON DELETE CASCADE,
    type            TEXT NOT NULL,
    payload         TEXT NOT NULL,  -- JSON
    causation_id    TEXT,
    correlation_id  TEXT,
    schema_version  INTEGER NOT NULL DEFAULT 1,
    prev_hash       TEXT NOT NULL,
    hash            TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id);
CREATE INDEX IF NOT EXISTS idx_events_agent ON events(agent_id);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(type);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(timestamp);

CREATE TABLE IF NOT EXISTS world_states (
    id          TEXT PRIMARY KEY,
    run_id      TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    version     INTEGER NOT NULL,
    state       TEXT NOT NULL,  -- JSON
    created_at  TEXT NOT NULL,
    UNIQUE(run_id, version)
);

CREATE TABLE IF NOT EXISTS memories (
    id          TEXT PRIMARY KEY,
    namespace   TEXT NOT NULL,
    type        TEXT NOT NULL,  -- episodic | semantic | procedural | self | experience | capability
    content     TEXT NOT NULL,  -- JSON
    importance  REAL NOT NULL DEFAULT 0.5,
    confidence  REAL NOT NULL DEFAULT 0.5,
    provenance  TEXT,           -- JSON
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    source_run  TEXT,
    source_agent TEXT,
    visibility  TEXT NOT NULL DEFAULT 'private',  -- private | shared | task
    hash        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memories_ns ON memories(namespace);
CREATE INDEX IF NOT EXISTS idx_memories_type ON memories(type);

CREATE TABLE IF NOT EXISTS beliefs (
    id          TEXT PRIMARY KEY,
    agent_id    TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    statement   TEXT NOT NULL,
    confidence  REAL NOT NULL,
    provenance  TEXT,  -- JSON
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_beliefs_agent ON beliefs(agent_id);

CREATE TABLE IF NOT EXISTS capabilities (
    capability_id     TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    description       TEXT NOT NULL,
    version           TEXT NOT NULL DEFAULT '0.1.0',
    requirements      TEXT,  -- JSON
    tools             TEXT,  -- JSON
    model_requirements TEXT, -- JSON
    policy            TEXT,  -- JSON
    performance       TEXT,  -- JSON {uses, successes, failures}
    confidence        REAL NOT NULL DEFAULT 0.0,
    provenance        TEXT,  -- JSON
    validation_status TEXT NOT NULL DEFAULT 'CANDIDATE',
    created_from      TEXT,
    lineage           TEXT,  -- JSON
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS capability_versions (
    id            TEXT PRIMARY KEY,
    capability_id TEXT NOT NULL REFERENCES capabilities(capability_id) ON DELETE CASCADE,
    version       TEXT NOT NULL,
    changes       TEXT,  -- JSON
    status        TEXT NOT NULL DEFAULT 'CANDIDATE',
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS capability_evaluations (
    id                TEXT PRIMARY KEY,
    capability_id     TEXT NOT NULL REFERENCES capabilities(capability_id) ON DELETE CASCADE,
    evaluation_run_id TEXT,
    verdict           TEXT NOT NULL,
    created_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS experiences (
    id                      TEXT PRIMARY KEY,
    run_id                  TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    goal                    TEXT NOT NULL,
    initial_state           TEXT,  -- JSON
    cognitive_configuration TEXT,  -- JSON
    agents                  TEXT,  -- JSON
    actions                 TEXT,  -- JSON
    observations            TEXT,  -- JSON
    outcomes                TEXT,  -- JSON
    cost                    REAL NOT NULL DEFAULT 0,
    latency_ms              INTEGER NOT NULL DEFAULT 0,
    failures                TEXT,  -- JSON
    verification            TEXT,  -- JSON
    final_result            TEXT,  -- JSON
    created_at              TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_experiences_run ON experiences(run_id);

CREATE TABLE IF NOT EXISTS simulations (
    id                  TEXT PRIMARY KEY,
    run_id              TEXT REFERENCES runs(id) ON DELETE CASCADE,
    strategy            TEXT NOT NULL,  -- JSON candidate cognitive strategy
    predicted_trajectory TEXT NOT NULL, -- JSON, always labeled SIMULATED
    created_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evaluation_suites (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    version     TEXT NOT NULL,
    cases       TEXT NOT NULL,  -- JSON
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evaluation_runs (
    id                TEXT PRIMARY KEY,
    suite_id          TEXT REFERENCES evaluation_suites(id),
    subject           TEXT NOT NULL,  -- JSON {kind, id, version}
    evaluator         TEXT NOT NULL,
    evaluator_version TEXT NOT NULL,
    metrics           TEXT,  -- JSON
    verdict           TEXT,
    evidence          TEXT,  -- JSON bundle
    started_at        TEXT NOT NULL,
    completed_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_eval_runs_subject ON evaluation_runs(subject);

CREATE TABLE IF NOT EXISTS assurance_runs (
    id                TEXT PRIMARY KEY,
    target            TEXT NOT NULL,  -- JSON {kind, id, version}
    probes            TEXT,  -- JSON
    false_accepts     INTEGER NOT NULL DEFAULT 0,
    false_rejects     INTEGER NOT NULL DEFAULT 0,
    timeouts          INTEGER NOT NULL DEFAULT 0,
    exploitability    TEXT,
    evaluator_verdict TEXT,
    system_verdict    TEXT,
    evidence          TEXT,  -- JSON bundle
    started_at        TEXT NOT NULL,
    completed_at      TEXT
);

CREATE TABLE IF NOT EXISTS evidence (
    id          TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,  -- execution | test | tool | observation | verification
    subject_type TEXT NOT NULL,
    subject_id  TEXT NOT NULL,
    content     TEXT NOT NULL,  -- JSON
    hash        TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_evidence_subject ON evidence(subject_type, subject_id);

CREATE TABLE IF NOT EXISTS policies (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE,
    current_version TEXT,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS policy_versions (
    id              TEXT PRIMARY KEY,
    policy_id       TEXT NOT NULL REFERENCES policies(id) ON DELETE CASCADE,
    version         TEXT NOT NULL,
    parent_version  TEXT,
    changes         TEXT,  -- JSON
    reason          TEXT,
    evidence        TEXT,  -- JSON
    evaluation      TEXT,  -- JSON
    assurance       TEXT,  -- JSON
    status          TEXT NOT NULL DEFAULT 'CANDIDATE',
    created_at      TEXT NOT NULL,
    UNIQUE(policy_id, version)
);

CREATE TABLE IF NOT EXISTS providers (
    name        TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,
    base_url    TEXT,
    api_key_env TEXT,
    enabled     INTEGER NOT NULL DEFAULT 1,
    models      TEXT,  -- JSON
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS models (
    id              TEXT PRIMARY KEY,
    provider        TEXT NOT NULL REFERENCES providers(name),
    model_id        TEXT NOT NULL,
    capabilities    TEXT,  -- JSON
    cost_per_1k_in  REAL,
    cost_per_1k_out REAL,
    local           INTEGER NOT NULL DEFAULT 0,
    UNIQUE(provider, model_id)
);

CREATE TABLE IF NOT EXISTS tools (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE,
    server          TEXT,
    schema          TEXT,  -- JSON
    capability_class TEXT NOT NULL DEFAULT 'READ',
    enabled         INTEGER NOT NULL DEFAULT 1,
    policy_status   TEXT NOT NULL DEFAULT 'allowed'
);

CREATE TABLE IF NOT EXISTS tool_calls (
    id          TEXT PRIMARY KEY,
    run_id      TEXT REFERENCES runs(id) ON DELETE CASCADE,
    agent_id    TEXT REFERENCES agents(id) ON DELETE CASCADE,
    tool        TEXT NOT NULL,
    args        TEXT,   -- JSON
    result      TEXT,   -- JSON
    status      TEXT NOT NULL DEFAULT 'started',
    latency_ms  INTEGER,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tool_calls_run ON tool_calls(run_id);
CREATE INDEX IF NOT EXISTS idx_tool_calls_agent ON tool_calls(agent_id);

CREATE TABLE IF NOT EXISTS budgets (
    id                  TEXT PRIMARY KEY,
    run_id              TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    agent_id            TEXT REFERENCES agents(id) ON DELETE CASCADE,
    scope               TEXT NOT NULL,  -- run | agent
    token_limit         INTEGER,
    time_limit_s        INTEGER,
    cost_limit          REAL,
    agent_limit         INTEGER,
    tool_call_limit     INTEGER,
    depth_limit         INTEGER,
    consumed_tokens     INTEGER NOT NULL DEFAULT 0,
    consumed_cost       REAL NOT NULL DEFAULT 0,
    consumed_tool_calls INTEGER NOT NULL DEFAULT 0,
    consumed_agents     INTEGER NOT NULL DEFAULT 0,
    status              TEXT NOT NULL DEFAULT 'ok',  -- ok | warning | exhausted
    updated_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_budgets_run ON budgets(run_id);

CREATE TABLE IF NOT EXISTS approvals (
    id          TEXT PRIMARY KEY,
    run_id      TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    agent_id    TEXT REFERENCES agents(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL,  -- tool_call | capability_promotion | spawn | destructive
    subject     TEXT NOT NULL,  -- JSON
    status      TEXT NOT NULL DEFAULT 'requested',
    requested_at TEXT NOT NULL,
    decided_at  TEXT,
    decided_by  TEXT
);
CREATE INDEX IF NOT EXISTS idx_approvals_run ON approvals(run_id);
CREATE INDEX IF NOT EXISTS idx_approvals_status ON approvals(status);

CREATE TABLE IF NOT EXISTS experiments (
    id                  TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    task_set            TEXT NOT NULL,  -- JSON
    strategies          TEXT NOT NULL,  -- JSON
    model_config        TEXT,  -- JSON
    budget              TEXT,  -- JSON
    evaluation_suite_id TEXT REFERENCES evaluation_suites(id),
    assurance_suite_id  TEXT,
    seed                INTEGER,
    status              TEXT NOT NULL DEFAULT 'created',
    created_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS experiment_runs (
    id            TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL REFERENCES experiments(id) ON DELETE CASCADE,
    strategy      TEXT NOT NULL,
    run_id        TEXT REFERENCES runs(id) ON DELETE CASCADE,
    metrics       TEXT,  -- JSON
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_experiment_runs_exp ON experiment_runs(experiment_id);

CREATE TABLE IF NOT EXISTS audit_entries (
    id          TEXT PRIMARY KEY,
    timestamp   TEXT NOT NULL,
    actor       TEXT NOT NULL,  -- operator | runtime | agent:{id} | policy
    action      TEXT NOT NULL,
    subject_type TEXT,
    subject_id  TEXT,
    detail      TEXT,  -- JSON
    prev_hash   TEXT NOT NULL,
    hash        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_entries(timestamp);
