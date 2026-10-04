import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Runs } from '../lib/api';
import { useApi } from '../lib/query';
import { Badge, ConfirmDialog, Empty, ErrorBox, Hash, Loading, Panel, Time } from '../components/ui';
import { ApiErrorBox } from '../components/blocked';

const TERMINAL = ['COMPLETED', 'FAILED', 'CANCELLED'];
const STRATEGIES = [
  'direct',
  'single_agent',
  'parallel_agents',
  'hierarchical_agents',
  'debate',
  'research_then_execute',
  'execute_then_verify',
  'simulation_first',
  'adaptive_spawn',
];

export default function RunsScreen() {
  const navigate = useNavigate();
  const { data: runs, error, loading, refresh } = useApi(() => Runs.list(50), [], { watch: () => true });

  const [newOpen, setNewOpen] = useState(false);
  const [goal, setGoal] = useState('');
  const [strategy, setStrategy] = useState('direct');
  const [agentBudget, setAgentBudget] = useState('');
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<Error | null>(null);

  const [cancelId, setCancelId] = useState<string | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const [cancelError, setCancelError] = useState<Error | null>(null);

  async function doCreate() {
    if (!goal.trim()) return;
    setCreating(true);
    setCreateError(null);
    try {
      const budget = agentBudget.trim() === '' ? undefined : Number(agentBudget);
      const res = await Runs.create({
        goal: goal.trim(),
        strategy,
        agent_budget: budget,
      });
      setNewOpen(false);
      setGoal('');
      setAgentBudget('');
      navigate(`/runs/${res.run_id}/live`);
    } catch (e) {
      setCreateError(e instanceof Error ? e : new Error(String(e)));
    } finally {
      setCreating(false);
    }
  }

  async function doCancel() {
    if (!cancelId) return;
    setCancelling(true);
    setCancelError(null);
    try {
      await Runs.cancel(cancelId);
      setCancelId(null);
      refresh();
    } catch (e) {
      setCancelError(e instanceof Error ? e : new Error(String(e)));
    } finally {
      setCancelling(false);
    }
  }

  const isActive = (status: string) => !TERMINAL.includes(status);

  return (
    <div>
      <div className="topbar">
        <h1>Runs</h1>
        <span className="dim">every run the backend reports; refresh follows live events</span>
        <span className="spacer" style={{ flex: 1 }} />
        <button className="btn primary" onClick={() => { setCreateError(null); setNewOpen(true); }}>
          New run
        </button>
      </div>

      {error && <ErrorBox error={error} onRetry={refresh} />}
      {cancelError && <ApiErrorBox error={cancelError} onRetry={refresh} />}
      {loading && !runs && <Loading label="Loading runs..." />}

      {runs && runs.length === 0 && (
        <Empty title="No runs yet">
          <p>Start one with the New run button. State comes from the backend only.</p>
        </Empty>
      )}

      {runs && runs.length > 0 && (
        <Panel title={`Runs (${runs.length})`} flush>
          <table className="tbl">
            <thead>
              <tr>
                <th>ID</th>
                <th>Goal</th>
                <th>Status</th>
                <th>Strategy</th>
                <th>Created</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {runs.map((r) => (
                <tr key={r.id}>
                  <td>
                    <Link to={`/runs/${r.id}/live`} title={r.id}>
                      <Hash id={r.id} />
                    </Link>
                  </td>
                  <td style={{ maxWidth: 420 }}>{r.goal}</td>
                  <td><Badge status={r.status} /></td>
                  <td className="mono">{r.strategy ?? 'unknown'}</td>
                  <td><Time iso={r.created_at} ago /></td>
                  <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                    {isActive(r.status) && (
                      <button className="btn small danger" onClick={() => { setCancelError(null); setCancelId(r.id); }}>
                        Cancel
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      )}

      {newOpen && (
        <ConfirmDialog
          title="New run"
          confirmLabel={creating ? 'Creating...' : 'Create and open live view'}
          onCancel={() => setNewOpen(false)}
          onConfirm={doCreate}
          body={
          <div>
            <label className="lbl" htmlFor="run-goal">Goal (required)</label>
            <textarea
              id="run-goal"
              className="inp"
              rows={3}
              value={goal}
              onChange={(e) => setGoal(e.target.value)}
              placeholder="What should this run accomplish?"
            />
            <label className="lbl" htmlFor="run-strategy">Strategy</label>
            <select id="run-strategy" className="inp" value={strategy} onChange={(e) => setStrategy(e.target.value)}>
              {STRATEGIES.map((s) => (
                <option key={s} value={s}>{s}</option>
              ))}
            </select>
            <label className="lbl" htmlFor="run-budget">Agent budget (optional)</label>
            <input
              id="run-budget"
              className="inp"
              type="number"
              min={0}
              value={agentBudget}
              onChange={(e) => setAgentBudget(e.target.value)}
              placeholder="leave empty for backend default"
            />
            {createError && <div style={{ marginTop: 10 }}><ApiErrorBox error={createError} /></div>}
          </div>
          }
        />
      )}

      {cancelId && (
        <ConfirmDialog
          title="Cancel run"
          confirmLabel={cancelling ? 'Cancelling...' : 'Cancel run'}
          danger
          onCancel={() => setCancelId(null)}
          onConfirm={doCancel}
          body={
          <div>
            <p>Cancel run <span className="mono">{cancelId}</span>? Agents stop and the run moves to a terminal state. This is a real backend mutation.</p>
            {cancelError && <ApiErrorBox error={cancelError} />}
          </div>
          }
        />
      )}
    </div>
  );
}
