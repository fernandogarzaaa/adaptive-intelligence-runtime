import { useEffect, useState } from 'react';
import { Capabilities } from '../lib/api';
import { useApi } from '../lib/query';
import { Badge, ConfirmDialog, Empty, ErrorBox, Hash, JsonBlock, KV, Loading, Panel, Time, Unknown, val } from '../components/ui';
import { ApiErrorBox, BlockedReasons } from '../components/blocked';

function CapabilityDetailPanel({ id, onMutated }: { id: string; onMutated: () => void }) {
  const { data: cap, error, loading, refresh } = useApi(() => Capabilities.get(id), [id], { watch: () => true });
  const { data: prov, error: provError } = useApi(() => Capabilities.provenance(id), [id], { watch: () => true });

  const [confirm, setConfirm] = useState<'promote' | 'rollback' | null>(null);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<Error | null>(null);
  const [actionResult, setActionResult] = useState<Record<string, unknown> | null>(null);

  useEffect(() => { setActionError(null); setActionResult(null); setConfirm(null); }, [id]);

  async function doAction() {
    if (!confirm) return;
    setBusy(true);
    setActionError(null);
    setActionResult(null);
    try {
      const r = confirm === 'promote' ? await Capabilities.promote(id) : await Capabilities.rollback(id);
      setActionResult(r as Record<string, unknown>);
      setConfirm(null);
      refresh();
      onMutated();
    } catch (e) {
      setActionError(e instanceof Error ? e : new Error(String(e)));
    } finally {
      setBusy(false);
    }
  }

  if (loading && !cap) return <Loading label="Loading capability..." />;
  if (error) return <ErrorBox error={error} onRetry={refresh} />;
  if (!cap) return <Empty title="Capability not found" />;

  const evRefs = prov?.evidence_references as { created_from?: string | null; validation_runs?: unknown; events?: { type: string; timestamp: string }[] } | undefined;
  const decision = prov?.decision as { validation_status?: string; confidence?: number } | undefined;

  return (
    <div>
      <Panel title={cap.name} right={<Badge status={cap.validation_status} />}>
        <KV rows={[
          ['capability_id', <Hash key="i" id={cap.capability_id} />],
          ['version', val(cap.version)],
          ['confidence', val(cap.confidence)],
          ['description', val(cap.description)],
          ['created_from', cap.created_from ? <Hash key="c" id={cap.created_from} /> : <Unknown />],
          ['created_at', <Time key="t" iso={cap.created_at} />],
        ]} />
        {cap.tools && cap.tools.length > 0 && (
          <div style={{ marginTop: 10 }}>
            <div className="faint" style={{ marginBottom: 4 }}>tools</div>
            <div className="mono">{cap.tools.join(', ')}</div>
          </div>
        )}
        <div style={{ marginTop: 10 }}>
          <div className="faint" style={{ marginBottom: 4 }}>effect</div>
          {cap.effect ? <JsonBlock data={cap.effect} /> : <Unknown />}
        </div>
        <div style={{ marginTop: 10 }}>
          <div className="faint" style={{ marginBottom: 4 }}>validation_runs</div>
          {cap.validation_runs != null ? <JsonBlock data={cap.validation_runs} /> : <Unknown />}
        </div>
        <div className="toolbar" style={{ marginTop: 12 }}>
          <button className="btn primary" onClick={() => { setActionError(null); setConfirm('promote'); }}>Promote</button>
          <button className="btn danger" onClick={() => { setActionError(null); setConfirm('rollback'); }}>Rollback</button>
        </div>
        {actionError && <BlockedReasons error={actionError} />}
        {actionResult && (
          <KV rows={[
            ['capability_id', <Hash key="i" id={String(actionResult['capability_id'] ?? '')} />],
            ['status', <Badge key="s" status={String(actionResult['status'])} />],
          ]} />
        )}
      </Panel>

      <Panel title="Provenance">
        {provError && <ApiErrorBox error={provError} />}
        {!prov && !provError && <Loading label="Loading provenance..." />}
        {prov && (
          <>
            <KV rows={[
              ['validation_status', <Badge key="v" status={decision?.validation_status ?? prov.decision.validation_status} />],
              ['confidence', val(decision?.confidence ?? prov.decision.confidence)],
            ]} />
            <div style={{ marginTop: 10 }}>
              <div className="faint" style={{ marginBottom: 4 }}>evidence: created_from</div>
              {evRefs?.created_from ? <Hash id={evRefs.created_from} /> : <Unknown />}
            </div>
            <div style={{ marginTop: 10 }}>
              <div className="faint" style={{ marginBottom: 4 }}>evidence: validation_runs</div>
              {evRefs?.validation_runs != null ? <JsonBlock data={evRefs.validation_runs} /> : <Unknown />}
            </div>
            <div style={{ marginTop: 10 }}>
              <div className="faint" style={{ marginBottom: 4 }}>evidence: linked events</div>
              {evRefs?.events && evRefs.events.length > 0 ? (
                <table className="tbl">
                  <thead><tr><th>Event</th><th>Time</th></tr></thead>
                  <tbody>
                    {evRefs.events.map((e, i) => (
                      <tr key={i}><td className="mono">{e.type}</td><td><Time iso={e.timestamp} /></td></tr>
                    ))}
                  </tbody>
                </table>
              ) : <Unknown />}
            </div>
          </>
        )}
      </Panel>

      {confirm && (
        <ConfirmDialog
          title={confirm === 'promote' ? 'Promote capability' : 'Rollback capability'}
          confirmLabel={busy ? 'Working...' : confirm === 'promote' ? 'Promote' : 'Rollback'}
          danger={confirm === 'rollback'}
          onCancel={() => setConfirm(null)}
          onConfirm={doAction}
          body={
          <div>
          <p>
            {confirm === 'promote' ? 'Promote' : 'Roll back'} capability <span className="mono">{cap.name}</span>?
            The capability gate may refuse; its reasons will be shown verbatim.
          </p>
          {actionError && <ApiErrorBox error={actionError} />}
          </div>
          }
        />
      )}
    </div>
  );
}

