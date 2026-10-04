/* Backend contract shapes. These mirror the API/service layer exactly.
   The console never invents fields: if the backend does not return
   something, the UI renders "unknown". */

export interface StreamEvent {
  event_id: string;
  event_type: string;
  schema_version: number;
  timestamp: string;
  run_id: string | null;
  agent_id: string | null;
  causation_id: string | null;
  correlation_id: string | null;
  sequence: number | null;
  payload: Record<string, unknown>;
}

export interface Run {
  id: string;
  goal: string;
  status: string;
  strategy: string | null;
  cognitive_plan: Record<string, unknown> | null;
  seed: number | null;
  policy_version: string | null;
  total_cost: number;
  total_tokens: number;
  error: string | null;
  final_result: Record<string, unknown> | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
}

export interface RunSummary {
  id: string;
  goal: string;
  status: string;
  strategy: string | null;
  created_at: string;
  completed_at: string | null;
}

export interface Agent {
  id: string;
  parent_id: string | null;
  root_run_id: string;
  generation: number;
  role: string;
  specialization: string | null;
  objective: string;
  model: string;
  provider: string;
  capabilities: string[];
  granted_capabilities: string[];
  tools: string[];
  memory_scope: string;
  budget: {
    token_limit: number | null;
    cost_limit_usd: number | null;
    tool_call_limit: number | null;
    agent_limit: number | null;
    consumed_tokens: number;
    consumed_cost_usd: number;
    consumed_tool_calls: number;
    consumed_agents: number;
  } | null;
  status: string;
  status_reason: string | null;
  created_at: string;
  terminated_at: string | null;
  lineage: string[];
  capability_version: string | null;
  policy_version: string | null;
}

export interface AgentRow {
  id: string;
  parent_id: string | null;
  root_run_id?: string;
  role: string;
  objective: string;
  status: string;
  generation?: number;
  created_at?: string;
}

export interface GraphData {
  nodes: { id: string; role: string; status: string; generation: number }[];
  edges: { from: string; to: string }[];
}

export interface SpawnDecisionSummary {
  decision_id: string;
  decision: string;
  role: string;
  reason: string;
  expected_gain: number;
  estimated_cost: number;
  risk: number;
  inputs: Record<string, unknown> | null;
  evidence: string[];
  events: { type: string; timestamp: string }[];
}

export interface SpawnDecisionExplain {
  subject: string;
  id: string;
  decision: string;
  inputs: Record<string, unknown> | null;
  constraints: Record<string, unknown> | null;
  scores: { expected_gain: number; estimated_cost: number; risk: number };
  reason: string;
  evidence_references: {
    evidence: string[];
    events: { type: string; timestamp: string }[];
  };
}

export interface ToolCallRow {
  id: string;
  run_id: string;
  agent_id: string;
  tool_name: string;
  capability: string;
  state: string;
  verification_status: string | null;
  created_at: string;
}

export interface ToolCallDetail extends ToolCallRow {
  server_id: string | null;
  args_redacted: Record<string, unknown> | null;
  args_hash: string | null;
  result_redacted: Record<string, unknown> | null;
  provenance: Record<string, unknown> | null;
  authorization_decision: Record<string, unknown> | null;
  policy_version: string | null;
  approval_id: string | null;
  error: string | null;
  latency_ms: number | null;
  completed_at: string | null;
}

export interface ToolAuthExplain {
  subject: string;
  id: string;
  decision: { state: string; approved: boolean | null };
  inputs: { tool_name: string; capability: string; server_id: string | null };
  authorization_checks: Record<string, unknown>;
  policy_version: string | null;
  budget_state: Record<string, unknown>;
  verification_state: { verification_status: string | null };
  evidence_references: {
    args_hash: string | null;
    provenance: Record<string, unknown> | null;
    run_id: string;
    agent_id: string;
  };
}

export interface Approval {
  id: string;
  kind: string;
  subject: string;
  payload: Record<string, unknown>;
  requested_by: string;
  status: string;
  decided_by: string | null;
  decided_at: string | null;
  created_at: string;
}

