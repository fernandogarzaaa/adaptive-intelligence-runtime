/* Live Intelligence screen: /runs/:runId/live
   Operator view of one run: status, cognitive graph, agent detail,
   spawn decisions (approvals AND denials), live event feed, run controls.
   The console renders only what the backend returns. Missing values
   render as "unknown"; nothing is simulated or inferred. */

import { useEffect, useMemo, useState } from 'react';
import { useParams } from 'react-router-dom';
import { Agents, Models, Runs, SpawnDecisions } from '../lib/api';
import { useApi, watchRun } from '../lib/query';
import { useLiveEvents } from '../lib/ws';
import type {
  AgentBudgetState,
  AgentRow,
  SpawnDecisionSummary,
  StreamEvent,
} from '../lib/types';
import CognitiveGraph, { CognitiveEdge, CognitiveNode } from '../components/CognitiveGraph';
import {
  Badge,
  ConfirmDialog,
  Empty,
  ErrorBox,
  Hash,
  JsonBlock,
  KV,
  Loading,
  Panel,
  Time,
  Unknown,
  fmtElapsed,
  fmtMoney,
  val,
} from '../components/ui';

const TERMINAL_RUN_STATES = ['COMPLETED', 'FAILED', 'CANCELLED'];

function elapsedMs(
  startIso: string | null | undefined,
  endIso: string | null | undefined,
  now: number,
): number | null {
  if (!startIso) return null;
  const start = Date.parse(startIso);
  if (Number.isNaN(start)) return null;
  const end = endIso ? Date.parse(endIso) : now;
  if (Number.isNaN(end)) return null;
  return Math.max(0, end - start);
}

function fmtScore(v: number | null | undefined): string {
  if (v === null || v === undefined) return 'unknown';
  return v.toFixed(3);
}

/** Decision badge: backend reports SPAWN/DENY; color SPAWN as approved. */
function DecisionBadge({ decision }: { decision: string | null | undefined }) {
  const d = (decision ?? 'unknown').toUpperCase();
  const cls = d === 'SPAWN' ? 'APPROVED' : d;
  return <Badge status={cls}>{decision ?? 'unknown'}</Badge>;
}

function summarizePayload(payload: Record<string, unknown>): string {
  const parts: string[] = [];
  for (const [k, v] of Object.entries(payload)) {
    if (v === null || v === undefined || typeof v === 'object') continue;
    parts.push(`${k}=${String(v)}`);
    if (parts.join(' ').length > 140) break;
  }
  return parts.length > 0 ? parts.join(' ') : '(no scalar fields)';
}

function listOrUnknown(items: string[] | null | undefined): React.ReactNode {
  if (!items || items.length === 0) return <Unknown />;
  return <span>{items.join(', ')}</span>;
}

/* ------------------------------------------------------- budget remaining */

function BudgetRemaining({ budget }: { budget: AgentBudgetState | null }) {
  if (!budget) return <Unknown />;
  const dims: { label: string; limit: number | null; consumed: number; fmt: (v: number) => string }[] = [
    { label: 'tokens', limit: budget.token_limit, consumed: budget.consumed_tokens, fmt: (v) => v.toLocaleString() },
    { label: 'cost', limit: budget.cost_limit_usd, consumed: budget.consumed_cost_usd, fmt: fmtMoney },
    { label: 'tool calls', limit: budget.tool_call_limit, consumed: budget.consumed_tool_calls, fmt: (v) => String(v) },
    { label: 'child agents', limit: budget.agent_limit, consumed: budget.consumed_agents, fmt: (v) => String(v) },
  ];
  return (
    <div>
      {dims.map((d) => (
        <div key={d.label} style={{ display: 'flex', gap: 10, padding: '2px 0' }}>
          <span className="faint" style={{ width: 96, flex: 'none' }}>{d.label}</span>
          <span className="mono">{d.limit === null ? 'unknown' : d.fmt(d.limit - d.consumed)}</span>
          <span className="faint">
            ({d.fmt(d.consumed)} used{d.limit !== null ? ` of ${d.fmt(d.limit)}` : ''})
          </span>
        </div>
      ))}
    </div>
  );
}

/* ---------------------------------------------------------- agent detail */

