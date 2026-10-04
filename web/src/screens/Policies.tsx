import { Link } from 'react-router-dom';
import { Policies } from '../lib/api';
import { useApi } from '../lib/query';
import { Badge, Empty, ErrorBox, Loading, Panel } from '../components/ui';

export default function PoliciesScreen() {
  const { data: policies, error, loading, refresh } = useApi(() => Policies.list(), [], { watch: () => true });

  return (
    <div>
      <div className="topbar">
        <h1>Policies</h1>
        <span className="dim">learning-managed policy versions; the console never edits policy text directly</span>
      </div>

      {error && <ErrorBox error={error} onRetry={refresh} />}
      {loading && !policies && <Loading label="Loading policies..." />}

      {policies && policies.length === 0 && (
        <Empty title="No policies">
          <p>The backend reported no policies. Versions are created by the learning engine, not the console.</p>
        </Empty>
      )}

      {policies && policies.length > 0 && (
        <Panel title={`Policies (${policies.length})`} flush>
          <table className="tbl">
            <thead>
              <tr>
                <th>Name</th>
                <th>Current version</th>
                <th>Active effects</th>
              </tr>
            </thead>
            <tbody>
              {policies.map((p) => (
                <tr key={p.name}>
                  <td>
                    <Link to={`/policies/${encodeURIComponent(p.name)}`} className="mono">
                      {p.name}
                    </Link>
                  </td>
                  <td><Badge status="ACTIVE">{p.current_version}</Badge></td>
                  <td className="mono">{p.effects.length}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      )}
    </div>
  );
}
