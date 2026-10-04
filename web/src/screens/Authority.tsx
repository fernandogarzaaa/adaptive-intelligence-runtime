import { useState } from 'react';
import { Tools } from '../lib/api';
import { useApi } from '../lib/query';
import type { ToolAuthExplain } from '../lib/types';
import { Badge, Empty, Hash, JsonBlock, KV, Loading, Panel, Time, Unknown, val } from '../components/ui';
import { ApiErrorBox } from '../components/blocked';

const STATES = ['ALL', 'DENIED', 'COMMITTED', 'APPROVAL_PENDING', 'FAILED'];

type CheckVerdict = { verdict: 'PASS' | 'FAIL' | 'NOT REACHED'; detail: string };

function interpretCheck(v: unknown): CheckVerdict {
  if (typeof v === 'object' && v !== null) {
    const o = v as Record<string, unknown>;
    const detail = typeof o['reason'] === 'string' && o['reason'] ? o['reason'] : JSON.stringify(o);
    if (o['ok'] === true) return { verdict: 'PASS', detail };
    if (o['ok'] === false) return { verdict: 'FAIL', detail };
    return { verdict: 'NOT REACHED', detail };
  }
  if (v === true) return { verdict: 'PASS', detail: 'true' };
  if (v === false) return { verdict: 'FAIL', detail: 'false' };
  return { verdict: 'NOT REACHED', detail: v == null ? 'unknown' : String(v) };
}

function AuthorizationPanel({ callId }: { callId: string }) {
  const { data: auth, error, loading, refresh } = useApi(() => Tools.authorization(callId), [callId], { watch: () => true });

  if (loading && !auth) return <Loading label="Loading authorization..." />;
  if (error) return <ApiErrorBox error={error} />;
  if (!auth) return <Empty title="No authorization record" />;

  const a: ToolAuthExplain = auth;
  const checks = a.authorization_checks && typeof a.authorization_checks === 'object'
    ? (a.authorization_checks as Record<string, unknown>)
    : {};
  const entries = Object.entries(checks);

  return (
    <Panel title={`Authorization ${callId.slice(0, 12)}...`} right={<Badge status={a.decision.state} />}>
      <KV rows={[
        ['decision.state', <Badge key="s" status={a.decision.state} />],
        ['approved', a.decision.approved === null || a.decision.approved === undefined ? <Unknown /> : val(a.decision.approved)],
        ['tool_name', <span key="t" className="mono">{a.inputs.tool_name}</span>],
        ['capability', val(a.inputs.capability)],
        ['policy_version', val(a.policy_version)],
        ['verification_status', val(a.verification_state?.verification_status)],
        ['args_hash', <Hash key="h" id={a.evidence_references.args_hash} />],
        ['run_id', <Hash key="r" id={a.evidence_references.run_id} />],
        ['agent_id', <Hash key="a" id={a.evidence_references.agent_id} />],
      ]} />
      <div style={{ marginTop: 10 }}>
        <div className="faint" style={{ marginBottom: 4 }}>
          authorization_checks (resolved in order by the gateway; evaluation stops at the first failing check)
        </div>
        {entries.length === 0 && <Unknown />}
        {entries.map(([name, v]) => {
          const c = interpretCheck(v);
          return (
            <div className="check-row" key={name}>
              <span className="mono">{name}</span>
              <span><Badge status={c.verdict} /></span>
              <span className="dim">{c.detail}</span>
            </div>
          );
        })}
      </div>
      <div style={{ marginTop: 10 }}>
        <div className="faint" style={{ marginBottom: 4 }}>budget_state</div>
        <JsonBlock data={a.budget_state} />
      </div>
      <div style={{ marginTop: 10 }}>
        <div className="faint" style={{ marginBottom: 4 }}>provenance</div>
        {a.evidence_references.provenance ? <JsonBlock data={a.evidence_references.provenance} /> : <Unknown />}
      </div>
      <div style={{ marginTop: 8 }}>
        <button className="btn small" onClick={refresh}>Refresh</button>
      </div>
    </Panel>
  );
}

export default function AuthorityScreen() {
  const [state, setState] = useState('ALL');
  const [runId, setRunId] = useState('');
  const [limit, setLimit] = useState('100');

  const n = Math.max(1, parseInt(limit, 10) || 100);
  const { data: calls, error, loading, refresh } = useApi(
    () => Tools.calls({
      run_id: runId.trim() || undefined,
      state: state === 'ALL' ? undefined : state,
      limit: n,
    }),
    [state, runId, n],
    { watch: () => true },
  );

  const [selected, setSelected] = useState<string | null>(null);

  return (
    <div>
      <div className="topbar">
        <h1>Authority</h1>
        <span className="dim">real tool-call authorization decisions; denials carry their exact cause</span>
      </div>

      <Panel title="Filters">
        <div className="grid3">
          <div>
            <label className="lbl" htmlFor="auth-state">state</label>
            <select id="auth-state" className="inp" value={state} onChange={(e) => { setState(e.target.value); setSelected(null); }}>
              {STATES.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
          </div>
          <div>
            <label className="lbl" htmlFor="auth-run">run_id</label>
            <input id="auth-run" className="inp mono" value={runId} onChange={(e) => { setRunId(e.target.value); setSelected(null); }} placeholder="optional" />
          </div>
          <div>
            <label className="lbl" htmlFor="auth-limit">limit</label>
            <input id="auth-limit" className="inp" type="number" min={1} value={limit} onChange={(e) => setLimit(e.target.value)} />
          </div>
        </div>
      </Panel>

      {error && <ApiErrorBox error={error} onRetry={refresh} />}
      {loading && !calls && <Loading label="Loading tool calls..." />}

      <div className="grid2">
        <div>
          {calls && calls.length === 0 && (
            <Empty title="No tool calls">
              <p>No tool calls match these filters. Backend state only.</p>
            </Empty>
          )}
          {calls && calls.length > 0 && (
            <Panel title={`Tool calls (${calls.length})`} flush>
              <table className="tbl">
                <thead>
                  <tr>
                    <th>Time</th>
                    <th>Tool</th>
                    <th>Agent</th>
                    <th>Capability</th>
                    <th>State</th>
                    <th>Verification</th>
                  </tr>
                </thead>
                <tbody>
                  {calls.map((c) => (
                    <tr key={c.id} className="clickable" onClick={() => setSelected(c.id)}>
                      <td><Time iso={c.created_at} ago /></td>
                      <td className="mono">{c.tool_name}</td>
                      <td><Hash id={c.agent_id} /></td>
                      <td className="mono dim">{c.capability}</td>
                      <td><Badge status={c.state} /></td>
                      <td className="mono dim">{c.verification_status ?? 'unknown'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Panel>
          )}
        </div>
        <div>
          {selected
            ? <AuthorizationPanel key={selected} callId={selected} />
            : <Empty title="Select a tool call"><p>Click a row to inspect the full authorization decision and checks.</p></Empty>}
        </div>
      </div>
    </div>
  );
}
