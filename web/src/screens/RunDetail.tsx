/* Run Detail: forensic screen for a single run.
   Every value comes from the backend REST contract. Missing backend data
   renders as "unknown". Nothing here is simulated or inferred. */

import { useEffect, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import {
  Agents,
  ApiError,
  Assurance,
  Eval,
  Experience,
  Policies,
  Runs,
  Tools,
} from '../lib/api';
import { useApi, watchRun } from '../lib/query';
import CognitiveGraph, { type CognitiveEdge } from '../components/CognitiveGraph';
import EvidenceChain from '../components/EvidenceChain';
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
  Tabs,
  Time,
  Unknown,
  fmtElapsed,
  fmtMoney,
  val,
} from '../components/ui';
import type {
  AgentMessage,
  ExperienceSummary,
  StreamEvent,
  ToolCallDetail,
} from '../lib/types';

const TABS = [
  { id: 'overview', label: 'Overview' },
  { id: 'graph', label: 'Cognitive Graph' },
  { id: 'timeline', label: 'Timeline' },
  { id: 'agents', label: 'Agents' },
  { id: 'tools', label: 'Tools' },
  { id: 'messages', label: 'Messages' },
  { id: 'artifacts', label: 'Artifacts' },
  { id: 'experience', label: 'Experience' },
  { id: 'evaluation', label: 'Evaluation' },
  { id: 'assurance', label: 'Assurance' },
  { id: 'policy', label: 'Policy' },
  { id: 'replay', label: 'Replay' },
];

function payloadSummary(ev: StreamEvent): string {
  try {
    return JSON.stringify(ev.payload ?? {}).slice(0, 120);
  } catch {
    return 'unserializable payload';
  }
}

function messageSummary(m: AgentMessage): string {
  try {
    return JSON.stringify(m.payload ?? {}).slice(0, 120);
  } catch {
    return 'unserializable payload';
  }
}

/** Extract human-readable reasons from a failed mutation (e.g. 409 bridge block). */
function errorDetails(e: unknown): string[] {
  if (e instanceof ApiError) {
    try {
      const parsed = JSON.parse(e.body) as { detail?: unknown };
      const d = parsed?.detail;
      if (d && typeof d === 'object') {
        const blocked = (d as { blocked?: unknown }).blocked;
        if (Array.isArray(blocked)) return blocked.map((r) => String(r));
      }
      if (typeof d === 'string') return [`API ${e.status}: ${d}`];
    } catch {
      /* not JSON: fall through to raw body */
    }
    return [`API ${e.status}: ${e.body.slice(0, 300)}`];
  }
  return [e instanceof Error ? e.message : String(e)];
}

export default function RunDetailScreen() {
  const { runId } = useParams();
  const [tab, setTab] = useState('overview');
  const header = useApi(() => Runs.get(runId ?? ''), [runId], {
    skip: !runId,
    watch: runId ? watchRun(runId) : undefined,
  });
  if (!runId) return <ErrorBox error="Missing run id in route." />;

  return (
    <div>
      <div className="page-head">
        <h1>
          Run <Hash id={runId} />
        </h1>
        <div className="sub">{header.data ? header.data.goal : 'Loading run...'}</div>
      </div>
      <div className="toolbar">
        {header.data ? (
          <Badge status={header.data.status}>{header.data.status}</Badge>
        ) : (
          <Unknown />
        )}
        <Link to="/runs" className="btn small">
          All runs
        </Link>
        <Link to={`/runs/${runId}/live`} className="btn small">
          Live view
        </Link>
      </div>
      <Tabs tabs={TABS} active={tab} onChange={setTab} />
      {tab === 'overview' && <OverviewTab runId={runId} />}
      {tab === 'graph' && <GraphTab runId={runId} />}
      {tab === 'timeline' && <TimelineTab runId={runId} />}
      {tab === 'agents' && <AgentsTab runId={runId} />}
      {tab === 'tools' && <ToolsTab runId={runId} />}
      {tab === 'messages' && <MessagesTab runId={runId} />}
      {tab === 'artifacts' && <ArtifactsTab runId={runId} />}
      {tab === 'experience' && <ExperienceTab runId={runId} />}
      {tab === 'evaluation' && <EvaluationTab runId={runId} />}
      {tab === 'assurance' && <AssuranceTab />}
      {tab === 'policy' && <PolicyTab runId={runId} />}
      {tab === 'replay' && <ReplayTab runId={runId} />}
    </div>
  );
}

/* ------------------------------------------------------------ overview */

