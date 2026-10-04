import { Health, Metrics, Models } from '../lib/api';
import { useApi } from '../lib/query';
import { Badge, Empty, ErrorBox, Loading, Panel, Unknown, val } from '../components/ui';

export default function MetricsScreen() {
  const { data: metrics, error: mError, loading: mLoading, refresh: mRefresh } = useApi(
    () => Metrics.get(),
    [],
    { watch: () => true, pollMs: 15000 },
  );
  const { data: models, error: moError, loading: moLoading, refresh: moRefresh } = useApi(
    () => Models.list(),
    [],
    { watch: () => true, pollMs: 60000 },
  );
  const { data: ready, error: rError, refresh: rRefresh } = useApi(
    () => Health.ready(),
    [],
    { watch: () => true, pollMs: 15000 },
  );

  return (
    <div>
      <div className="topbar">
        <h1>Metrics</h1>
        <span className="dim">counts are backend-reported; the console computes nothing beyond display</span>
      </div>

      <Panel title="Runtime counters">
        {mError && <ErrorBox error={mError} onRetry={mRefresh} />}
        {mLoading && !metrics && <Loading label="Loading metrics..." />}
        {metrics && (
          <div className="stat-row">
            <div className="stat"><div className="v">{metrics.runs}</div><div className="k">runs</div></div>
            <div className="stat"><div className="v">{metrics.agents}</div><div className="k">agents</div></div>
            <div className="stat"><div className="v">{metrics.events}</div><div className="k">events</div></div>
            <div className="stat"><div className="v">{metrics.spawns_approved}</div><div className="k">spawns approved</div></div>
            <div className="stat"><div className="v">{metrics.spawns_denied}</div><div className="k">spawns denied</div></div>
            <div className="stat"><div className="v">{metrics.tool_calls}</div><div className="k">tool calls</div></div>
          </div>
        )}
      </Panel>

      <Panel title="Event chain" right={rError ? null : <button className="btn small" onClick={rRefresh}>Refresh</button>}>
        {rError && <ErrorBox error={rError} onRetry={rRefresh} />}
        {!ready && !rError && <Loading label="Checking chain..." />}
        {ready && (
          <div>
            {ready.ok
              ? <Badge status="COMPLETED">chain ok</Badge>
              : <Badge status="FAILED">bad_event</Badge>}
            {!ready.ok && (
              <div style={{ marginTop: 8 }}>
                <span className="faint">bad_event: </span>
                {ready.bad_event ? <span className="mono">{ready.bad_event}</span> : <Unknown />}
              </div>
            )}
          </div>
        )}
      </Panel>

      <Panel title="Models">
        {moError && <ErrorBox error={moError} onRetry={moRefresh} />}
        {moLoading && !models && <Loading label="Loading models..." />}
        {models && models.length === 0 && (
          <Empty title="No models"><p>The backend reported no configured models.</p></Empty>
        )}
        {models && models.length > 0 && (
          <table className="tbl">
            <thead>
              <tr>
                <th>Provider</th>
                <th>Model</th>
                <th>Local</th>
                <th>Reason</th>
              </tr>
            </thead>
            <tbody>
              {models.map((m, i) => (
                <tr key={`${m.provider}:${m.model}:${i}`}>
                  <td className="mono">{m.provider}</td>
                  <td className="mono">{m.model}</td>
                  <td>{m.local === undefined ? <Unknown /> : <Badge status={m.local ? 'ACTIVE' : 'PENDING'}>{m.local ? 'yes' : 'no'}</Badge>}</td>
                  <td className="dim">{val(m.reason)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>
    </div>
  );
}
