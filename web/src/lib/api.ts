/* Typed client for the AIR REST contract. Same-origin: relative URLs.
   In dev, set VITE_API_BASE=http://127.0.0.1:8765.
   No simulation, no caching of authority: every call hits the backend. */

import type {
  Agent,
  AgentExplain,
  AgentMessage,
  AgentRow,
  Approval,
  Assurance as AssuranceRecord,
  CapabilityDetail,
  CapabilityProvenance,
  CapabilitySummary,
  Evaluation,
  ExperienceDetail,
  ExperienceLineage,
  ExperienceSummary,
  GraphData,
  MemoryHit,
  Metrics as MetricsData,
  ModelInfo,
  PolicyListItem,
  PolicyProvenance,
  Run,
  RunExplain,
  RunSummary,
  SpawnDecisionExplain,
  SpawnDecisionSummary,
  StreamEvent,
  ToolAuthExplain,
  ToolCallDetail,
  ToolCallRow,
} from './types';

const BASE = import.meta.env.VITE_API_BASE ?? '';

export class ApiError extends Error {
  status: number;
  body: string;
  constructor(status: number, body: string) {
    super(`API ${status}: ${body.slice(0, 300)}`);
    this.status = status;
    this.body = body;
  }
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(BASE + path, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    throw new ApiError(res.status, await res.text());
  }
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

const q = (params: Record<string, string | number | boolean | undefined | null>) => {
  const s = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== '') s.set(k, String(v));
  }
  const str = s.toString();
  return str ? `?${str}` : '';
};

export function wsUrl(lastEventId?: string | null): string {
  const base = BASE || window.location.origin;
  const u = new URL('/ws/events', base);
  if (lastEventId) u.searchParams.set('last_event_id', lastEventId);
  u.protocol = u.protocol === 'https:' ? 'wss:' : 'ws:';
  return u.toString();
}

export const Health = {
  get: () => api<{ ok: boolean; version: string }>('/health'),
  ready: () => api<{ ok: boolean; bad_event: string | null }>('/ready'),
};

export const Runs = {
  list: (limit = 50) => api<RunSummary[]>(`/runs${q({ limit })}`),
  create: (body: { goal: string; strategy?: string; context?: Record<string, unknown>; agent_budget?: number; seed?: number }, idempotencyKey?: string) =>
    api<{ run_id: string }>('/runs', {
      method: 'POST',
      body: JSON.stringify(body),
      headers: idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : {},
    }),
  get: (runId: string) => api<Run>(`/runs/${runId}`),
  pause: (runId: string) => api<{ run_id: string; status: string }>(`/runs/${runId}/pause`, { method: 'POST' }),
  resume: (runId: string) => api<{ run_id: string; status: string }>(`/runs/${runId}/resume`, { method: 'POST' }),
  cancel: (runId: string) => api<{ run_id: string; status: string }>(`/runs/${runId}/cancel`, { method: 'POST' }),
  agents: (runId: string) => api<AgentRow[]>(`/runs/${runId}/agents`),
  events: (runId: string, opts?: { limit?: number; after_event_id?: string }) =>
    api<StreamEvent[]>(`/runs/${runId}/events${q({ limit: opts?.limit ?? 500, after_event_id: opts?.after_event_id })}`),
  world: (runId: string) => api<Record<string, unknown>>(`/runs/${runId}/world`),
  graph: (runId: string) => api<GraphData>(`/runs/${runId}/graph`),
  messages: (runId: string, limit = 200) => api<AgentMessage[]>(`/runs/${runId}/messages${q({ limit })}`),
  spawnDecisions: (runId: string) => api<SpawnDecisionSummary[]>(`/runs/${runId}/spawn-decisions`),
  explain: (runId: string) => api<RunExplain>(`/runs/${runId}/explain`),
};