export interface AgentMessage {
  id: string;
  from_agent_id: string | null;
  to_agent_id: string | null;
  channel: string;
  kind: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface ExperienceSummary {
  id: string;
  run_id: string;
  goal: string;
  cost: number;
  latency_ms: number;
  created_at: string;
}

export interface ExperienceDetail extends ExperienceSummary {
  dimensions: Record<string, unknown> | null;
  outcomes: Record<string, unknown> | null;
  failures: unknown;
  verification: Record<string, unknown> | null;
  evaluation_refs: unknown;
  assurance_refs: unknown;
  runtime_version: string;
}

export interface ExperienceLineage {
  subject: string;
  id: string;
  decision: { goal: string; cost: number; latency_ms: number };
  inputs: { run_id: string; dimensions: Record<string, unknown> | null };
  verification_state: Record<string, unknown> | null;
  evidence_references: {
    evaluation_refs: unknown;
    assurance_refs: unknown;
    derived_memories: { id: string; type: string; created_at: string }[];
  };
}

export interface MemoryHit {
  memory: {
    id: string;
    namespace: string;
    type: string;
    content: Record<string, unknown>;
    provenance: string;
    created_at: string;
  };
  score: number;
  why_retrieved: string;
  trust: number;
  trust_flags: string[];
}

export interface CapabilitySummary {
  capability_id: string;
  name: string;
  version: string;
  validation_status: string;
  confidence: number;
}

export interface CapabilityDetail {
  capability_id: string;
  name: string;
  description: string;
  version: string;
  requirements: unknown;
  tools: string[];
  model_requirements: unknown;
  policy: unknown;
  effect: Record<string, unknown> | null;
  performance: unknown;
  confidence: number;
  provenance: Record<string, unknown>;
  validation_status: string;
  created_from: string | null;
  lineage: unknown;
  validation_runs: unknown;
  created_at: string;
  updated_at: string;
}

export interface CapabilityProvenance {
  subject: string;
  id: string;
  decision: { validation_status: string; confidence: number };
  inputs: { name: string; description: string };
  evidence_references: {
    created_from: string | null;
    validation_runs: unknown;
    events: { type: string; timestamp: string }[];
  };
}

export interface PolicyListItem {
  name: string;
  current_version: string;
  effects: Record<string, unknown>[];
}

export interface PolicyVersionDetail {
  version: string;
  status: string;
  parent_version: string | null;
  hypothesis: string | null;
  generated_by: string | null;
  source_experiences: string[] | null;
  changes: Record<string, unknown>;
  expected_effect: Record<string, unknown> | null;
  constraints: Record<string, unknown> | null;
  reason: string | null;
  evaluation: Record<string, unknown> | null;
  assurance: Record<string, unknown> | null;
  created_at: string;
}

export interface PolicyProvenance {
  policy: string;
  active_version: string | null;
  chain: PolicyVersionDetail[];
}

export interface Evaluation {
  id: string;
  suite_id: string;
  subject: Record<string, unknown> | null;
  evaluator: string;
  evaluator_version: string;
  metrics: Record<string, unknown> | null;
  verdict: string;
  evidence: Record<string, unknown> | null;
  started_at: string;
  completed_at: string;
}

export interface Assurance {
  id: string;
  target: Record<string, unknown> | null;
  probes: unknown;
  false_accepts: number;
  false_rejects: number;
  timeouts: number;
  evaluator_verdict: string;
  system_verdict: string;
  evidence: Record<string, unknown> | null;
  started_at: string;
  completed_at: string;
}

export interface ModelInfo {
  provider: string;
  model: string;
  capabilities: Record<string, unknown>;
  local?: boolean;
  reason?: string;
}

export interface Metrics {
  runs: number;
  agents: number;
  events: number;
  spawns_approved: number;
  spawns_denied: number;
  tool_calls: number;
}

export interface RunExplain {
  subject: string;
  id: string;
  decision: { status: string; strategy: string | null };
  inputs: { goal: string; seed: number | null };
  constraints: Record<string, unknown> | null;
  candidate_strategies: unknown;
  selected_strategy: string | null;
  scores: unknown;
  policy_version: string | null;
  budget_state: { total_cost: number; total_tokens: number };
  verification_state: { error: string | null };
  evidence_references: {
    event_count: number;
    event_types: Record<string, number>;
    first_event_id: string | null;
    last_event_id: string | null;
  };
}

export interface AgentBudgetState {
  token_limit: number | null;
  cost_limit_usd: number | null;
  tool_call_limit: number | null;
  agent_limit: number | null;
  consumed_tokens: number;
  consumed_cost_usd: number;
  consumed_tool_calls: number;
  consumed_agents: number;
}

export interface AgentExplain {
  subject: string;
  id: string;
  decision: { status: string; status_reason: string | null };
  inputs: {
    role: string;
    objective: string;
    specialization: string | null;
  };
  constraints: { memory_scope: string; budget: AgentBudgetState | null };
  authorization_checks: {
    capabilities: string[];
    granted_capabilities: string[];
    tools: string[];
  };
  policy_version: string | null;
  evidence_references: {
    lineage: string[];
    parent_id: string | null;
    root_run_id: string;
  };
}