function OverviewTab({ runId }: { runId: string }) {
  const { data, error, loading, refresh } = useApi(() => Runs.explain(runId), [runId], {
    watch: watchRun(runId),
  });
  if (loading && !data) return <Loading label="Loading run explanation..." />;
  if (error) return <ErrorBox error={error} onRetry={refresh} />;
  if (!data) return <Empty title="No explanation returned by the backend" />;

  const ev = data.evidence_references;
  const types = Object.entries(ev.event_types ?? {}).sort((a, b) => b[1] - a[1]);
  const pv = data.policy_version;
  const policyName = pv && pv.includes('@') ? pv.split('@')[0] : null;

  return (
    <div>
      <Panel title="Run">
        <KV
          rows={[
            ['status', <Badge status={data.decision.status}>{data.decision.status}</Badge>],
            ['strategy', val(data.decision.strategy)],
            ['goal', val(data.inputs.goal)],
            ['seed', val(data.inputs.seed)],
            [
              'policy_version',
              policyName ? (
                <Link to={`/policies/${policyName}`} className="mono">
                  {pv}
                </Link>
              ) : (
                val(pv)
              ),
            ],
            ['total_cost', <span className="mono">{fmtMoney(data.budget_state.total_cost)}</span>],
            ['total_tokens', val(data.budget_state.total_tokens)],
            ['error', val(data.verification_state.error)],
          ]}
        />
      </Panel>
      {(data.scores != null || data.candidate_strategies != null) && (
        <Panel title="Strategy scoring">
          {data.scores != null && (
            <div>
              <div className="faint">scores</div>
              <JsonBlock data={data.scores} />
            </div>
          )}
          {data.candidate_strategies != null && (
            <div style={{ marginTop: 10 }}>
              <div className="faint">candidate_strategies</div>
              <JsonBlock data={data.candidate_strategies} />
            </div>
          )}
        </Panel>
      )}
      <Panel title="Evidence references">
        <KV
          rows={[
            ['event_count', val(ev.event_count)],
            ['first_event_id', <Hash id={ev.first_event_id} />],
            ['last_event_id', <Hash id={ev.last_event_id} />],
          ]}
        />
        <table className="tbl" style={{ marginTop: 10 }}>
          <thead>
            <tr>
              <th>event_type</th>
              <th>count</th>
            </tr>
          </thead>
          <tbody>
            {types.map(([t, c]) => (
              <tr key={t}>
                <td className="mono">{t}</td>
                <td className="mono">{c}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>
    </div>
  );
}

/* ---------------------------------------------------------------- graph */

function GraphTab({ runId }: { runId: string }) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const agentsQ = useApi(() => Runs.agents(runId), [runId], { watch: watchRun(runId) });
  const msgsQ = useApi(() => Runs.messages(runId, 200), [runId], { watch: watchRun(runId) });
  const agents = agentsQ.data ?? [];
  const messages = msgsQ.data ?? [];

  const nodes = useMemo(
    () =>
      agents.map((a) => ({
        id: a.id,
        role: a.role,
        status: a.status,
        generation: a.generation ?? 0,
      })),
    [agents],
  );
  const edges = useMemo<CognitiveEdge[]>(() => {
    const list: CognitiveEdge[] = [];
    const seen = new Set<string>();
    for (const a of agents) {
      if (a.parent_id) {
        const key = `delegation:${a.parent_id}->${a.id}`;
        if (!seen.has(key)) {
          seen.add(key);
          list.push({ from: a.parent_id, to: a.id, kind: 'delegation' });
        }
      }
    }
    for (const m of messages) {
      if (m.from_agent_id && m.to_agent_id) {
        const key = `message:${m.from_agent_id}->${m.to_agent_id}`;
        if (!seen.has(key)) {
          seen.add(key);
          list.push({ from: m.from_agent_id, to: m.to_agent_id, kind: 'message' });
        }
      }
    }
    return list;
  }, [agents, messages]);

  const selected = agents.find((a) => a.id === selectedId) ?? null;

  if (agentsQ.loading && agents.length === 0) return <Loading label="Loading agents..." />;
  if (agentsQ.error) return <ErrorBox error={agentsQ.error} onRetry={agentsQ.refresh} />;

  return (
    <div>
      <Panel
        title="Cognitive graph"
        right={
          <span className="faint">
            {nodes.length} agents, {edges.length} edges
          </span>
        }
      >
        <CognitiveGraph
          nodes={nodes}
          edges={edges}
          selectedId={selectedId}
          onSelect={setSelectedId}
          height={440}
        />
        <div className="faint" style={{ marginTop: 8 }}>
          Delegation edges come from agent parent_id. Message edges come from recorded agent
          messages (deduplicated by sender and recipient).
        </div>
      </Panel>
      {selected && (
        <Panel title="Selected agent">
          <KV
            rows={[
              ['id', <Hash id={selected.id} />],
              ['role', val(selected.role)],
              ['status', <Badge status={selected.status}>{selected.status}</Badge>],
              ['generation', val(selected.generation)],
              ['parent', <Hash id={selected.parent_id} />],
              ['objective', val(selected.objective)],
            ]}
          />
        </Panel>
      )}
    </div>
  );
}

/* ------------------------------------------------------------- timeline */

function TimelineTab({ runId }: { runId: string }) {
  const { data, error, loading, refresh } = useApi(() => Runs.events(runId, { limit: 1000 }), [runId], {
    watch: watchRun(runId),
  });
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const events = data ?? [];
  const selected = events.find((e) => e.event_id === selectedId) ?? null;

  if (loading && events.length === 0) return <Loading label="Loading events..." />;
  if (error) return <ErrorBox error={error} onRetry={refresh} />;

  return (
    <div>
      <Panel title={`Timeline (${events.length} events)`} flush>
        {events.length === 0 ? (
          <div style={{ padding: 14 }}>
            <Empty title="No events recorded for this run" />
          </div>
        ) : (
          <table className="tbl">
            <thead>
              <tr>
                <th>time</th>
                <th>event_type</th>
                <th>agent</th>
                <th>payload</th>
              </tr>
            </thead>
            <tbody>
              {events.map((e) => (
                <tr
                  key={e.event_id}
                  className={e.event_id === selectedId ? 'clickable selected' : 'clickable'}
                  onClick={() => setSelectedId(e.event_id)}
                >
                  <td>
                    <Time iso={e.timestamp} />
                  </td>
                  <td className="mono">{e.event_type}</td>
                  <td>
                    <Hash id={e.agent_id} />
                  </td>
                  <td className="mono faint">{payloadSummary(e)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>
      {selected && (
        <Panel title="Selected event">
          <KV
            rows={[
              ['event_id', <Hash id={selected.event_id} />],
              ['event_type', <span className="mono">{selected.event_type}</span>],
              ['timestamp', <Time iso={selected.timestamp} />],
              ['sequence', val(selected.sequence)],
              ['causation_id', <Hash id={selected.causation_id} />],
              ['correlation_id', <Hash id={selected.correlation_id} />],
            ]}
          />
          <JsonBlock data={selected.payload} />
        </Panel>
      )}
    </div>
  );
}

/* --------------------------------------------------------------- agents */

function AgentsTab({ runId }: { runId: string }) {
  const { data, error, loading, refresh } = useApi(() => Runs.agents(runId), [runId], {
    watch: watchRun(runId),
  });
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const agents = data ?? [];

  if (loading && agents.length === 0) return <Loading label="Loading agents..." />;
  if (error) return <ErrorBox error={error} onRetry={refresh} />;

  return (
    <div className="grid2">
      <Panel title={`Agents (${agents.length})`} flush>
        {agents.length === 0 ? (
          <div style={{ padding: 14 }}>
            <Empty title="No agents recorded for this run" />
          </div>
        ) : (
          <table className="tbl">
            <thead>
              <tr>
                <th>id</th>
                <th>role</th>
                <th>status</th>
                <th>gen</th>
                <th>parent</th>
              </tr>
            </thead>
            <tbody>
              {agents.map((a) => (
                <tr
                  key={a.id}
                  className={a.id === selectedId ? 'clickable selected' : 'clickable'}
                  onClick={() => setSelectedId(a.id)}
                >
                  <td>
                    <Hash id={a.id} />
                  </td>
                  <td>{a.role}</td>
                  <td>
                    <Badge status={a.status}>{a.status}</Badge>
                  </td>
                  <td className="mono">{a.generation ?? <Unknown />}</td>
                  <td>
                    <Hash id={a.parent_id} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>
      <div>
        {selectedId ? (
          <AgentDetail
            agentId={selectedId}
            onTerminated={() => {
              setSelectedId(null);
              refresh();
            }}
          />
        ) : (
          <Empty title="Select an agent" >
            <p>Click a row to inspect the agent explanation and terminate it.</p>
          </Empty>
        )}
      </div>
    </div>
  );
}

function AgentDetail({ agentId, onTerminated }: { agentId: string; onTerminated: () => void }) {
  const { data, error, loading, refresh } = useApi(() => Agents.explain(agentId), [agentId]);
  const [confirming, setConfirming] = useState(false);
  const [termError, setTermError] = useState<Error | null>(null);
  const [terminating, setTerminating] = useState(false);

  async function terminate() {
    setTerminating(true);
    setTermError(null);
    try {
      await Agents.terminate(agentId, true);
      setConfirming(false);
      onTerminated();
    } catch (e) {
      setTermError(e instanceof Error ? e : new Error(String(e)));
    } finally {
      setTerminating(false);
    }
  }

  if (loading && !data) return <Loading label="Loading agent explanation..." />;
  if (error) return <ErrorBox error={error} onRetry={refresh} />;
  if (!data) return <Empty title="No explanation returned by the backend" />;

  return (
    <div>
      <Panel
        title="Agent explanation"
        right={
          <button className="btn small danger" onClick={() => setConfirming(true)}>
            Terminate
          </button>
        }
      >
        <KV
          rows={[
            ['id', <Hash id={data.id} />],
            ['status', <Badge status={data.decision.status}>{data.decision.status}</Badge>],
            ['status_reason', val(data.decision.status_reason)],
            ['role', val(data.inputs.role)],
            ['objective', val(data.inputs.objective)],
            ['specialization', val(data.inputs.specialization)],
            ['memory_scope', val(data.constraints.memory_scope)],
            ['budget', val(data.constraints.budget)],
            ['capabilities', val(data.authorization_checks.capabilities)],
            ['granted_capabilities', val(data.authorization_checks.granted_capabilities)],
            ['tools', val(data.authorization_checks.tools)],
            ['policy_version', val(data.policy_version)],
            ['parent_id', <Hash id={data.evidence_references.parent_id} />],
            ['root_run_id', <Hash id={data.evidence_references.root_run_id} />],
            ['lineage', val(data.evidence_references.lineage)],
          ]}
        />
      </Panel>
      {termError && <ErrorBox error={termError} />}
      {confirming && (
        <ConfirmDialog
          title="Terminate agent"
          body={
            <div>
              <p>
                Terminate agent <Hash id={agentId} /> and its subtree? This is a destructive
                runtime action.
              </p>
            </div>
          }
          confirmLabel={terminating ? 'Terminating...' : 'Terminate agent'}
          danger
          onConfirm={terminate}
          onCancel={() => setConfirming(false)}
        />
      )}
    </div>
  );
}

/* ---------------------------------------------------------------- tools */

function ToolsTab({ runId }: { runId: string }) {
  const { data, error, loading, refresh } = useApi(() => Tools.calls({ run_id: runId, limit: 200 }), [runId], {
    watch: watchRun(runId),
  });
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const calls = data ?? [];

  if (loading && calls.length === 0) return <Loading label="Loading tool calls..." />;
  if (error) return <ErrorBox error={error} onRetry={refresh} />;

  return (
    <div>
      <Panel title={`Tool calls (${calls.length})`} flush>
        {calls.length === 0 ? (
          <div style={{ padding: 14 }}>
            <Empty title="No tool calls recorded for this run" />
          </div>
        ) : (
          <table className="tbl">
            <thead>
              <tr>
                <th>time</th>
                <th>tool</th>
                <th>agent</th>
                <th>capability</th>
                <th>state</th>
                <th>verification</th>
              </tr>
            </thead>
            <tbody>
              {calls.map((c) => (
                <tr
                  key={c.id}
                  className={c.id === selectedId ? 'clickable selected' : 'clickable'}
                  onClick={() => setSelectedId(c.id)}
                >
                  <td>
                    <Time iso={c.created_at} />
                  </td>
                  <td className="mono">{c.tool_name}</td>
                  <td>
                    <Hash id={c.agent_id} />
                  </td>
                  <td className="mono">{c.capability}</td>
                  <td>
                    <Badge status={c.state}>{c.state}</Badge>
                  </td>
                  <td>{c.verification_status ? <Badge status={c.verification_status}>{c.verification_status}</Badge> : <Unknown />}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>
      {selectedId && <ToolCallDetailView callId={selectedId} />}
    </div>
  );
}

function ToolCallDetailView({ callId }: { callId: string }) {
  const detail = useApi(() => Tools.get(callId), [callId]);
  const auth = useApi(() => Tools.authorization(callId), [callId]);
  const d = detail.data;

  return (
    <div style={{ marginTop: 14 }}>
      {detail.loading && !d && <Loading label="Loading tool call detail..." />}
      {detail.error && <ErrorBox error={detail.error} onRetry={detail.refresh} />}
      {d && (
        <Panel title="Tool call detail">
          <KV
            rows={[
              ['id', <Hash id={d.id} />],
              ['tool_name', <span className="mono">{d.tool_name}</span>],
              ['capability', val(d.capability)],
              ['agent_id', <Hash id={d.agent_id} />],
              ['run_id', <Hash id={d.run_id} />],
              ['state', <Badge status={d.state}>{d.state}</Badge>],
              ['verification_status', d.verification_status ? <Badge status={d.verification_status}>{d.verification_status}</Badge> : <Unknown />],
              ['server_id', val(d.server_id)],
              ['policy_version', val(d.policy_version)],
              ['approval_id', val(d.approval_id)],
              ['latency_ms', val(d.latency_ms)],
              ['created_at', <Time iso={d.created_at} />],
              ['completed_at', <Time iso={d.completed_at} />],
              ['error', val(d.error)],
              ['args_hash', val(d.args_hash)],
            ]}
          />
          <div style={{ marginTop: 10 }}>
            <div className="faint">args_redacted</div>
            <JsonBlock data={d.args_redacted} />
          </div>
          <div style={{ marginTop: 10 }}>
            <div className="faint">result_redacted</div>
            <JsonBlock data={d.result_redacted} />
          </div>
          <div style={{ marginTop: 10 }}>
            <div className="faint">provenance</div>
            <JsonBlock data={d.provenance} />
          </div>
        </Panel>
      )}
      <Panel title="Authorization">
        {auth.loading && !auth.data && <Loading label="Loading authorization..." />}
        {auth.error && <ErrorBox error={auth.error} onRetry={auth.refresh} />}
        {auth.data && (
          <div>
            <KV
              rows={[
                ['decision.state', <Badge status={auth.data.decision.state}>{auth.data.decision.state}</Badge>],
                ['decision.approved', val(auth.data.decision.approved)],
                ['tool_name', val(auth.data.inputs.tool_name)],
                ['capability', val(auth.data.inputs.capability)],
                ['server_id', val(auth.data.inputs.server_id)],
                ['policy_version', val(auth.data.policy_version)],
                ['budget_state', val(auth.data.budget_state)],
                ['verification_status', val(auth.data.verification_state.verification_status)],
              ]}
            />
            <div style={{ marginTop: 10 }}>
              <div className="faint">authorization_checks</div>
              <JsonBlock data={auth.data.authorization_checks} />
            </div>
          </div>
        )}
      </Panel>
    </div>
  );
}

/* ------------------------------------------------------------- messages */

function MessagesTab({ runId }: { runId: string }) {
  const { data, error, loading, refresh } = useApi(() => Runs.messages(runId, 200), [runId], {
    watch: watchRun(runId),
  });
  const messages = data ?? [];

  if (loading && messages.length === 0) return <Loading label="Loading messages..." />;
  if (error) return <ErrorBox error={error} onRetry={refresh} />;

  return (
    <Panel title={`Messages (${messages.length})`} flush>
      {messages.length === 0 ? (
        <div style={{ padding: 14 }}>
          <Empty title="No messages recorded for this run" />
        </div>
      ) : (
        <table className="tbl">
          <thead>
            <tr>
              <th>time</th>
              <th>from</th>
              <th>to</th>
              <th>channel</th>
              <th>kind</th>
              <th>payload</th>
            </tr>
          </thead>
          <tbody>
            {messages.map((m) => (
              <tr key={m.id}>
                <td>
                  <Time iso={m.created_at} />
                </td>
                <td>
                  <Hash id={m.from_agent_id} />
                </td>
                <td>
                  <Hash id={m.to_agent_id} />
                </td>
                <td className="mono">{m.channel}</td>
                <td className="mono">{m.kind}</td>
                <td className="mono faint">{messageSummary(m)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Panel>
  );
}

/* ------------------------------------------------------------ artifacts */

function ArtifactsTab({ runId }: { runId: string }) {
  const { data, error, loading, refresh } = useApi(() => Tools.calls({ run_id: runId, limit: 200 }), [runId], {
    watch: watchRun(runId),
  });
  const [details, setDetails] = useState<Record<string, ToolCallDetail | null>>({});
  const committed = useMemo(
    () => (data ?? []).filter((c) => c.tool_name === 'fs.write' && c.state === 'COMMITTED'),
    [data],
  );

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const entries = await Promise.all(
        committed.map(async (c) => {
          try {
            const d = await Tools.get(c.id);
            return [c.id, d] as const;
          } catch {
            return [c.id, null] as const;
          }
        }),
      );
      if (!cancelled) setDetails(Object.fromEntries(entries));
    })();
    return () => {
      cancelled = true;
    };
  }, [committed]);

  if (loading && (data ?? []).length === 0) return <Loading label="Loading tool calls..." />;
  if (error) return <ErrorBox error={error} onRetry={refresh} />;
  if (committed.length === 0) {
    return <Empty title="No file artifacts produced by tool calls in this run." />;
  }

  return (
    <Panel title={`File artifacts (${committed.length})`} flush>
      <table className="tbl">
        <thead>
          <tr>
            <th>path</th>
            <th>tool call</th>
            <th>completed_at</th>
          </tr>
        </thead>
        <tbody>
          {committed.map((c) => {
            const d = details[c.id];
            const p = d?.args_redacted;
            const path =
              p && typeof p === 'object' && typeof (p as Record<string, unknown>).path === 'string'
                ? String((p as Record<string, unknown>).path)
                : null;
            return (
              <tr key={c.id}>
                <td>{path ? <span className="mono">{path}</span> : <Unknown />}</td>
                <td>
                  <Hash id={c.id} />
                </td>
                <td>
                  <Time iso={d?.completed_at} />
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <div className="faint" style={{ padding: '8px 12px' }}>
        Derived honestly: committed fs.write tool calls in this run. Paths come from the
        redacted call arguments; nothing is listed that the backend did not record.
      </div>
    </Panel>
  );
}

/* ----------------------------------------------------------- experience */

function ExperienceTab({ runId }: { runId: string }) {
  const { data, error, loading, refresh } = useApi(() => Experience.list(runId, 50), [runId], {
    watch: watchRun(runId),
  });
  const exps = data ?? [];

  return (
    <div>
      <EvidenceChain runId={runId} />
      <Panel title={`Experiences (${exps.length})`}>
        {loading && exps.length === 0 && <Loading label="Loading experiences..." />}
        {error && <ErrorBox error={error} onRetry={refresh} />}
        {exps.length === 0 && !loading && (
          <Empty title="No experiences recorded for this run" />
        )}
        <div className="grid2">
          {exps.map((e) => (
            <ExperienceCard key={e.id} exp={e} />
          ))}
        </div>
      </Panel>
      <ComparePanel />
    </div>
  );
}

function ExperienceCard({ exp }: { exp: ExperienceSummary }) {
  const [namespace, setNamespace] = useState('global');
  const [promoting, setPromoting] = useState(false);
  const [memoryId, setMemoryId] = useState<string | null>(null);
  const [promoteProblems, setPromoteProblems] = useState<string[] | null>(null);
  const [showLineage, setShowLineage] = useState(false);

  async function onPromote() {
    setPromoting(true);
    setMemoryId(null);
    setPromoteProblems(null);
    try {
      const r = await Experience.promote(exp.id, namespace.trim() || 'global');
      setMemoryId(r.memory_id);
    } catch (e) {
      setPromoteProblems(errorDetails(e));
    } finally {
      setPromoting(false);
    }
  }

  return (
    <Panel
      title="Experience"
      right={
        <span className="faint">
          <Time iso={exp.created_at} />
        </span>
      }
    >
      <KV
        rows={[
          ['id', <Hash id={exp.id} />],
          ['goal', val(exp.goal)],
          ['cost', <span className="mono">{fmtMoney(exp.cost)}</span>],
          ['latency', <span className="mono">{fmtElapsed(exp.latency_ms)}</span>],
        ]}
      />
      <div className="toolbar" style={{ marginTop: 10, marginBottom: 0 }}>
        <input
          className="inp"
          style={{ width: 160 }}
          value={namespace}
          onChange={(e) => setNamespace(e.target.value)}
          placeholder="namespace"
          aria-label="namespace"
        />
        <button className="btn small primary" onClick={onPromote} disabled={promoting}>
          {promoting ? 'Promoting...' : 'Promote'}
        </button>
        <button className="btn small" onClick={() => setShowLineage(!showLineage)}>
          {showLineage ? 'Hide lineage' : 'Lineage'}
        </button>
      </div>
      {memoryId && (
        <div className="stat-row" style={{ marginTop: 8 }}>
          <div className="stat">
            <div className="k">promoted memory_id</div>
            <div className="v" style={{ fontSize: 13 }}>
              <Hash id={memoryId} />
            </div>
          </div>
        </div>
      )}
      {promoteProblems && (
        <div className="error-box" style={{ marginTop: 8 }}>
          <div style={{ fontWeight: 600, marginBottom: 4 }}>Promotion blocked</div>
          {promoteProblems.map((p, i) => (
            <div key={i}>{p}</div>
          ))}
        </div>
      )}
      {showLineage && <LineagePanel expId={exp.id} />}
    </Panel>
  );
}

function refIds(v: unknown): string[] {
  if (!v || !Array.isArray(v)) return [];
  const out: string[] = [];
  for (const item of v) {
    if (typeof item === 'string' && item) out.push(item);
    else if (item && typeof item === 'object') {
      const id = (item as Record<string, unknown>).id;
      if (typeof id === 'string' && id) out.push(id);
    }
  }
  return out;
}

function LineagePanel({ expId }: { expId: string }) {
  const { data, error, loading } = useApi(() => Experience.lineage(expId), [expId]);
  if (loading) return <Loading label="Loading lineage..." />;
  if (error) return <ErrorBox error={error} />;
  if (!data) return <Empty title="No lineage returned by the backend" />;

  const refs = data.evidence_references;
  const evalIds = refIds(refs.evaluation_refs);
  const assuranceIds = refIds(refs.assurance_refs);
  const derived = Array.isArray(refs.derived_memories) ? refs.derived_memories : [];

  return (
    <div style={{ marginTop: 10 }}>
      <div className="faint">evaluation_refs</div>
      {evalIds.length === 0 ? (
        <div className="faint">none recorded</div>
      ) : (
        evalIds.map((id) => (
          <div key={id} className="mono">
            <Hash id={id} />
          </div>
        ))
      )}
      <div className="faint" style={{ marginTop: 8 }}>
        assurance_refs
      </div>
      {assuranceIds.length === 0 ? (
        <div className="faint">none recorded</div>
      ) : (
        assuranceIds.map((id) => (
          <div key={id} className="mono">
            <Hash id={id} />
          </div>
        ))
      )}
      <div className="faint" style={{ marginTop: 8 }}>
        derived_memories
      </div>
      {derived.length === 0 ? (
        <div className="faint">none recorded</div>
      ) : (
        <table className="tbl">
          <thead>
            <tr>
              <th>id</th>
              <th>type</th>
              <th>created</th>
            </tr>
          </thead>
          <tbody>
            {derived.map((m) => (
              <tr key={m.id}>
                <td>
                  <Link to="/memory" className="mono">
                    <Hash id={m.id} />
                  </Link>
                </td>
                <td className="mono">{m.type}</td>
                <td>
                  <Time iso={m.created_at} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function ComparePanel() {
  const [a, setA] = useState('');
  const [b, setB] = useState('');
  const [result, setResult] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [busy, setBusy] = useState(false);

  async function run() {
    const ids = [a.trim(), b.trim()].filter(Boolean);
    if (ids.length < 2) return;
    setBusy(true);
    setError(null);
    try {
      setResult(await Experience.compare(ids));
    } catch (e) {
      setError(e instanceof Error ? e : new Error(String(e)));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Panel title="Compare experiences">
      <div className="toolbar">
        <input
          className="inp"
          style={{ width: 280 }}
          value={a}
          onChange={(e) => setA(e.target.value)}
          placeholder="experience id A"
          aria-label="experience id A"
        />
        <input
          className="inp"
          style={{ width: 280 }}
          value={b}
          onChange={(e) => setB(e.target.value)}
          placeholder="experience id B"
          aria-label="experience id B"
        />
        <button className="btn small" onClick={run} disabled={busy}>
          {busy ? 'Comparing...' : 'Compare'}
        </button>
      </div>
      {error && <ErrorBox error={error} />}
      {result && <JsonBlock data={result} />}
    </Panel>
  );
}

/* ----------------------------------------------------------- evaluation */

function EvaluationTab({ runId }: { runId: string }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [result, setResult] = useState<Awaited<ReturnType<typeof Eval.run>> | null>(null);

  async function runEval() {
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      setResult(await Eval.run(runId));
    } catch (e) {
      setError(e instanceof Error ? e : new Error(String(e)));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Panel title="Run evaluation">
      <p className="dim">
        There is no list endpoint for evaluations. Past evaluations surface via experience
        lineage refs on the Experience tab.
      </p>
      <div className="toolbar">
        <button className="btn primary" onClick={runEval} disabled={busy}>
          {busy ? 'Evaluating...' : 'Run evaluation'}
        </button>
      </div>
      {error && <ErrorBox error={error} />}
      {result && (
        <div>
          <KV
            rows={[
              ['evaluation_id', <Hash id={result.evaluation_id} />],
              ['verdict', <Badge status={result.verdict}>{result.verdict}</Badge>],
            ]}
          />
          <div style={{ marginTop: 10 }}>
            <div className="faint">metrics</div>
            <JsonBlock data={result.metrics} />
          </div>
        </div>
      )}
    </Panel>
  );
}

/* ------------------------------------------------------------ assurance */

function AssuranceTab() {
  const [evalId, setEvalId] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [result, setResult] = useState<Awaited<ReturnType<typeof Assurance.run>> | null>(null);
  const [full, setFull] = useState<Awaited<ReturnType<typeof Assurance.get>> | null>(null);

  async function runAssurance() {
    const id = evalId.trim();
    if (!id || busy) return;
    setBusy(true);
    setError(null);
    setResult(null);
    setFull(null);
    try {
      const r = await Assurance.run(id);
      setResult(r);
      try {
        setFull(await Assurance.get(r.assurance_id));
      } catch (e) {
        setError(e instanceof Error ? e : new Error(String(e)));
      }
    } catch (e) {
      setError(e instanceof Error ? e : new Error(String(e)));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Panel title="Run assurance">
      <p className="dim">
        Assurance evaluates an evaluation. Enter an evaluation id (for example one from an
        experience lineage), then run assurance against it.
      </p>
      <div className="toolbar">
        <input
          className="inp"
          style={{ width: 320 }}
          value={evalId}
          onChange={(e) => setEvalId(e.target.value)}
          placeholder="evaluation id"
          aria-label="evaluation id"
        />
        <button className="btn primary" onClick={runAssurance} disabled={busy}>
          {busy ? 'Running...' : 'Run assurance'}
        </button>
      </div>
      {error && <ErrorBox error={error} />}
      {result && (
        <div>
          <KV
            rows={[
              ['assurance_id', <Hash id={result.assurance_id} />],
              ['evaluator_verdict', <Badge status={result.evaluator_verdict}>{result.evaluator_verdict}</Badge>],
              ['system_verdict', <Badge status={result.system_verdict}>{result.system_verdict}</Badge>],
              ['false_accepts', val(result.false_accepts)],
              ['false_rejects', val(result.false_rejects)],
            ]}
          />
          {full && (
            <div style={{ marginTop: 10 }}>
              <KV rows={[['timeouts', val(full.timeouts)]]} />
              <div className="faint" style={{ marginTop: 8 }}>
                probes
              </div>
              <JsonBlock data={full.probes} />
            </div>
          )}
        </div>
      )}
    </Panel>
  );
}

/* --------------------------------------------------------------- policy */

function PolicyTab({ runId }: { runId: string }) {
  const runQ = useApi(() => Runs.get(runId), [runId], { watch: watchRun(runId) });
  const pv = runQ.data?.policy_version ?? null;
  const policyName = pv && pv.includes('@') ? pv.split('@')[0] : null;
  const provQ = useApi(() => Policies.provenance(policyName ?? ''), [policyName], {
    skip: !policyName,
  });

  if (runQ.loading && !runQ.data) return <Loading label="Loading run..." />;
  if (runQ.error) return <ErrorBox error={runQ.error} onRetry={runQ.refresh} />;

  return (
    <Panel title="Policy">
      <KV
        rows={[
          [
            'policy_version',
            policyName ? (
              <Link to={`/policies/${policyName}`} className="mono">
                {pv}
              </Link>
            ) : (
              val(pv)
            ),
          ],
          [
            'active_version',
            provQ.loading ? (
              <span className="faint">loading...</span>
            ) : provQ.error ? (
              <Unknown />
            ) : (
              val(provQ.data?.active_version)
            ),
          ],
        ]}
      />
      {provQ.error && (
        <div className="faint" style={{ marginTop: 8 }}>
          Could not load policy provenance: {provQ.error.message}
        </div>
      )}
      {!pv && (
        <p className="dim">This run recorded no policy_version.</p>
      )}
    </Panel>
  );
}

/* --------------------------------------------------------------- replay */

interface RosterEntry {
  id: string;
  role: string;
  parent_id: string | null;
  generation: number | null;
  objective: string | null;
}

function ReplayTab({ runId }: { runId: string }) {
  const { data, error, loading, refresh } = useApi(() => Runs.events(runId, { limit: 10000 }), [runId]);
  const events = data ?? [];
  const n = events.length;
  const [pos, setPos] = useState(0);
  const [playing, setPlaying] = useState(false);
  const safePos = n === 0 ? 0 : Math.min(pos, n - 1);

  useEffect(() => {
    setPos(0);
    setPlaying(false);
  }, [runId]);

  useEffect(() => {
    if (!playing || n === 0) return;
    if (safePos + 1 >= n) {
      setPlaying(false);
      return;
    }
    const t = window.setTimeout(() => setPos(safePos + 1), 600);
    return () => window.clearTimeout(t);
  }, [playing, safePos, n]);

  const roster = useMemo<RosterEntry[]>(() => {
    const map = new Map<string, RosterEntry>();
    for (let i = 0; i <= safePos && i < n; i++) {
      const ev = events[i];
      if (ev.event_type === 'agent.created' && ev.agent_id) {
        const p = (ev.payload ?? {}) as Record<string, unknown>;
        map.set(ev.agent_id, {
          id: ev.agent_id,
          role: typeof p.role === 'string' ? p.role : 'unknown',
          parent_id: typeof p.parent_id === 'string' ? p.parent_id : null,
          generation: typeof p.generation === 'number' ? p.generation : null,
          objective: typeof p.objective === 'string' ? p.objective : null,
        });
      }
    }
    return [...map.values()];
  }, [events, safePos, n]);

  const hist = useMemo(() => {
    const m = new Map<string, number>();
    for (let i = 0; i <= safePos && i < n; i++) {
      const t = events[i].event_type;
      m.set(t, (m.get(t) ?? 0) + 1);
    }
    return [...m.entries()].sort((a, b) => b[1] - a[1]);
  }, [events, safePos, n]);

  const current = n > 0 ? events[safePos] : null;

  if (loading && n === 0) return <Loading label="Loading event stream..." />;
  if (error) return <ErrorBox error={error} onRetry={refresh} />;

  return (
    <div>
      <Panel title="Event-sourced replay">
        <div className="toolbar">
          <button className="btn" onClick={() => setPlaying(!playing)} disabled={n === 0}>
            {playing ? 'Pause' : 'Play'}
          </button>
          <button
            className="btn small"
            disabled={n === 0}
            onClick={() => {
              setPlaying(false);
              setPos(Math.max(0, safePos - 1));
            }}
          >
            Prev
          </button>
          <button
            className="btn small"
            disabled={n === 0}
            onClick={() => {
              setPlaying(false);
              setPos(Math.min(n - 1, safePos + 1));
            }}
          >
            Next
          </button>
          <span className="mono faint">
            event {n === 0 ? 0 : safePos + 1} of {n}
          </span>
          <span className="spacer" />
          <button className="btn small" onClick={() => refresh()}>
            Refresh snapshot
          </button>
        </div>
        <input
          type="range"
          min={0}
          max={Math.max(0, n - 1)}
          value={safePos}
          disabled={n === 0}
          onChange={(e) => {
            setPlaying(false);
            setPos(Number(e.target.value));
          }}
          style={{ width: '100%' }}
          aria-label="event position"
        />
        {current && (
          <div className="dim" style={{ marginTop: 6 }}>
            <Time iso={current.timestamp} /> <span className="mono">{current.event_type}</span>{' '}
            <Hash id={current.agent_id} />
          </div>
        )}
        <p className="faint">
          The roster and histogram are folded from the canonical event stream. Scrubbing
          re-derives state from events; no separate UI recording is used. Play advances one
          event every 600ms.
        </p>
      </Panel>
      {n === 0 ? (
        <Empty title="No events recorded for this run" />
      ) : (
        <div>
          <div className="grid2">
            <Panel title={`Agent roster at position (${roster.length})`} flush>
              {roster.length === 0 ? (
                <div style={{ padding: 14 }}>
                  <Empty title="No agents created up to this position" />
                </div>
              ) : (
                <table className="tbl">
                  <thead>
                    <tr>
                      <th>id</th>
                      <th>role</th>
                      <th>gen</th>
                      <th>parent</th>
                      <th>objective</th>
                    </tr>
                  </thead>
                  <tbody>
                    {roster.map((r) => (
                      <tr key={r.id}>
                        <td>
                          <Hash id={r.id} />
                        </td>
                        <td>{r.role}</td>
                        <td className="mono">{r.generation ?? <Unknown />}</td>
                        <td>
                          <Hash id={r.parent_id} />
                        </td>
                        <td className="faint">{r.objective ? r.objective.slice(0, 80) : <Unknown />}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </Panel>
            <Panel title="Event-type histogram to position" flush>
              {hist.length === 0 ? (
                <div style={{ padding: 14 }}>
                  <Empty title="No events up to this position" />
                </div>
              ) : (
                <table className="tbl">
                  <thead>
                    <tr>
                      <th>event_type</th>
                      <th>count</th>
                    </tr>
                  </thead>
                  <tbody>
                    {hist.map(([t, c]) => (
                      <tr key={t}>
                        <td className="mono">{t}</td>
                        <td className="mono">{c}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </Panel>
          </div>
          {current && (
            <Panel title="Event at position (full JSON)">
              <pre className="json">{JSON.stringify(current, null, 2)}</pre>
            </Panel>
          )}
        </div>
      )}
    </div>
  );
}
