import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { Assurance, Policies } from '../lib/api';
import { useApi } from '../lib/query';
import type { PolicyVersionDetail } from '../lib/types';
import { Badge, ConfirmDialog, Empty, ErrorBox, Hash, JsonBlock, KV, Loading, Panel, Tabs, Time, Unknown, val } from '../components/ui';
import { ApiErrorBox, BlockedReasons } from '../components/blocked';

function field(v: Record<string, unknown> | null | undefined, key: string): unknown {
  return v ? v[key] : undefined;
}

/* ---------------------------------------------------------------- lineage graph */

function LineageGraph({ chain, active, selected, onSelect }: {
  chain: PolicyVersionDetail[];
  active: string | null;
  selected: string | null;
  onSelect: (version: string) => void;
}) {
  const boxW = 150, boxH = 58, gap = 46, pad = 18;
  const n = chain.length;
  const width = pad * 2 + n * boxW + Math.max(0, n - 1) * gap;
  const height = pad * 2 + boxH + 6;
  const cy = pad + boxH / 2;
  const x = (i: number) => pad + i * (boxW + gap);
  const idx = new Map(chain.map((v, i) => [v.version, i] as [string, number]));

  const edges: [number, number][] = [];
  chain.forEach((v, i) => {
    if (i === 0) return;
    const p = v.parent_version != null ? idx.get(v.parent_version) : undefined;
    edges.push([p ?? i - 1, i]);
  });

  return (
    <svg width={width} height={height} style={{ maxWidth: '100%', display: 'block' }} role="img" aria-label="policy version lineage">
      <defs>
        <marker id="lg-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
          <path d="M0,0 L8,4 L0,8" fill="none" stroke="var(--text-faint)" strokeWidth="1.4" />
        </marker>
      </defs>
      {edges.map(([a, b], i) => {
        const x1 = x(a) + boxW, x2 = x(b);
        const d = b === a + 1
          ? `M ${x1} ${cy} L ${x2 - 3} ${cy}`
          : `M ${x1} ${cy} C ${x1 + gap / 2} ${cy}, ${x2 - gap / 2} ${cy}, ${x2 - 3} ${cy}`;
        return <path key={i} d={d} fill="none" stroke="var(--text-faint)" strokeWidth="1.4" markerEnd="url(#lg-arrow)" />;
      })}
      {chain.map((v, i) => {
        const isActive = v.version === active;
        const isSel = v.version === selected;
        return (
          <g key={v.version} onClick={() => onSelect(v.version)} style={{ cursor: 'pointer' }}>
            <rect
              x={x(i)} y={pad} width={boxW} height={boxH} rx={4}
              fill={isSel ? 'var(--panel-2)' : 'var(--bg-raised)'}
              stroke={isActive ? 'var(--accent)' : 'var(--border-strong)'}
              strokeWidth={isActive ? 2 : 1}
            />
            <text x={x(i) + boxW / 2} y={pad + 22} textAnchor="middle" fill="var(--text)" fontSize="13" fontFamily="var(--mono)" fontWeight="600">
              {v.version}
            </text>
            <text x={x(i) + boxW / 2} y={pad + 42} textAnchor="middle" fill="var(--text-dim)" fontSize="11" fontFamily="var(--mono)">
              {v.status}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

/* ---------------------------------------------------------------- constraints */

function Constraints({ constraints }: { constraints: Record<string, unknown> | null | undefined }) {
  if (!constraints || Object.keys(constraints).length === 0) return <Unknown />;
  return (
    <div>
      {Object.entries(constraints).map(([k, v]) => {
        const ok = v === true ? 'PASS' : v === false ? 'FAIL' : null;
        return (
          <div className="check-row" key={k}>
            <span className="mono">{k}</span>
            <span>{ok ? <Badge status={ok} /> : <span className="faint">constraint</span>}</span>
            <span className="mono dim">{typeof v === 'object' ? JSON.stringify(v) : String(v)}</span>
          </div>
        );
      })}
    </div>
  );
}

/* ---------------------------------------------------------------- screen */

export default function PolicyDetailScreen() {
  const { name } = useParams();
  const policyName = name ? decodeURIComponent(name) : '';

  const { data: prov, error, loading, refresh } = useApi(
    () => Policies.provenance(policyName),
    [policyName],
    { watch: () => true },
  );

  const [selected, setSelected] = useState<string | null>(null);
  useEffect(() => { setSelected(null); }, [policyName]);
  const selVersion = selected ?? prov?.active_version ?? null;
  const sel = prov?.chain.find((v) => v.version === selVersion) ?? null;

  const [tab, setTab] = useState('propose');

  // propose
  const [pParams, setPParams] = useState('{\n  \n}');
  const [pReason, setPReason] = useState('');
  const [pResult, setPResult] = useState<Record<string, unknown> | null>(null);
  const [pError, setPError] = useState<Error | null>(null);
  const [pBusy, setPBusy] = useState(false);

  // evaluate
  const [eVersion, setEVersion] = useState('');
  const [eBaseline, setEBaseline] = useState('');
  const [eResult, setEResult] = useState<{ evaluation_id: string; verdict: string; dimensions: Record<string, unknown> } | null>(null);
  const [eError, setEError] = useState<Error | null>(null);
  const [eBusy, setEBusy] = useState(false);

  // assurance
  const [aEvalId, setAEvalId] = useState('');
  const [aResult, setAResult] = useState<{ assurance_id: string; evaluator_verdict: string; system_verdict: string; false_accepts: number; false_rejects: number } | null>(null);
  const [aError, setAError] = useState<Error | null>(null);
  const [aBusy, setABusy] = useState(false);

  // promote
  const [mVersion, setMVersion] = useState('');
  const [mEvalId, setMEvalId] = useState('');
  const [mAssId, setMAssId] = useState('');
  const [mResult, setMResult] = useState<Record<string, unknown> | null>(null);
  const [mError, setMError] = useState<Error | null>(null);
  const [mBusy, setMBusy] = useState(false);

  // rollback request
  const [rbOpen, setRbOpen] = useState(false);
  const [rbReason, setRbReason] = useState('');
  const [rbEvidence, setRbEvidence] = useState('');
  const [rbRecord, setRbRecord] = useState<Record<string, unknown> | null>(null);
  const [rbError, setRbError] = useState<Error | null>(null);
  const [rbBusy, setRbBusy] = useState(false);

  // approve rollback
  const [apResult, setApResult] = useState<Record<string, unknown> | null>(null);
  const [apError, setApError] = useState<Error | null>(null);
  const [apBusy, setApBusy] = useState(false);

  // rollback now
  const [rnConfirm, setRnConfirm] = useState(false);
  const [rnResult, setRnResult] = useState<Record<string, unknown> | null>(null);
  const [rnError, setRnError] = useState<Error | null>(null);
  const [rnBusy, setRnBusy] = useState(false);

  if (!policyName) return <Empty title="No policy name"><p>Missing policy name in route.</p></Empty>;

  async function doPropose() {
    setPError(null); setPResult(null);
    let params: Record<string, unknown>;
    try {
      params = JSON.parse(pParams) as Record<string, unknown>;
    } catch (e) {
      setPError(new Error(`params is not valid JSON: ${e instanceof Error ? e.message : e}`));
      return;
    }
    setPBusy(true);
    try {
      const r = await Policies.propose(policyName, params, pReason);
      setPResult(r as Record<string, unknown>);
      refresh();
    } catch (e) {
      setPError(e instanceof Error ? e : new Error(String(e)));
    } finally {
      setPBusy(false);
    }
  }

  async function doEvaluate() {
    setEError(null); setEResult(null);
    const version = eVersion.trim() || selVersion || '';
    if (!version) { setEError(new Error('no version selected')); return; }
    setEBusy(true);
    try {
      const r = await Policies.evaluate(policyName, version, eBaseline.trim() || undefined);
      setEResult(r);
      setAEvalId(r.evaluation_id);
    } catch (e) {
      setEError(e instanceof Error ? e : new Error(String(e)));
    } finally {
      setEBusy(false);
    }
  }

  async function doAssurance() {
    setAError(null); setAResult(null);
    if (!aEvalId.trim()) { setAError(new Error('evaluation_id is required')); return; }
    setABusy(true);
    try {
      const r = await Assurance.run(aEvalId.trim());
      setAResult(r);
      setMAssId(r.assurance_id);
    } catch (e) {
      setAError(e instanceof Error ? e : new Error(String(e)));
    } finally {
      setABusy(false);
    }
  }

  async function doPromote() {
    setMError(null); setMResult(null);
    if (!mVersion.trim() || !mEvalId.trim() || !mAssId.trim()) {
      setMError(new Error('version, evaluation_id, and assurance_id are all required'));
      return;
    }
    setMBusy(true);
    try {
      const r = await Policies.promote(policyName, mVersion.trim(), mEvalId.trim(), mAssId.trim());
      setMResult(r as Record<string, unknown>);
      refresh();
    } catch (e) {
      setMError(e instanceof Error ? e : new Error(String(e)));
    } finally {
      setMBusy(false);
    }
  }

  async function doRequestRollback() {
    setRbError(null); setRbRecord(null);
    if (!rbReason.trim()) { setRbError(new Error('reason is required')); return; }
    let evidence: Record<string, unknown> | undefined;
    if (rbEvidence.trim()) {
      try {
        evidence = JSON.parse(rbEvidence) as Record<string, unknown>;
      } catch (e) {
        setRbError(new Error(`evidence is not valid JSON: ${e instanceof Error ? e.message : e}`));
        return;
      }
    }
    setRbBusy(true);
    try {
      const r = await Policies.requestRollback(policyName, rbReason.trim(), evidence);
      setRbRecord(r);
      setRbOpen(false);
      setApResult(null);
    } catch (e) {
      setRbError(e instanceof Error ? e : new Error(String(e)));
    } finally {
      setRbBusy(false);
    }
  }

  async function doApproveRollback() {
    const rbId = String(rbRecord?.['id'] ?? rbRecord?.['rollback_id'] ?? '');
    if (!rbId) return;
    setApError(null); setApResult(null);
    setApBusy(true);
    try {
      const r = await Policies.approveRollback(rbId);
      setApResult(r as Record<string, unknown>);
      refresh();
    } catch (e) {
      setApError(e instanceof Error ? e : new Error(String(e)));
    } finally {
      setApBusy(false);
    }
  }

  async function doRollbackNow() {
    setRnError(null); setRnResult(null);
    setRnBusy(true);
    try {
      const r = await Policies.rollback(policyName);
      setRnResult(r as Record<string, unknown>);
      setRnConfirm(false);
      refresh();
    } catch (e) {
      setRnError(e instanceof Error ? e : new Error(String(e)));
    } finally {
      setRnBusy(false);
    }
  }

  const ev = sel?.evaluation ?? null;
  const as = sel?.assurance ?? null;

  return (
    <div>
      <div className="topbar">
        <h1 className="mono">{policyName}</h1>
        <span className="dim">policy lineage and governance</span>
      </div>

      {error && <ErrorBox error={error} onRetry={refresh} />}
      {loading && !prov && <Loading label="Loading provenance..." />}

      {prov && prov.chain.length === 0 && (
        <Empty title="No versions">
          <p>The backend reported an empty provenance chain for this policy.</p>
        </Empty>
      )}

      {prov && prov.chain.length > 0 && (
        <>
          <Panel title={`Lineage (active: ${prov.active_version ?? 'unknown'})`}>
            <LineageGraph chain={prov.chain} active={prov.active_version} selected={selVersion} onSelect={setSelected} />
            <div className="faint" style={{ marginTop: 8 }}>Click a version to inspect it. Edges follow parent_version links.</div>
          </Panel>

          {sel && (
            <Panel title={`Version ${sel.version}`} right={<Badge status={sel.status} />}>
              <KV rows={[
                ['parent_version', val(sel.parent_version)],
                ['generated_by', val(sel.generated_by)],
                ['created_at', <Time key="t" iso={sel.created_at} />],
                ['hypothesis', sel.hypothesis ? sel.hypothesis : <Unknown />],
                ['reason', val(sel.reason)],
              ]} />
              <div style={{ marginTop: 10 }}>
                <div className="faint" style={{ marginBottom: 6 }}>source_experiences</div>
                {sel.source_experiences && sel.source_experiences.length > 0 ? (
                  sel.source_experiences.map((id) => (
                    <div key={id} style={{ marginBottom: 4 }}>
                      <Hash id={id} /> <Link to="/experience">view in Experience</Link>
                    </div>
                  ))
                ) : <Unknown />}
              </div>
              <div style={{ marginTop: 10 }}>
                <div className="faint" style={{ marginBottom: 6 }}>changes</div>
                <JsonBlock data={sel.changes} />
              </div>
              <div style={{ marginTop: 10 }}>
                <div className="faint" style={{ marginBottom: 6 }}>expected_effect</div>
                {sel.expected_effect ? <JsonBlock data={sel.expected_effect} /> : <Unknown />}
              </div>
              <div style={{ marginTop: 10 }}>
                <div className="faint" style={{ marginBottom: 6 }}>constraints (guardrails)</div>
                <Constraints constraints={sel.constraints} />
              </div>
              <div style={{ marginTop: 10 }} className="grid2">
                <div>
                  <div className="faint" style={{ marginBottom: 6 }}>evaluation</div>
                  {ev ? (
                    <div>
                      <Badge status={String(field(ev, 'verdict') ?? 'unknown')}>{String(field(ev, 'verdict') ?? 'unknown')}</Badge>
                      <div style={{ marginTop: 6 }}><JsonBlock data={ev} /></div>
                    </div>
                  ) : <Unknown />}
                </div>
                <div>
                  <div className="faint" style={{ marginBottom: 6 }}>assurance</div>
                  {as ? (
                    <div>
                      <span style={{ marginRight: 6 }}>
                        <Badge status={String(field(as, 'evaluator_verdict') ?? 'unknown')}>evaluator: {String(field(as, 'evaluator_verdict') ?? 'unknown')}</Badge>
                      </span>
                      <Badge status={String(field(as, 'system_verdict') ?? 'unknown')}>system: {String(field(as, 'system_verdict') ?? 'unknown')}</Badge>
                      <div style={{ marginTop: 6 }}><JsonBlock data={as} /></div>
                    </div>
                  ) : <Unknown />}
                </div>
              </div>
            </Panel>
          )}

          <Panel title="Governance actions">
            <Tabs
              tabs={[
                { id: 'propose', label: 'Propose' },
                { id: 'evaluate', label: 'Evaluate' },
                { id: 'assurance', label: 'Assurance' },
                { id: 'promote', label: 'Promote' },
                { id: 'rollback', label: 'Rollback' },
              ]}
              active={tab}
              onChange={setTab}
            />

            {tab === 'propose' && (
              <div>
                <p className="dim">Propose a new candidate version. This sends only <span className="mono">params</span> and <span className="mono">reason</span> to the backend; the hypothesis field is set by the learning engine when it proposes, not by this form.</p>
                <label className="lbl" htmlFor="pd-params">params (JSON)</label>
                <textarea id="pd-params" className="inp mono" rows={8} value={pParams} onChange={(e) => setPParams(e.target.value)} />
                <label className="lbl" htmlFor="pd-reason">reason</label>
                <input id="pd-reason" className="inp" value={pReason} onChange={(e) => setPReason(e.target.value)} />
                <div style={{ marginTop: 10 }}>
                  <button className="btn primary" onClick={doPropose} disabled={pBusy}>{pBusy ? 'Proposing...' : 'Propose version'}</button>
                </div>
                {pError && <div style={{ marginTop: 10 }}><ApiErrorBox error={pError} /></div>}
                {pResult && (
                  <div style={{ marginTop: 10 }}>
                    <KV rows={[
                      ['policy', val(pResult['policy'])],
                      ['version', val(pResult['version'])],
                      ['status', <Badge key="s" status={String(pResult['status'])} />],
                    ]} />
                  </div>
                )}
              </div>
            )}

            {tab === 'evaluate' && (
              <div>
                <p className="dim">Run the policy evaluation pipeline against a candidate version. The verdict comes from the backend evaluator.</p>
                <div className="grid2">
                  <div>
                    <label className="lbl" htmlFor="pd-eval-ver">version</label>
                    <input id="pd-eval-ver" className="inp mono" value={eVersion} placeholder={selVersion ?? 'e.g. v3'} onChange={(e) => setEVersion(e.target.value)} />
                  </div>
                  <div>
                    <label className="lbl" htmlFor="pd-eval-base">baseline (optional)</label>
                    <input id="pd-eval-base" className="inp mono" value={eBaseline} placeholder="e.g. v2" onChange={(e) => setEBaseline(e.target.value)} />
                  </div>
                </div>
                <div style={{ marginTop: 10 }}>
                  <button className="btn primary" onClick={doEvaluate} disabled={eBusy}>{eBusy ? 'Evaluating...' : 'Evaluate'}</button>
                </div>
                {eError && <div style={{ marginTop: 10 }}><ApiErrorBox error={eError} /></div>}
                {eResult && (
                  <div style={{ marginTop: 10 }}>
                    <KV rows={[
                      ['evaluation_id', <Hash key="e" id={eResult.evaluation_id} />],
                      ['verdict', <Badge key="v" status={eResult.verdict} />],
                    ]} />
                    <div className="faint" style={{ margin: '8px 0 4px' }}>dimensions</div>
                    <JsonBlock data={eResult.dimensions} />
                  </div>
                )}
              </div>
            )}

            {tab === 'assurance' && (
              <div>
                <p className="dim">Run adversarial assurance against an existing evaluation. This calls POST /assurance with evaluation_id as a query parameter, per the backend contract.</p>
                <label className="lbl" htmlFor="pd-ass-eval">evaluation_id</label>
                <input id="pd-ass-eval" className="inp mono" value={aEvalId} onChange={(e) => setAEvalId(e.target.value)} placeholder="from the Evaluate step" />
                <div style={{ marginTop: 10 }}>
                  <button className="btn primary" onClick={doAssurance} disabled={aBusy}>{aBusy ? 'Running...' : 'Run assurance'}</button>
                </div>
                {aError && <div style={{ marginTop: 10 }}><ApiErrorBox error={aError} /></div>}
                {aResult && (
                  <div style={{ marginTop: 10 }}>
                    <KV rows={[
                      ['assurance_id', <Hash key="a" id={aResult.assurance_id} />],
                      ['evaluator_verdict', <Badge key="ev" status={aResult.evaluator_verdict} />],
                      ['system_verdict', <Badge key="sv" status={aResult.system_verdict} />],
                      ['false_accepts', val(aResult.false_accepts)],
                      ['false_rejects', val(aResult.false_rejects)],
                    ]} />
                  </div>
                )}
              </div>
            )}

            {tab === 'promote' && (
              <div>
                <p className="dim">Promote a candidate to active. The gate reads the persisted evaluation and assurance rows; caller-supplied verdicts are never accepted. If the gate refuses, the blocked reasons are shown verbatim below.</p>
                <div className="grid2">
                  <div>
                    <label className="lbl" htmlFor="pd-m-ver">version</label>
                    <input id="pd-m-ver" className="inp mono" value={mVersion} placeholder={selVersion ?? 'e.g. v3'} onChange={(e) => setMVersion(e.target.value)} />
                  </div>
                  <div>
                    <label className="lbl" htmlFor="pd-m-eval">evaluation_id</label>
                    <input id="pd-m-eval" className="inp mono" value={mEvalId} onChange={(e) => setMEvalId(e.target.value)} />
                  </div>
                </div>
                <label className="lbl" htmlFor="pd-m-ass">assurance_id</label>
                <input id="pd-m-ass" className="inp mono" value={mAssId} onChange={(e) => setMAssId(e.target.value)} />
                <div style={{ marginTop: 10 }}>
                  <button className="btn primary" onClick={doPromote} disabled={mBusy}>{mBusy ? 'Promoting...' : 'Promote'}</button>
                </div>
                {mError && <div style={{ marginTop: 10 }}><BlockedReasons error={mError} /></div>}
                {mResult && (
                  <div style={{ marginTop: 10 }}>
                    <KV rows={[
                      ['policy', val(mResult['policy'])],
                      ['version', val(mResult['version'])],
                      ['status', <Badge key="s" status={String(mResult['status'])} />],
                    ]} />
                  </div>
                )}
              </div>
            )}

            {tab === 'rollback' && (
              <div>
                <p className="dim">Two paths. Request rollback records operator intent and requires a second approval before anything changes. Rollback now executes immediately.</p>
                <div className="toolbar">
                  <button className="btn" onClick={() => { setRbError(null); setRbRecord(null); setRbOpen(true); }}>Request rollback</button>
                  <button className="btn danger" onClick={() => { setRnError(null); setRnResult(null); setRnConfirm(true); }}>Rollback now</button>
                </div>
                {rbError && <ApiErrorBox error={rbError} />}
                {rbRecord && (
                  <Panel title="Rollback request recorded">
                    <KV rows={[
                      ['rollback_id', <Hash key="r" id={String(rbRecord['id'] ?? rbRecord['rollback_id'] ?? '')} />],
                      ['policy', val(rbRecord['policy'])],
                      ['from_version', val(rbRecord['from_version'])],
                      ['to_version', val(rbRecord['to_version'])],
                      ['status', <Badge key="s" status={String(rbRecord['status'])} />],
                    ]} />
                    <div style={{ marginTop: 10 }}>
                      <button className="btn primary" onClick={doApproveRollback} disabled={apBusy}>
                        {apBusy ? 'Approving...' : 'Approve rollback'}
                      </button>
                    </div>
                    {apError && <div style={{ marginTop: 10 }}><BlockedReasons error={apError} /></div>}
                    {apResult && (
                      <div style={{ marginTop: 10 }}>
                        <KV rows={[
                          ['version', val(apResult['version'])],
                          ['status', <Badge key="s" status={String(apResult['status'])} />],
                        ]} />
                      </div>
                    )}
                  </Panel>
                )}
                {rnError && <div style={{ marginTop: 10 }}><BlockedReasons error={rnError} /></div>}
                {rnResult && (
                  <div style={{ marginTop: 10 }}>
                    <KV rows={[
                      ['policy', val(rnResult['policy'])],
                      ['version', val(rnResult['version'])],
                      ['status', <Badge key="s" status={String(rnResult['status'])} />],
                    ]} />
                  </div>
                )}
              </div>
            )}
          </Panel>
        </>
      )}

      {rbOpen && (
        <ConfirmDialog
          title="Request rollback"
          confirmLabel={rbBusy ? 'Recording...' : 'Record rollback request'}
          onCancel={() => setRbOpen(false)}
          onConfirm={doRequestRollback}
          body={
          <div>
            <p className="dim">This records intent only; nothing changes until the request is approved. Target provenance:</p>
            {prov && prov.chain.length > 0 && (
              <table className="tbl" style={{ marginBottom: 10 }}>
                <thead><tr><th>Version</th><th>Status</th><th>Role</th></tr></thead>
                <tbody>
                  {prov.chain.map((v) => (
                    <tr key={v.version}>
                      <td className="mono">{v.version}</td>
                      <td><Badge status={v.status} /></td>
                      <td>
                        {v.version === prov.active_version ? 'active (rolls back FROM)' : ''}
                        {v.version === prov.chain.find((c) => c.version === prov.active_version)?.parent_version ? 'rollback target (TO)' : ''}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            <label className="lbl" htmlFor="pd-rb-reason">reason (required)</label>
            <textarea id="pd-rb-reason" className="inp" rows={3} value={rbReason} onChange={(e) => setRbReason(e.target.value)} />
            <label className="lbl" htmlFor="pd-rb-ev">evidence (optional JSON)</label>
            <textarea id="pd-rb-ev" className="inp mono" rows={4} value={rbEvidence} onChange={(e) => setRbEvidence(e.target.value)} placeholder="{}" />
            {rbError && <div style={{ marginTop: 10 }}><ApiErrorBox error={rbError} /></div>}
          </div>
          }
        />
      )}

      {rnConfirm && (
        <ConfirmDialog
          title="Rollback now"
          confirmLabel={rnBusy ? 'Rolling back...' : 'Rollback now'}
          danger
          onCancel={() => setRnConfirm(false)}
          onConfirm={doRollbackNow}
          body={
          <div>
            <p>Execute the rollback immediately for policy <span className="mono">{policyName}</span>? The gate may refuse; its reasons will be shown.</p>
            {rnError && <ApiErrorBox error={rnError} />}
          </div>
          }
        />
      )}
    </div>
  );
}
