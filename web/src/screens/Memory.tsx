import { useState } from 'react';
import { Memory } from '../lib/api';
import { useApi } from '../lib/query';
import { Badge, ConfirmDialog, Empty, ErrorBox, Hash, JsonBlock, Loading, Panel, Time, Unknown } from '../components/ui';
import { ApiErrorBox } from '../components/blocked';

const TYPES = ['episodic', 'semantic', 'procedural', 'self', 'experience', 'capability'];

export default function MemoryScreen() {
  const [namespace, setNamespace] = useState('global');
  const [scopes, setScopes] = useState('global');
  const [query, setQuery] = useState('');
  const [type, setType] = useState('');

  const { data: hits, error, loading, refresh } = useApi(
    () => Memory.list(namespace.trim() || 'global', {
      scopes: scopes.trim() || undefined,
      q: query.trim() || undefined,
      type: type || undefined,
    }),
    [namespace, scopes, query, type],
    { watch: () => true },
  );

  const [forgetId, setForgetId] = useState<string | null>(null);
  const [forgetBusy, setForgetBusy] = useState(false);
  const [forgetError, setForgetError] = useState<Error | null>(null);

  const [recordOpen, setRecordOpen] = useState(false);
  const [rNamespace, setRNamespace] = useState('global');
  const [rType, setRType] = useState('semantic');
  const [rContent, setRContent] = useState('{\n  \n}');
  const [rScope, setRScope] = useState('run');
  const [rBusy, setRBusy] = useState(false);
  const [rError, setRError] = useState<Error | null>(null);
  const [rResult, setRResult] = useState<string | null>(null);

  async function doForget() {
    if (!forgetId) return;
    setForgetBusy(true);
    setForgetError(null);
    try {
      await Memory.forget(forgetId);
      setForgetId(null);
      refresh();
    } catch (e) {
      setForgetError(e instanceof Error ? e : new Error(String(e)));
    } finally {
      setForgetBusy(false);
    }
  }

  async function doRecord() {
    setRError(null); setRResult(null);
    let content: Record<string, unknown>;
    try {
      content = JSON.parse(rContent) as Record<string, unknown>;
    } catch (e) {
      setRError(new Error(`content is not valid JSON: ${e instanceof Error ? e.message : e}`));
      return;
    }
    setRBusy(true);
    try {
      const r = await Memory.create({
        namespace: rNamespace.trim() || 'global',
        type: rType,
        content,
        scope: rScope.trim() || undefined,
      });
      setRResult(r.id);
      setRecordOpen(false);
      refresh();
    } catch (e) {
      setRError(e instanceof Error ? e : new Error(String(e)));
    } finally {
      setRBusy(false);
    }
  }

  return (
    <div>
      <div className="topbar">
        <h1>Memory</h1>
        <span className="dim">retrieved knowledge; provenance is minted by the backend, never the console</span>
        <span className="spacer" style={{ flex: 1 }} />
        <button className="btn primary" onClick={() => { setRError(null); setRResult(null); setRecordOpen(true); }}>
          Record
        </button>
      </div>

      <Panel title="Query">
        <div className="grid3">
          <div>
            <label className="lbl" htmlFor="mem-ns">namespace</label>
            <input id="mem-ns" className="inp" value={namespace} onChange={(e) => setNamespace(e.target.value)} />
          </div>
          <div>
            <label className="lbl" htmlFor="mem-scopes">scopes</label>
            <input id="mem-scopes" className="inp" value={scopes} onChange={(e) => setScopes(e.target.value)} placeholder="global" />
          </div>
          <div>
            <label className="lbl" htmlFor="mem-type">type</label>
            <select id="mem-type" className="inp" value={type} onChange={(e) => setType(e.target.value)}>
              <option value="">any</option>
              {TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          </div>
        </div>
        <label className="lbl" htmlFor="mem-q">query</label>
        <input id="mem-q" className="inp" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="optional free text" />
      </Panel>

      {error && <ErrorBox error={error} onRetry={refresh} />}
      {rResult && <div className="warn-box">Recorded memory: <span className="mono">{rResult}</span></div>}
      {loading && !hits && <Loading label="Retrieving memories..." />}

      {hits && hits.length === 0 && (
        <Empty title="No memories"><p>No memories matched this query. Backend state only.</p></Empty>
      )}

      {hits && hits.map((h) => (
        <Panel key={h.memory.id} title={h.memory.id.slice(0, 18)} right={<Badge status={h.memory.provenance} />}>
          <div className="grid2">
            <div>
              <div className="faint" style={{ marginBottom: 4 }}>content</div>
              <JsonBlock data={h.memory.content} />
            </div>
            <div>
              <dl className="kv">
                <dt>id</dt><dd><Hash id={h.memory.id} /></dd>
                <dt>namespace</dt><dd className="mono">{h.memory.namespace}</dd>
                <dt>type</dt><dd className="mono">{h.memory.type}</dd>
                <dt>created</dt><dd><Time iso={h.memory.created_at} ago /></dd>
                <dt>score</dt><dd className="mono">{typeof h.score === 'number' ? h.score.toFixed(4) : 'unknown'}</dd>
                <dt>trust</dt><dd className="mono">{typeof h.trust === 'number' ? h.trust.toFixed(3) : 'unknown'}</dd>
                <dt>trust_flags</dt>
                <dd>
                  {h.trust_flags && h.trust_flags.length > 0
                    ? h.trust_flags.map((f) => <Badge key={f} status={f} />)
                    : <Unknown />}
                </dd>
                <dt>why_retrieved</dt><dd className="dim">{h.why_retrieved || 'unknown'}</dd>
              </dl>
              <div className="toolbar" style={{ marginTop: 10 }}>
                <button className="btn small danger" onClick={() => { setForgetError(null); setForgetId(h.memory.id); }}>
                  Forget
                </button>
              </div>
            </div>
          </div>
        </Panel>
      ))}

      {forgetId && (
        <ConfirmDialog
          title="Forget memory"
          confirmLabel={forgetBusy ? 'Forgetting...' : 'Forget'}
          danger
          onCancel={() => setForgetId(null)}
          onConfirm={doForget}
          body={
          <div>
            <p>Forget memory <span className="mono">{forgetId}</span>? This is a real backend deletion.</p>
            {forgetError && <ApiErrorBox error={forgetError} />}
          </div>
          }
        />
      )}

      {recordOpen && (
        <ConfirmDialog
          title="Record memory"
          confirmLabel={rBusy ? 'Recording...' : 'Record'}
          onCancel={() => setRecordOpen(false)}
          onConfirm={doRecord}
          body={
          <div>
            <p className="dim">Direct writes are operator assertions: the backend mints them with USER_ASSERTED provenance. Evaluated knowledge enters only through the learning bridge, never here.</p>
            <label className="lbl" htmlFor="mem-r-ns">namespace</label>
            <input id="mem-r-ns" className="inp" value={rNamespace} onChange={(e) => setRNamespace(e.target.value)} />
            <label className="lbl" htmlFor="mem-r-type">type</label>
            <select id="mem-r-type" className="inp" value={rType} onChange={(e) => setRType(e.target.value)}>
              {TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
            <label className="lbl" htmlFor="mem-r-scope">scope</label>
            <input id="mem-r-scope" className="inp" value={rScope} onChange={(e) => setRScope(e.target.value)} placeholder="run" />
            <label className="lbl" htmlFor="mem-r-content">content (JSON)</label>
            <textarea id="mem-r-content" className="inp mono" rows={6} value={rContent} onChange={(e) => setRContent(e.target.value)} />
            {rError && <div style={{ marginTop: 10 }}><ApiErrorBox error={rError} /></div>}
          </div>
          }
        />
      )}
    </div>
  );
}