export default function CapabilitiesScreen() {
  const { data: caps, error, loading, refresh } = useApi(() => Capabilities.list(), [], { watch: () => true });
  const [selected, setSelected] = useState<string | null>(null);
  const [proposeOpen, setProposeOpen] = useState(false);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [effect, setEffect] = useState('');
  const [createdFrom, setCreatedFrom] = useState('');
  const [busy, setBusy] = useState(false);
  const [proposeError, setProposeError] = useState<Error | null>(null);
  const [proposeResult, setProposeResult] = useState<string | null>(null);

  async function doPropose() {
    setProposeError(null); setProposeResult(null);
    if (!name.trim() || !description.trim()) {
      setProposeError(new Error('name and description are required'));
      return;
    }
    let effectJson: Record<string, unknown> | undefined;
    if (effect.trim()) {
      try {
        effectJson = JSON.parse(effect) as Record<string, unknown>;
      } catch (e) {
        setProposeError(new Error(`effect is not valid JSON: ${e instanceof Error ? e.message : e}`));
        return;
      }
    }
    setBusy(true);
    try {
      const r = await Capabilities.propose({
        name: name.trim(),
        description: description.trim(),
        effect: effectJson,
        created_from: createdFrom.trim() || undefined,
      });
      setProposeResult(r.capability_id);
      setProposeOpen(false);
      setName(''); setDescription(''); setEffect(''); setCreatedFrom('');
      refresh();
    } catch (e) {
      setProposeError(e instanceof Error ? e : new Error(String(e)));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <div className="topbar">
        <h1>Capabilities</h1>
        <span className="dim">agent capability proposals under validation gates</span>
        <span className="spacer" style={{ flex: 1 }} />
        <button className="btn primary" onClick={() => { setProposeError(null); setProposeResult(null); setProposeOpen(true); }}>
          Propose capability
        </button>
      </div>

      {error && <ErrorBox error={error} onRetry={refresh} />}
      {proposeResult && <div className="warn-box">Capability proposed: <span className="mono">{proposeResult}</span></div>}
      {loading && !caps && <Loading label="Loading capabilities..." />}

      <div className="grid2">
        <div>
          {caps && caps.length === 0 && (
            <Empty title="No capabilities"><p>The backend reported no capabilities.</p></Empty>
          )}
          {caps && caps.length > 0 && (
            <Panel title={`Capabilities (${caps.length})`} flush>
              <table className="tbl">
                <thead>
                  <tr>
                    <th>Name</th>
                    <th>Version</th>
                    <th>Validation</th>
                    <th>Confidence</th>
                  </tr>
                </thead>
                <tbody>
                  {caps.map((c) => (
                    <tr key={c.capability_id} className="clickable" onClick={() => setSelected(c.capability_id)}>
                      <td className="mono">{c.name}</td>
                      <td className="mono">{c.version}</td>
                      <td><Badge status={c.validation_status} /></td>
                      <td className="mono">{typeof c.confidence === 'number' ? c.confidence.toFixed(3) : 'unknown'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Panel>
          )}
        </div>
        <div>
          {selected
            ? <CapabilityDetailPanel key={selected} id={selected} onMutated={refresh} />
            : <Empty title="Select a capability"><p>Click a row to inspect detail and provenance.</p></Empty>}
        </div>
      </div>

      {proposeOpen && (
        <ConfirmDialog
          title="Propose capability"
          confirmLabel={busy ? 'Proposing...' : 'Propose'}
          onCancel={() => setProposeOpen(false)}
          onConfirm={doPropose}
          body={
          <div>
            <label className="lbl" htmlFor="cap-name">name (required)</label>
            <input id="cap-name" className="inp" value={name} onChange={(e) => setName(e.target.value)} />
            <label className="lbl" htmlFor="cap-desc">description (required)</label>
            <textarea id="cap-desc" className="inp" rows={3} value={description} onChange={(e) => setDescription(e.target.value)} />
            <label className="lbl" htmlFor="cap-effect">effect (optional JSON)</label>
            <textarea id="cap-effect" className="inp mono" rows={3} value={effect} onChange={(e) => setEffect(e.target.value)} placeholder='{"type": "spawn_threshold", "value": 0.1}' />
            <label className="lbl" htmlFor="cap-from">created_from (optional capability id)</label>
            <input id="cap-from" className="inp mono" value={createdFrom} onChange={(e) => setCreatedFrom(e.target.value)} />
            {proposeError && <div style={{ marginTop: 10 }}><ApiErrorBox error={proposeError} /></div>}
          </div>
          }
        />
      )}
    </div>
  );
}
