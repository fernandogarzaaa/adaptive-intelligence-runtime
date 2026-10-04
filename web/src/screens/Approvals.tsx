import { useEffect, useState } from 'react';
import { Approvals } from '../lib/api';
import { useApi } from '../lib/query';
import type { Approval } from '../lib/types';
import { Badge, ConfirmDialog, Empty, ErrorBox, Hash, JsonBlock, KV, Loading, Panel, Time, Unknown, val } from '../components/ui';
import { ApiErrorBox, BlockedReasons } from '../components/blocked';

function ApprovalDetail({ id, onDecided }: { id: string; onDecided: () => void }) {
  const { data: ap, error, loading, refresh } = useApi(() => Approvals.get(id), [id], { watch: () => true, pollMs: 15000 });
  const [decidedBy, setDecidedBy] = useState('operator');
  const [confirm, setConfirm] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);
  const [decideError, setDecideError] = useState<Error | null>(null);

  useEffect(() => { setDecideError(null); setConfirm(null); }, [id]);

  async function doDecide() {
    if (confirm === null) return;
    setBusy(true);
    setDecideError(null);
    try {
      await Approvals.decide(id, confirm, decidedBy.trim() || 'operator');
      setConfirm(null);
      refresh();
      onDecided();
    } catch (e) {
      setDecideError(e instanceof Error ? e : new Error(String(e)));
    } finally {
      setBusy(false);
    }
  }

  if (loading && !ap) return <Loading label="Loading approval..." />;
  if (error) return <ApiErrorBox error={error} />;
  if (!ap) return <Empty title="Approval not found" />;

  const a: Approval = ap;
  const decided = a.status !== 'PENDING';

  return (
    <Panel title={`Approval ${id.slice(0, 12)}...`} right={<Badge status={a.status} />}>
      <KV rows={[
        ['id', <Hash key="i" id={a.id} />],
        ['kind', <span key="k" className="mono">{a.kind}</span>],
        ['subject', val(a.subject)],
        ['requested_by', val(a.requested_by)],
        ['created_at', <Time key="c" iso={a.created_at} />],
        ['decided_by', a.decided_by ? val(a.decided_by) : <Unknown />],
        ['decided_at', a.decided_at ? <Time key="d" iso={a.decided_at} /> : <Unknown />],
      ]} />
      <div style={{ marginTop: 10 }}>
        <div className="faint" style={{ marginBottom: 4 }}>payload</div>
        <JsonBlock data={a.payload} />
      </div>
      {!decided && (
        <div style={{ marginTop: 12 }}>
          <label className="lbl" htmlFor="ap-decider">decided_by</label>
          <input id="ap-decider" className="inp" value={decidedBy} onChange={(e) => setDecidedBy(e.target.value)} />
          <div className="toolbar" style={{ marginTop: 10 }}>
            <button className="btn primary" onClick={() => { setDecideError(null); setConfirm(true); }}>Approve</button>
            <button className="btn danger" onClick={() => { setDecideError(null); setConfirm(false); }}>Deny</button>
          </div>
          {decideError && <BlockedReasons error={decideError} />}
        </div>
      )}
      {confirm !== null && (
        <ConfirmDialog
          title={confirm ? 'Approve request' : 'Deny request'}
          confirmLabel={busy ? 'Deciding...' : confirm ? 'Approve' : 'Deny'}
          danger={!confirm}
          onCancel={() => setConfirm(null)}
          onConfirm={doDecide}
          body={
          <div>
            <p>
              {confirm ? 'Approve' : 'Deny'} this {a.kind} request (subject: <span className="mono">{a.subject}</span>) as{' '}
              <span className="mono">{decidedBy.trim() || 'operator'}</span>? The backend records the decision; a duplicate decision is a 409, not a second execution.
            </p>
            {decideError && <ApiErrorBox error={decideError} />}
          </div>
          }
        />
      )}
    </Panel>
  );
}

export default function ApprovalsScreen() {
  const { data: approvals, error, loading, refresh } = useApi(
    () => Approvals.pending(),
    [],
    { watch: () => true, pollMs: 15000 },
  );
  const [selected, setSelected] = useState<string | null>(null);

  return (
    <div>
      <div className="topbar">
        <h1>Approvals</h1>
        <span className="dim">operator decisions; polled every 15s and refreshed on live events</span>
      </div>

      {error && <ErrorBox error={error} onRetry={refresh} />}

      <div className="grid2">
        <div>
          {loading && !approvals && <Loading label="Loading approvals..." />}
          {approvals && approvals.length === 0 && (
            <Empty title="No pending approvals"><p>Nothing is waiting for an operator decision.</p></Empty>
          )}
          {approvals && approvals.length > 0 && (
            <Panel title={`Pending approvals (${approvals.length})`} flush>
              <table className="tbl">
                <thead>
                  <tr>
                    <th>ID</th>
                    <th>Kind</th>
                    <th>Subject</th>
                    <th>Requested by</th>
                    <th>Created</th>
                  </tr>
                </thead>
                <tbody>
                  {approvals.map((a) => (
                    <tr key={a.id} className="clickable" onClick={() => setSelected(a.id)}>
                      <td><Hash id={a.id} /></td>
                      <td className="mono">{a.kind}</td>
                      <td className="mono dim" style={{ maxWidth: 260 }}>{a.subject}</td>
                      <td>{a.requested_by}</td>
                      <td><Time iso={a.created_at} ago /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Panel>
          )}
        </div>
        <div>
          {selected
            ? <ApprovalDetail key={selected} id={selected} onDecided={refresh} />
            : <Empty title="Select an approval"><p>Click a row to review the payload and decide.</p></Empty>}
        </div>
      </div>
    </div>
  );
}
