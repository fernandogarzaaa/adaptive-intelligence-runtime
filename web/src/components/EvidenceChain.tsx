/* Evidence Chain: clickable provenance chain for a run.
   RUN -> EXPERIENCE -> EVALUATION -> ASSURANCE -> POLICY CANDIDATE -> PROMOTION.
   Every node is real backend data. Missing links render as dim
   "not yet recorded" nodes; nothing is fabricated and nothing crashes. */

import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { Assurance, Eval, Experience, Policies } from '../lib/api';
import { useApi } from '../lib/query';
import {
  Badge,
  Empty,
  ErrorBox,
  Hash,
  KV,
  Loading,
  Panel,
  Time,
  Unknown,
  fmtMoney,
  val,
} from './ui';
import type { PolicyProvenance } from '../lib/types';

/** Defensive extraction of ref ids. The backend stores string id lists;
   tolerate {id, ...} objects and render Unknown on anything else. */
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

function Connector() {
  return (
    <div
      aria-hidden
      style={{
        width: 2,
        height: 14,
        background: 'var(--border-strong)',
        margin: '2px 0 2px 28px',
      }}
    />
  );
}

function Missing({ why }: { why: string }) {
  return (
    <span className="faint">
      not yet recorded <span className="faint">({why})</span>
    </span>
  );
}

function EvalRefNode({ id }: { id: string }) {
  const { data, error, loading } = useApi(() => Eval.get(id), [id]);
  return (
    <div className="check-row">
      <span>
        <Hash id={id} />
      </span>
      <span>
        {loading ? (
          <span className="faint">resolving...</span>
        ) : error || !data ? (
          <Unknown />
        ) : (
          <Badge status={data.verdict}>{data.verdict}</Badge>
        )}
      </span>
      <span className="faint">
        {loading ? '' : error ? 'lookup failed' : `evaluator ${data?.evaluator ?? 'unknown'}`}
      </span>
    </div>
  );
}

function AssuranceRefNode({ id }: { id: string }) {
  const { data, error, loading } = useApi(() => Assurance.get(id), [id]);
  return (
    <div className="check-row">
      <span>
        <Hash id={id} />
      </span>
      <span>
        {loading ? (
          <span className="faint">resolving...</span>
        ) : error || !data ? (
          <Unknown />
        ) : (
          <Badge status={data.evaluator_verdict}>{data.evaluator_verdict}</Badge>
        )}
      </span>
      <span>
        {loading ? (
          ''
        ) : error || !data ? (
          <Unknown />
        ) : (
          <span>
            <span className="faint">system </span>
            <Badge status={data.system_verdict}>{data.system_verdict}</Badge>
          </span>
        )}
      </span>
    </div>
  );
}

interface PolicyCandidate {
  policy: string;
  version: string;
  status: string;
  active: boolean;
}