interface SpawnResult {
  agent_id: string | null;
  decision: string;
  reason: string;
  expected_gain: number;
  estimated_cost: number;
  risk: number;
  decision_id: string;
}

function AgentDetail({
  agentId,
  now,
  onClose,
  onChanged,
}: {
  agentId: string;
  now: number;
  onClose: () => void;
  onChanged: () => void;
}) {
  const explain = useApi(() => Agents.explain(agentId), [agentId]);
  const full = useApi(() => Agents.get(agentId), [agentId]);
  const [spawning, setSpawning] = useState(false);
  const [spawnRole, setSpawnRole] = useState('');
  const [spawnObjective, setSpawnObjective] = useState('');
  const [spawnResult, setSpawnResult] = useState<SpawnResult | null>(null);
  const [spawnError, setSpawnError] = useState<string | null>(null);
  const [confirmTerminate, setConfirmTerminate] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const x = explain.data;
  const a = full.data;
  const budget = x?.constraints?.budget ?? null;
  const ms = elapsedMs(a?.created_at, a?.terminated_at, now);

  const closeSpawn = () => {
    setSpawning(false);
    setSpawnRole('');
    setSpawnObjective('');
    setSpawnResult(null);
    setSpawnError(null);
  };

  const doSpawn = async () => {
    setBusy(true);
    setSpawnError(null);
    try {
      const r = await Agents.spawn(agentId, { role: spawnRole.trim(), objective: spawnObjective.trim() });
      setSpawnResult(r);
    } catch (e) {
      setSpawnError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const doTerminate = async () => {
    setBusy(true);
    setActionError(null);
    try {
      await Agents.terminate(agentId, true);
      setConfirmTerminate(false);
      onChanged();
      onClose();
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e));
      setConfirmTerminate(false);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Panel
      title={`Agent ${agentId.slice(0, 8)}`}
      right={
        <span style={{ display: 'inline-flex', gap: 8 }}>
          <button className="btn small" onClick={() => setSpawning(true)} disabled={busy}>
            Spawn child
          </button>
          <button className="btn small" onClick={() => setConfirmTerminate(true)} disabled={busy}>
            Terminate
          </button>
          <button className="btn small" onClick={onClose}>
            Close
          </button>
        </span>
      }
    >
      {explain.loading || full.loading ? (
        <Loading label="Loading agent..." />
      ) : explain.error ? (
        <ErrorBox error={explain.error} onRetry={explain.refresh} />
      ) : full.error ? (
        <ErrorBox error={full.error} onRetry={full.refresh} />
      ) : x && a ? (
        <>
          {actionError && <ErrorBox error={actionError} />}
          <KV
            rows={[
              ['role', val(x.inputs.role)],
              ['objective', val(x.inputs.objective)],
              ['specialization', val(x.inputs.specialization)],
              ['model', val(a.model)],
              [
                'status',
                <span key="st">
                  <Badge status={x.decision.status} />{' '}
                  <span className="dim">{x.decision.status_reason ?? ''}</span>
                </span>,
              ],
              ['elapsed', ms === null ? <Unknown /> : <span className="mono">{fmtElapsed(ms)}</span>],
              ['tokens consumed', budget ? <span className="mono">{budget.consumed_tokens.toLocaleString()}</span> : <Unknown />],
              ['cost', budget ? <span className="mono">{fmtMoney(budget.consumed_cost_usd)}</span> : <Unknown />],
              ['budget remaining', <BudgetRemaining key="br" budget={budget} />],
              ['capabilities', listOrUnknown(x.authorization_checks.capabilities)],
              ['granted capabilities', listOrUnknown(x.authorization_checks.granted_capabilities)],
              ['tools', listOrUnknown(x.authorization_checks.tools)],
              ['capability version', val(a.capability_version)],
              ['policy version', val(x.policy_version)],
              ['parent', <Hash key="p" id={x.evidence_references.parent_id} len={8} />],
              ['memory scope', val(x.constraints.memory_scope)],
            ]}
          />
        </>
      ) : (
        <Empty title="Agent not found" />
      )}

      {spawning && (
        <div className="dialog-backdrop" onClick={() => !busy && closeSpawn()}>
          <div className="dialog" onClick={(e) => e.stopPropagation()}>
            <div className="dialog-h">Spawn child agent</div>
            <div className="dialog-b">
              {spawnResult ? (
                <>
                  <KV
                    rows={[
                      ['decision', <DecisionBadge key="d" decision={spawnResult.decision} />],
                      ['reason', val(spawnResult.reason)],
                      ['expected gain', <span key="g" className="mono">{fmtScore(spawnResult.expected_gain)}</span>],
                      ['estimated cost', <span key="c" className="mono">{fmtMoney(spawnResult.estimated_cost)}</span>],
                      ['risk', <span key="r" className="mono">{fmtScore(spawnResult.risk)}</span>],
                      ['decision id', <Hash key="id" id={spawnResult.decision_id} len={12} />],
                      ['agent', spawnResult.agent_id ? <Hash key="a" id={spawnResult.agent_id} len={12} /> : <span key="n" className="faint">none created</span>],
                    ]}
                  />
                  <p className="dim" style={{ marginTop: 10 }}>
                    The spawn policy decided; the decision is recorded in the spawn decisions ledger below.
                  </p>
                </>
              ) : (
                <>
                  <label className="lbl">Role</label>
                  <input
                    className="inp"
                    value={spawnRole}
                    onChange={(e) => setSpawnRole(e.target.value)}
                    placeholder="e.g. researcher"
                  />
                  <label className="lbl">Objective</label>
                  <textarea
                    className="inp"
                    rows={3}
                    value={spawnObjective}
                    onChange={(e) => setSpawnObjective(e.target.value)}
                    placeholder="What the child agent should do"
                  />
                  {spawnError && (
                    <div className="error-box" style={{ marginTop: 10 }}>
                      {spawnError}
                    </div>
                  )}
                </>
              )}
            </div>
            <div className="dialog-f">
              {spawnResult ? (
                <button
                  className="btn primary"
                  onClick={() => {
                    closeSpawn();
                    onChanged();
                  }}
                >
                  Done
                </button>
              ) : (
                <>
                  <button className="btn" onClick={closeSpawn} disabled={busy}>
                    Cancel
                  </button>
                  <button
                    className="btn primary"
                    disabled={busy || !spawnRole.trim() || !spawnObjective.trim()}
                    onClick={doSpawn}
                  >
                    {busy ? 'Requesting...' : 'Request spawn'}
                  </button>
                </>
              )}
            </div>
          </div>
        </div>
      )}

      {confirmTerminate && (
        <ConfirmDialog
          title="Terminate agent"
          body={
            <span>
              Terminate agent <span className="mono">{agentId.slice(0, 8)}</span> and its subtree?
              This is resolved by the runtime; it cannot be undone from the console.
            </span>
          }
          confirmLabel={busy ? 'Terminating...' : 'Terminate'}
          danger
          onConfirm={doTerminate}
          onCancel={() => setConfirmTerminate(false)}
        />
      )}
    </Panel>
  );
}

/* ------------------------------------------------------------ WHY panel */

function WhyPanel({
  decision,
  onClose,
}: {
  decision: SpawnDecisionSummary;
  onClose: () => void;
}) {
  const why = useApi(() => SpawnDecisions.get(decision.decision_id), [decision.decision_id]);
  const w = why.data;
  const inputs = (w?.inputs ?? null) as Record<string, unknown> | null;
  const strategy = inputs && typeof inputs.strategy === 'string' ? inputs.strategy : null;
  const evidence = w?.evidence_references?.evidence ?? [];
  const events = w?.evidence_references?.events ?? [];

  return (
    <Panel
      title="WHY: spawn decision"
      right={
        <button className="btn small" onClick={onClose}>
          Close
        </button>
      }
    >
      {why.loading ? (
        <Loading label="Loading decision evidence..." />
      ) : why.error ? (
        <ErrorBox error={why.error} onRetry={why.refresh} />
      ) : w ? (
        <>
          <KV
            rows={[
              ['decision id', <Hash key="id" id={w.id} len={12} />],
              ['role', val(decision.role)],
              ['strategy', val(strategy)],
              ['decision', <DecisionBadge key="d" decision={w.decision} />],
              ['reason', val(w.reason)],
              ['expected gain', <span key="g" className="mono">{fmtScore(w.scores.expected_gain)}</span>],
              ['estimated cost', <span key="c" className="mono">{fmtMoney(w.scores.estimated_cost)}</span>],
              ['risk', <span key="r" className="mono">{fmtScore(w.scores.risk)}</span>],
              ['spawn threshold', val((w.constraints ?? {})['spawn_threshold'])],
              ['budget agents remaining', val((w.constraints ?? {})['budget_agents_remaining'])],
            ]}
          />
          <div style={{ marginTop: 10 }}>
            <div className="faint" style={{ fontSize: 11.5, marginBottom: 4 }}>
              Evidence
            </div>
            {evidence.length === 0 ? (
              <Unknown />
            ) : (
              evidence.map((e, i) => (
                <div key={i} className="mono" style={{ fontSize: 12 }}>
                  {e}
                </div>
              ))
            )}
          </div>
          <div style={{ marginTop: 10 }}>
            <div className="faint" style={{ fontSize: 11.5, marginBottom: 4 }}>
              Events
            </div>
            {events.length === 0 ? (
              <Unknown />
            ) : (
              <table className="tbl">
                <thead>
                  <tr>
                    <th>Type</th>
                    <th>Time</th>
                  </tr>
                </thead>
                <tbody>
                  {events.map((ev, i) => (
                    <tr key={i}>
                      <td className="mono">{ev.type}</td>
                      <td>
                        <Time iso={ev.timestamp} ago />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
          <div style={{ marginTop: 10 }}>
            <div className="faint" style={{ fontSize: 11.5, marginBottom: 4 }}>
              Inputs
            </div>
            <JsonBlock data={w.inputs} />
          </div>
        </>
      ) : (
        <Empty title="Decision not found" />
      )}
    </Panel>
  );
}

/* ---------------------------------------------------------- events feed */

function EventsFeed({ runId }: { runId: string }) {
  const events = useLiveEvents((ev: StreamEvent) => ev.run_id === runId);
  const rows = useMemo(() => events.slice(-30).reverse(), [events]);
  return (
    <Panel
      title="Live events"
      right={<span className="mono faint">{rows.length} shown</span>}
      flush
    >
      {rows.length === 0 ? (
        <div style={{ padding: 12 }}>
          <Empty title="No live events yet" />
        </div>
      ) : (
        <table className="tbl">
          <thead>
            <tr>
              <th style={{ width: 90 }}>Time</th>
              <th style={{ width: 200 }}>Type</th>
              <th style={{ width: 120 }}>Agent</th>
              <th>Payload</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((ev) => (
              <tr key={ev.event_id}>
                <td>
                  <Time iso={ev.timestamp} ago />
                </td>
                <td className="mono">{ev.event_type}</td>
                <td>
                  <Hash id={ev.agent_id} len={8} />
                </td>
                <td
                  className="dim mono"
                  style={{
                    maxWidth: 420,
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                  }}
                  title={JSON.stringify(ev.payload)}
                >
                  {summarizePayload(ev.payload)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Panel>
  );
}

/* ------------------------------------------------------ no provider note */

function NoProviderPanel() {
  return (
    <Panel title="Model execution unavailable">
      <p>Core systems operational.</p>
      <p className="dim">
        Model execution is currently unavailable, so agents cannot run. The following remain
        fully inspectable:
      </p>
      <p className="dim">
        Event ledger, memory, experiences, policies, evaluations, assurance.
      </p>
    </Panel>
  );
}

/* ---------------------------------------------------------------- screen */

export default function LiveScreen() {
  const { runId } = useParams<{ runId: string }>();
  const id = runId ?? '';

  const watch = useMemo(() => watchRun(id), [id]);
  const run = useApi(() => Runs.get(id), [id], { watch, skip: !id });
  const agents = useApi(() => Runs.agents(id), [id], { watch, skip: !id });
  const explain = useApi(() => Runs.explain(id), [id], { watch, skip: !id });
  const messages = useApi(() => Runs.messages(id, 200), [id], { watch, skip: !id });
  const spawnDecisions = useApi(() => Runs.spawnDecisions(id), [id], { watch, skip: !id });
  const models = useApi(() => Models.list(), [], { pollMs: 60000 });

  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, []);

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [whyDecision, setWhyDecision] = useState<SpawnDecisionSummary | null>(null);
  const [confirmCancel, setConfirmCancel] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const r = run.data;
  const x = explain.data;

  const graphNodes: CognitiveNode[] = useMemo(
    () =>
      (agents.data ?? []).map((a) => ({
        id: a.id,
        role: a.role,
        status: a.status,
        generation: a.generation ?? 0,
      })),
    [agents.data],
  );

  const graphEdges: CognitiveEdge[] = useMemo(() => {
    const list: AgentRow[] = agents.data ?? [];
    const ids = new Set(list.map((a) => a.id));
    const edges: CognitiveEdge[] = [];
    const seen = new Set<string>();
    const add = (from: string, to: string, kind: CognitiveEdge['kind']) => {
      if (from === to || !ids.has(from) || !ids.has(to)) return;
      const key = `${kind}:${from}->${to}`;
      if (seen.has(key)) return;
      seen.add(key);
      edges.push({ from, to, kind });
    };
    for (const a of list) {
      if (a.parent_id) add(a.parent_id, a.id, 'delegation');
    }
    const siblingGroups = new Map<string, AgentRow[]>();
    for (const a of list) {
      if (!a.parent_id) continue;
      const g = siblingGroups.get(a.parent_id);
      if (g) g.push(a);
      else siblingGroups.set(a.parent_id, [a]);
    }
    for (const g of siblingGroups.values()) {
      for (let i = 0; i + 1 < g.length; i++) add(g[i].id, g[i + 1].id, 'sibling');
    }
    for (const m of messages.data ?? []) {
      if (m.from_agent_id && m.to_agent_id) add(m.from_agent_id, m.to_agent_id, 'message');
    }
    return edges;
  }, [agents.data, messages.data]);

  const noProvider =
    (models.data ?? []).length > 0 && (models.data ?? []).every((m) => m.model === 'unavailable');

  const isTerminal = r ? TERMINAL_RUN_STATES.includes(r.status) : true;
  const runMs = r ? elapsedMs(r.started_at ?? r.created_at, r.completed_at, now) : null;
  const vErr = x?.verification_state?.error ?? null;
  const policyVersion = x?.policy_version ?? null;

  const doControl = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setActionError(null);
    try {
      await fn();
      run.refresh();
      agents.refresh();
      spawnDecisions.refresh();
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const refreshAll = () => {
    run.refresh();
    agents.refresh();
    spawnDecisions.refresh();
    messages.refresh();
  };

  if (!id) {
    return <ErrorBox error="Missing run id" />;
  }

  return (
    <div>
      <div className="page-head">
        <h1>RUN {r ? r.id.slice(0, 8) : '...'}</h1>
        <div className="sub">{r ? r.goal : 'Loading run...'}</div>
      </div>

      {run.error && <ErrorBox error={run.error} onRetry={run.refresh} />}
      {actionError && <ErrorBox error={actionError} />}

      {r && (
        <>
          <div className="toolbar">
            <button
              className="btn small"
              disabled={busy || isTerminal || r.status === 'PAUSED'}
              onClick={() => doControl(() => Runs.pause(id))}
            >
              Pause
            </button>
            <button
              className="btn small"
              disabled={busy || r.status !== 'PAUSED'}
              onClick={() => doControl(() => Runs.resume(id))}
            >
              Resume
            </button>
            <button
              className="btn small danger"
              disabled={busy || isTerminal}
              onClick={() => setConfirmCancel(true)}
            >
              Cancel
            </button>
          </div>

          <div className="stat-row" style={{ marginBottom: 14 }}>
            <div className="stat">
              <div className="v">
                <Badge status={r.status} />
              </div>
              <div className="k">status</div>
            </div>
            <div className="stat">
              <div className="v">{runMs === null ? 'unknown' : fmtElapsed(runMs)}</div>
              <div className="k">elapsed</div>
            </div>
            <div className="stat">
              <div className="v">{fmtMoney(r.total_cost)}</div>
              <div className="k">cost</div>
            </div>
            <div className="stat">
              <div className="v">{agents.data ? agents.data.length : 'unknown'}</div>
              <div className="k">agents</div>
            </div>
            <div className="stat">
              <div className="v">{policyVersion ?? 'unknown'}</div>
              <div className="k">policy version</div>
            </div>
            <div className="stat">
              <div className="v">
                {vErr ? (
                  <Badge status="INVALID">{vErr}</Badge>
                ) : r.status === 'COMPLETED' ? (
                  <Badge status="SOUND">clean</Badge>
                ) : (
                  <span className="faint">pending</span>
                )}
              </div>
              <div className="k">verification</div>
            </div>
          </div>
        </>
      )}

      {noProvider && <NoProviderPanel />}

      <Panel
        title="Cognitive graph"
        right={
          selectedId ? (
            <span style={{ display: 'inline-flex', gap: 8, alignItems: 'center' }}>
              <span className="mono faint">{selectedId.slice(0, 8)}</span>
              <button className="btn small" onClick={() => setSelectedId(null)}>
                Clear
              </button>
            </span>
          ) : (
            <span className="faint">click a node to inspect</span>
          )
        }
      >
        {agents.loading && !agents.data ? (
          <Loading label="Loading agents..." />
        ) : agents.error ? (
          <ErrorBox error={agents.error} onRetry={agents.refresh} />
        ) : (
          <CognitiveGraph
            nodes={graphNodes}
            edges={graphEdges}
            selectedId={selectedId}
            onSelect={setSelectedId}
            height={400}
          />
        )}
      </Panel>

      {selectedId && (
        <AgentDetail
          agentId={selectedId}
          now={now}
          onClose={() => setSelectedId(null)}
          onChanged={refreshAll}
        />
      )}

      <Panel
        title="Spawn decisions"
        right={
          <span className="faint" style={{ fontSize: 11.5 }}>
            approvals and denials are both listed
          </span>
        }
        flush
      >
        {spawnDecisions.loading && !spawnDecisions.data ? (
          <Loading label="Loading spawn decisions..." />
        ) : spawnDecisions.error ? (
          <div style={{ padding: 12 }}>
            <ErrorBox error={spawnDecisions.error} onRetry={spawnDecisions.refresh} />
          </div>
        ) : (spawnDecisions.data ?? []).length === 0 ? (
          <div style={{ padding: 12 }}>
            <Empty title="No spawn decisions recorded" />
          </div>
        ) : (
          <table className="tbl">
            <thead>
              <tr>
                <th>Decision</th>
                <th>Role</th>
                <th>Verdict</th>
                <th>Expected gain</th>
                <th>Est. cost</th>
                <th>Risk</th>
                <th>Time</th>
              </tr>
            </thead>
            <tbody>
              {(spawnDecisions.data ?? []).map((d) => {
                const last = d.events[d.events.length - 1];
                return (
                  <tr key={d.decision_id}>
                    <td>
                      <button className="btn small" onClick={() => setWhyDecision(d)}>
                        <Hash id={d.decision_id} len={8} />
                      </button>
                    </td>
                    <td>{d.role ?? 'unknown'}</td>
                    <td>
                      <DecisionBadge decision={d.decision} />
                    </td>
                    <td className="mono">{fmtScore(d.expected_gain)}</td>
                    <td className="mono">{fmtMoney(d.estimated_cost)}</td>
                    <td className="mono">{fmtScore(d.risk)}</td>
                    <td>{last ? <Time iso={last.timestamp} ago /> : <Unknown />}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </Panel>

      {whyDecision && (
        <WhyPanel decision={whyDecision} onClose={() => setWhyDecision(null)} />
      )}

      <EventsFeed runId={id} />

      {confirmCancel && (
        <ConfirmDialog
          title="Cancel run"
          body={
            <span>
              Cancel run <span className="mono">{id.slice(0, 8)}</span>? Running agents will be
              stopped. This is resolved by the runtime and cannot be undone from the console.
            </span>
          }
          confirmLabel="Cancel run"
          danger
          onConfirm={() => {
            setConfirmCancel(false);
            doControl(() => Runs.cancel(id));
          }}
          onCancel={() => setConfirmCancel(false)}
        />
      )}
    </div>
  );
}