export const Agents = {
  list: (runId?: string, limit = 100) => api<AgentRow[]>(`/agents${q({ run_id: runId, limit })}`),
  get: (agentId: string) => api<Agent>(`/agents/${agentId}`),
  explain: (agentId: string) => api<AgentExplain>(`/agents/${agentId}/explain`),
  create: (runId: string, body: { role: string; objective: string; specialization?: string; capabilities?: string[]; tools?: string[]; model?: string }) =>
    api<{ agent_id: string; role: string; status: string }>(`/runs/${runId}/agents`, { method: 'POST', body: JSON.stringify(body) }),
  spawn: (parentId: string, body: { objective: string; role: string; specialization?: string; capabilities?: string[]; tools?: string[]; model?: string; uncertainty?: number }) =>
    api<{ agent_id: string | null; decision: string; reason: string; expected_gain: number; estimated_cost: number; risk: number; decision_id: string }>(
      `/agents/${parentId}/spawn`, { method: 'POST', body: JSON.stringify(body) }),
  terminate: (agentId: string, subtree = true) =>
    api<{ agent_id: string; terminated: boolean }>(`/agents/${agentId}/terminate${q({ subtree })}`, { method: 'POST' }),
  message: (agentId: string, runId: string, body: { to_agent_id?: string; channel?: string; kind?: string; payload?: Record<string, unknown> }) =>
    api<{ message_id: string }>(`/agents/${agentId}/message${q({ run_id: runId })}`, { method: 'POST', body: JSON.stringify(body) }),
};

export const SpawnDecisions = {
  get: (decisionId: string) => api<SpawnDecisionExplain>(`/spawn-decisions/${decisionId}`),
};

export const Tools = {
  list: () => api<{ id: string; name: string; server: string | null; capability_class: string; enabled: boolean; policy_status: string }[]>('/tools'),
  call: (agentId: string, toolName: string, args: Record<string, unknown>) =>
    api<Record<string, unknown>>(`/agents/${agentId}/tools/call`, { method: 'POST', body: JSON.stringify({ tool_name: toolName, args }) }),
  calls: (opts?: { run_id?: string; agent_id?: string; state?: string; limit?: number }) =>
    api<ToolCallRow[]>(`/tool-calls${q({ run_id: opts?.run_id, agent_id: opts?.agent_id, state: opts?.state, limit: opts?.limit ?? 100 })}`),
  get: (callId: string) => api<ToolCallDetail>(`/tool-calls/${callId}`),
  authorization: (callId: string) => api<ToolAuthExplain>(`/tool-calls/${callId}/authorization`),
};

export const Approvals = {
  pending: () => api<Approval[]>('/approvals'),
  get: (id: string) => api<Approval>(`/approvals/${id}`),
  decide: (id: string, approved: boolean, decidedBy = 'operator') =>
    api<Approval>(`/approvals/${id}/decide`, { method: 'POST', body: JSON.stringify({ approved, decided_by: decidedBy }) }),
};

export const Experience = {
  list: (runId?: string, limit = 50) => api<ExperienceSummary[]>(`/experience${q({ run_id: runId, limit })}`),
  get: (id: string) => api<ExperienceDetail>(`/experience/${id}`),
  compare: (ids: string[]) => api<Record<string, unknown>>(`/experience/compare${q({ ids: ids.join(',') })}`),
  promote: (id: string, namespace = 'global', knowledge?: Record<string, unknown>) =>
    api<{ memory_id: string }>(`/experience/${id}/promote${q({ namespace })}`, { method: 'POST', body: JSON.stringify({ knowledge: knowledge ?? null }) }),
  lineage: (id: string) => api<ExperienceLineage>(`/experiences/${id}/lineage`),
};

export const Memory = {
  list: (namespace: string, opts?: { scopes?: string; type?: string; q?: string; limit?: number }) =>
    api<MemoryHit[]>(`/memory${q({ namespace, scopes: opts?.scopes ?? 'global', type: opts?.type, q: opts?.q, limit: opts?.limit ?? 50 })}`),
  create: (body: { namespace: string; type?: string; content: Record<string, unknown>; scope?: string; importance?: number; confidence?: number }) =>
    api<{ id: string }>('/memory', { method: 'POST', body: JSON.stringify(body) }),
  history: (id: string) => api<{ memory: { id: string; provenance: string } }[]>(`/memory/${id}/history`),
  forget: (id: string) => api<{ ok: boolean }>(`/memory/${id}`, { method: 'DELETE' }),
};