export default function EvidenceChain({ runId }: { runId: string }) {
  const expsQ = useApi(() => Experience.list(runId, 50), [runId]);
  const exps = expsQ.data ?? [];
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [policies, setPolicies] = useState<{ name: string; prov: PolicyProvenance | null }[]>([]);

  useEffect(() => {
    setSelectedId(null);
  }, [runId]);

  useEffect(() => {
    if (!selectedId && exps.length > 0) setSelectedId(exps[0].id);
  }, [exps, selectedId]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const list = await Policies.list();
        const rows = await Promise.all(
          list.map(async (p) => {
            try {
              return { name: p.name, prov: await Policies.provenance(p.name) };
            } catch {
              return { name: p.name, prov: null as PolicyProvenance | null };
            }
          }),
        );
        if (!cancelled) setPolicies(rows);
      } catch {
        if (!cancelled) setPolicies([]);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const lineageQ = useApi(() => Experience.lineage(selectedId ?? ''), [selectedId], {
    skip: !selectedId,
  });
  const lineage = lineageQ.data ?? null;
  const refs = lineage?.evidence_references;
  const evalIds = refIds(refs?.evaluation_refs);
  const assuranceIds = refIds(refs?.assurance_refs);
  const derived = Array.isArray(refs?.derived_memories) ? refs.derived_memories : [];
  const selected = exps.find((e) => e.id === selectedId) ?? null;

  const candidates: PolicyCandidate[] = [];
  if (selectedId) {
    for (const { name, prov } of policies) {
      if (!prov) continue;
      for (const v of prov.chain ?? []) {
        const sources = v.source_experiences ?? [];
        if (Array.isArray(sources) && sources.includes(selectedId)) {
          candidates.push({
            policy: name,
            version: v.version,
            status: v.status,
            active: prov.active_version === v.version,
          });
        }
      }
    }
  }

  return (
    <Panel
      title="Evidence chain"
      right={<span className="faint">run to promotion provenance</span>}
    >
      {expsQ.loading && exps.length === 0 && <Loading label="Loading experiences..." />}
      {expsQ.error && <ErrorBox error={expsQ.error} onRetry={expsQ.refresh} />}
      {!expsQ.loading && exps.length === 0 && !expsQ.error && (
        <Empty title="No experiences for this run" >
          <p>The chain starts when the run records experience.</p>
        </Empty>
      )}

      {exps.length > 0 && (
        <div>
          <Panel title="1. Run">
            <Link to={`/runs/${runId}`} className="mono">
              <Hash id={runId} />
            </Link>
          </Panel>
          <Connector />

          <Panel title="2. Experience">
            {exps.length > 1 && (
              <div style={{ marginBottom: 8 }}>
                <label className="lbl" htmlFor="evc-exp-select">
                  Experience ({exps.length} recorded, most recent first)
                </label>
                <select
                  id="evc-exp-select"
                  className="inp"
                  value={selectedId ?? ''}
                  onChange={(e) => setSelectedId(e.target.value)}
                >
                  {exps.map((x) => (
                    <option key={x.id} value={x.id}>
                      {x.id.slice(0, 12)} - {x.goal.slice(0, 48)}
                    </option>
                  ))}
                </select>
              </div>
            )}
            {selected ? (
              <div>
                <KV
                  rows={[
                    ['id', <Hash id={selected.id} />],
                    [
                      'link',
                      <Link to="/experience" className="mono">
                        /experience
                      </Link>,
                    ],
                    ['goal', val(selected.goal)],
                    ['cost', <span className="mono">{fmtMoney(selected.cost)}</span>],
                    ['created', <Time iso={selected.created_at} />],
                  ]}
                />
              </div>
            ) : (
              <Missing why="select an experience" />
            )}
          </Panel>
          <Connector />

          <Panel title="3. Evaluation">
            {lineageQ.loading && <Loading label="Loading lineage..." />}
            {lineageQ.error && <ErrorBox error={lineageQ.error} onRetry={lineageQ.refresh} />}
            {!lineageQ.loading && !lineageQ.error && (
              <div>
                {evalIds.length === 0 ? (
                  <Missing why="no evaluation_refs on this experience" />
                ) : (
                  evalIds.map((id) => <EvalRefNode key={id} id={id} />)
                )}
              </div>
            )}
          </Panel>
          <Connector />

          <Panel title="4. Assurance">
            {lineageQ.loading && <Loading label="Loading lineage..." />}
            {lineageQ.error && <ErrorBox error={lineageQ.error} onRetry={lineageQ.refresh} />}
            {!lineageQ.loading && !lineageQ.error && (
              <div>
                {assuranceIds.length === 0 ? (
                  <Missing why="no assurance_refs on this experience" />
                ) : (
                  assuranceIds.map((id) => <AssuranceRefNode key={id} id={id} />)
                )}
              </div>
            )}
          </Panel>
          <Connector />

          <Panel title="5. Policy candidate">
            {candidates.length === 0 ? (
              <Missing why="no policy version cites this experience as a source" />
            ) : (
              candidates.map((c) => (
                <div className="check-row" key={`${c.policy}@${c.version}`}>
                  <span>
                    <Link to={`/policies/${c.policy}`} className="mono">
                      {c.policy}
                    </Link>
                  </span>
                  <span className="mono">{c.version}</span>
                  <span>
                    <Badge status={c.status}>{c.status}</Badge>
                    {c.active && <span className="faint"> active</span>}
                  </span>
                </div>
              ))
            )}
          </Panel>
          <Connector />

          <Panel title="6. Promotion">
            {lineageQ.loading && <Loading label="Loading lineage..." />}
            {lineageQ.error && <ErrorBox error={lineageQ.error} onRetry={lineageQ.refresh} />}
            {!lineageQ.loading && !lineageQ.error && (
              <div>
                {derived.length === 0 ? (
                  <Missing why="no derived memories from this experience" />
                ) : (
                  <table className="tbl">
                    <thead>
                      <tr>
                        <th>memory</th>
                        <th>type</th>
                        <th>created</th>
                      </tr>
                    </thead>
                    <tbody>
                      {derived.map((m: { id: string; type: string; created_at: string }) => (
                        <tr key={m.id}>
                          <td>
                            <Link to="/memory" className="mono">
                              <Hash id={m.id} />
                            </Link>
                          </td>
                          <td className="mono">{m.type ?? <Unknown />}</td>
                          <td>
                            <Time iso={m.created_at} />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            )}
          </Panel>
        </div>
      )}
    </Panel>
  );
}
