import { useState } from 'react';
import { Experience } from '../lib/api';
import { useApi } from '../lib/query';
import { ConfirmDialog, Empty, ErrorBox, Hash, JsonBlock, KV, Loading, Panel, Time, Unknown, fmtElapsed, fmtMoney } from '../components/ui';
import { ApiErrorBox, BlockedReasons } from '../components/blocked';

function ExperienceDetail({ id }: { id: string }) {
  const { data: exp, error, loading, refresh } = useApi(() => Experience.get(id), [id], { watch: () => true });
  const { data: lineage, error: lineageError } = useApi(() => Experience.lineage(id), [id], { watch: () => true });

  const [promoteOpen, setPromoteOpen] = useState(false);
  const [namespace, setNamespace] = useState('global');
  const [busy, setBusy] = useState(false);
  const [promoteError, setPromoteError] = useState<Error | null>(null);
  const [memoryId, setMemoryId] = useState<string | null>(null);

  async function doPromote() {
    setBusy(true);
    setPromoteError(null);
    setMemoryId(null);
    try {
      const r = await Experience.promote(id, namespace.trim() || 'global');
      setMemoryId(r.memory_id);
      setPromoteOpen(false);
    } catch (e) {
      setPromoteError(e instanceof Error ? e : new Error(String(e)));
    } finally {
      setBusy(false);
    }
  }

  if (loading && !exp) return <Loading label="Loading experience..." />;
  if (error) return <ApiErrorBox error={error} />;
  if (!exp) return <Empty title="Experience not found" />;

  const derived = lineage?.evidence_references.derived_memories ?? [];

  return (
    <div>
      <Panel title={`Experience ${id.slice(0, 12)}...`}>
        <KV rows={[
          ['id', <Hash key="i" id={exp.id} />],
          ['run_id', <Hash key="r" id={exp.run_id} />],
          ['goal', exp.goal],
          ['cost', fmtMoney(exp.cost)],
          ['latency', fmtElapsed(exp.latency_ms)],
          ['runtime_version', exp.runtime_version],
          ['created_at', <Time key="c" iso={exp.created_at} />],
        ]} />
        <div style={{ marginTop: 10 }}>
          <div className="faint" style={{ marginBottom: 4 }}>dimensions</div>
          {exp.dimensions ? <JsonBlock data={exp.dimensions} /> : <Unknown />}
        </div>
        <div style={{ marginTop: 10 }}>
          <div className="faint" style={{ marginBottom: 4 }}>outcomes</div>
          {exp.outcomes ? <JsonBlock data={exp.outcomes} /> : <Unknown />}
        </div>
        <div style={{ marginTop: 10 }}>
          <div className="faint" style={{ marginBottom: 4 }}>verification</div>
          {exp.verification ? <JsonBlock data={exp.verification} /> : <Unknown />}
        </div>
        <div className="toolbar" style={{ marginTop: 12 }}>
          <button className="btn primary" onClick={() => { setPromoteError(null); setMemoryId(null); setPromoteOpen(true); }}>
            Promote to knowledge
          </button>
        </div>
        {promoteError && <BlockedReasons error={promoteError} />}
        {memoryId && <div className="warn-box">Promoted to memory: <span className="mono">{memoryId}</span></div>}
      </Panel>

      <Panel title="Lineage">
        {lineageError && <ApiErrorBox error={lineageError} />}
        {!lineage && !lineageError && <Loading label="Loading lineage..." />}
        {lineage && (
          <>
            <KV rows={[
              ['goal', lineage.decision.goal],
              ['cost', fmtMoney(lineage.decision.cost)],
              ['latency', fmtElapsed(lineage.decision.latency_ms)],
              ['verification_state', lineage.verification_state ? <JsonBlock data={lineage.verification_state} /> : <Unknown />],
            ]} />
            <div style={{ marginTop: 10 }}>
              <div className="faint" style={{ marginBottom: 4 }}>derived memories</div>
              {derived.length > 0 ? (
                <table className="tbl">
                  <thead><tr><th>Memory ID</th><th>Type</th><th>Created</th></tr></thead>
                  <tbody>
                    {derived.map((m) => (
                      <tr key={m.id}>
                        <td><Hash id={m.id} /></td>
                        <td className="mono">{m.type}</td>
                        <td><Time iso={m.created_at} ago /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : <Unknown />}
            </div>
          </>
        )}
      </Panel>

      {promoteOpen && (
        <ConfirmDialog
          title="Promote to knowledge"
          confirmLabel={busy ? 'Promoting...' : 'Promote'}
          onCancel={() => setPromoteOpen(false)}
          onConfirm={doPromote}
          body={
          <div>
            <p className="dim">Promote this experience into evaluated knowledge via the learning bridge. The gate may refuse; its reasons will be shown.</p>
            <label className="lbl" htmlFor="exp-ns">namespace</label>
            <input id="exp-ns" className="inp" value={namespace} onChange={(e) => setNamespace(e.target.value)} />
            {promoteError && <div style={{ marginTop: 10 }}><ApiErrorBox error={promoteError} /></div>}
          </div>
          }
        />
      )}

      <div style={{ marginTop: 8 }}>
        <button className="btn small" onClick={refresh}>Refresh</button>
      </div>
    </div>
  );
}

export default function ExperienceScreen() {
  const { data: exps, error, loading, refresh } = useApi(() => Experience.list(undefined, 50), [], { watch: () => true });
  const [selected, setSelected] = useState<string | null>(null);

  const [cmpA, setCmpA] = useState('');
  const [cmpB, setCmpB] = useState('');
  const [cmpResult, setCmpResult] = useState<Record<string, unknown> | null>(null);
  const [cmpError, setCmpError] = useState<Error | null>(null);
  const [cmpBusy, setCmpBusy] = useState(false);

  async function doCompare() {
    setCmpError(null); setCmpResult(null);
    if (!cmpA.trim() || !cmpB.trim()) {
      setCmpError(new Error('two experience ids are required'));
      return;
    }
    setCmpBusy(true);
    try {
      const r = await Experience.compare([cmpA.trim(), cmpB.trim()]);
      setCmpResult(r);
    } catch (e) {
      setCmpError(e instanceof Error ? e : new Error(String(e)));
    } finally {
      setCmpBusy(false);
    }
  }

  return (
    <div>
      <div className="topbar">
        <h1>Experience</h1>
        <span className="dim">recorded run outcomes; promotion to knowledge goes through the learning bridge</span>
      </div>

      {error && <ErrorBox error={error} onRetry={refresh} />}

      <div className="grid2">
        <div>
          {loading && !exps && <Loading label="Loading experiences..." />}
          {exps && exps.length === 0 && (
            <Empty title="No experiences"><p>The backend reported no experiences yet.</p></Empty>
          )}
          {exps && exps.length > 0 && (
            <Panel title={`Experiences (${exps.length})`} flush>
              <table className="tbl">
                <thead>
                  <tr>
                    <th>ID</th>
                    <th>Run</th>
                    <th>Goal</th>
                    <th>Cost</th>
                    <th>Latency</th>
                    <th>Created</th>
                  </tr>
                </thead>
                <tbody>
                  {exps.map((e) => (
                    <tr key={e.id} className="clickable" onClick={() => setSelected(e.id)}>
                      <td><Hash id={e.id} /></td>
                      <td><Hash id={e.run_id} /></td>
                      <td style={{ maxWidth: 280 }}>{e.goal}</td>
                      <td className="mono">{fmtMoney(e.cost)}</td>
                      <td className="mono">{fmtElapsed(e.latency_ms)}</td>
                      <td><Time iso={e.created_at} ago /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Panel>
          )}
        </div>
        <div>
          {selected
            ? <ExperienceDetail key={selected} id={selected} />
            : <Empty title="Select an experience"><p>Click a row to inspect detail and lineage.</p></Empty>}
        </div>
      </div>

      <Panel title="Compare">
        <div className="grid2">
          <div>
            <label className="lbl" htmlFor="exp-cmp-a">experience id A</label>
            <input id="exp-cmp-a" className="inp mono" value={cmpA} onChange={(e) => setCmpA(e.target.value)} />
          </div>
          <div>
            <label className="lbl" htmlFor="exp-cmp-b">experience id B</label>
            <input id="exp-cmp-b" className="inp mono" value={cmpB} onChange={(e) => setCmpB(e.target.value)} />
          </div>
        </div>
        <div style={{ marginTop: 10 }}>
          <button className="btn" onClick={doCompare} disabled={cmpBusy}>{cmpBusy ? 'Comparing...' : 'Compare'}</button>
        </div>
        {cmpError && <div style={{ marginTop: 10 }}><ApiErrorBox error={cmpError} /></div>}
        {cmpResult && <div style={{ marginTop: 10 }}><JsonBlock data={cmpResult} /></div>}
      </Panel>
    </div>
  );
}