export const Capabilities = {
  list: () => api<CapabilitySummary[]>('/capabilities'),
  get: (id: string) => api<CapabilityDetail>(`/capabilities/${id}`),
  provenance: (id: string) => api<CapabilityProvenance>(`/capabilities/${id}/provenance`),
  propose: (body: { name: string; description: string; effect?: Record<string, unknown>; created_from?: string }) =>
    api<{ capability_id: string }>('/capabilities', { method: 'POST', body: JSON.stringify(body) }),
  promote: (id: string) => api<{ capability_id: string; status: string }>(`/capabilities/${id}/promote`, { method: 'POST' }),
  rollback: (id: string) => api<{ capability_id: string; status: string }>(`/capabilities/${id}/rollback`, { method: 'POST' }),
};

export const Eval = {
  run: (runId: string, suite?: Record<string, unknown>) =>
    api<{ evaluation_id: string; verdict: string; metrics: Record<string, unknown> }>('/evaluations', { method: 'POST', body: JSON.stringify({ run_id: runId, suite: suite ?? null }) }),
  get: (id: string) => api<Evaluation>(`/evaluations/${id}`),
};

export const Assurance = {
  run: (evaluationId: string) =>
    api<{ assurance_id: string; evaluator_verdict: string; system_verdict: string; false_accepts: number; false_rejects: number }>(
      `/assurance${q({ evaluation_id: evaluationId })}`, { method: 'POST' }),
  get: (id: string) => api<AssuranceRecord>(`/assurance/${id}`),
};

export const Policies = {
  list: () => api<PolicyListItem[]>('/policies'),
  get: (name: string) => api<{ current: Record<string, unknown>; history: Record<string, unknown>[] }>(`/policies/${name}`),
  propose: (name: string, params: Record<string, unknown>, reason: string) =>
    api<{ policy: string; version: string; status: string }>(`/policies/${name}/propose`, { method: 'POST', body: JSON.stringify({ params, reason }) }),
  promote: (name: string, version: string, evaluationId: string, assuranceId: string) =>
    api<{ policy: string; version: string; status: string }>(`/policies/${name}/promote`, {
      method: 'POST', body: JSON.stringify({ version, evaluation_id: evaluationId, assurance_id: assuranceId }),
    }),
  rollback: (name: string) =>
    api<{ policy: string; version: string; status: string }>(`/policies/${name}/rollback`, { method: 'POST' }),
  requestRollback: (name: string, reason: string, evidence?: Record<string, unknown>, requestedBy = 'operator') =>
    api<Record<string, unknown>>(`/policies/${name}/rollback/request`, {
      method: 'POST', body: JSON.stringify({ reason, evidence: evidence ?? null, requested_by: requestedBy }),
    }),
  approveRollback: (rollbackId: string, approvedBy = 'operator') =>
    api<{ version: string; status: string }>(`/policies/rollback/${rollbackId}/approve${q({ approved_by: approvedBy })}`, { method: 'POST' }),
  provenance: (name: string) => api<PolicyProvenance>(`/policies/${name}/provenance`),
  evaluate: (name: string, version: string, baseline?: string) =>
    api<{ evaluation_id: string; verdict: string; dimensions: Record<string, unknown> }>(
      `/policies/${name}/evaluate${q({ version, baseline })}`, { method: 'POST' }),
};

export const Learning = {
  analyze: () => api<Record<string, unknown>>('/learning/analyze', { method: 'POST' }),
  propose: () => api<Record<string, unknown>>('/learning/propose', { method: 'POST' }),
};

export const Models = {
  list: () => api<ModelInfo[]>('/models'),
};

export const Metrics = {
  get: () => api<MetricsData>('/metrics'),
};
