import { useEffect } from 'react';
import { Link, NavLink, Route, Routes, useLocation, useNavigate, useParams } from 'react-router-dom';
import { Approvals, Models, Runs } from './lib/api';
import { useApi } from './lib/query';
import { eventStream, useStreamState } from './lib/ws';

import LiveScreen from './screens/Live';
import RunDetailScreen from './screens/RunDetail';
import RunsScreen from './screens/Runs';
import PoliciesScreen from './screens/Policies';
import PolicyDetailScreen from './screens/PolicyDetail';
import CapabilitiesScreen from './screens/Capabilities';
import AuthorityScreen from './screens/Authority';
import ApprovalsScreen from './screens/Approvals';
import ExperienceScreen from './screens/Experience';
import MemoryScreen from './screens/Memory';
import MetricsScreen from './screens/Metrics';

function IndexRedirect() {
  const { data: runs } = useApi(() => Runs.list(10), []);
  const navigate = useNavigate();
  useEffect(() => {
    if (runs) {
      const active = runs.find((r) => !['COMPLETED', 'FAILED', 'CANCELLED'].includes(r.status));
      navigate(active ? `/runs/${active.id}/live` : '/runs', { replace: true });
    }
  }, [runs, navigate]);
  return <div className="dim" style={{ padding: 24 }}>Resolving...</div>;
}

function Shell() {
  const { connected, reconnects } = useStreamState();
  const location = useLocation();
  const { data: approvals } = useApi(() => Approvals.pending(), [], { pollMs: 15000 });
  const { data: models } = useApi(() => Models.list(), [], { pollMs: 60000 });
  const pendingCount = (approvals ?? []).filter((a) => a.status === 'PENDING').length;
  const noProvider = (models ?? []).length > 0 && (models ?? []).every((m) => m.model === 'unavailable');

  useEffect(() => {
    // One canonical stream for the whole console.
    const off = eventStream.subscribe(() => {});
    return () => {
      off();
      // keep the socket alive across route changes
    };
  }, []);

  const nav: { to: string; label: string; count?: number; warn?: boolean }[] = [
    { to: '/runs', label: 'Runs' },
    { to: '/policies', label: 'Policies' },
    { to: '/capabilities', label: 'Capabilities' },
    { to: '/authority', label: 'Authority' },
    { to: '/approvals', label: 'Approvals', count: pendingCount, warn: pendingCount > 0 },
    { to: '/experience', label: 'Experience' },
    { to: '/memory', label: 'Memory' },
    { to: '/metrics', label: 'Metrics' },
  ];

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <Link to="/" style={{ color: 'inherit' }}>AIR Console</Link>
          <div className="sub">Adaptive Intelligence Runtime</div>
        </div>
        <nav className="nav">
          {nav.map((n) => (
            <NavLink key={n.to} to={n.to} className={({ isActive }) => (isActive ? 'active' : '')}>
              <span>{n.label}</span>
              {n.count !== undefined && n.count > 0 && (
                <span className="count" style={n.warn ? { color: 'var(--warn)', borderColor: '#4d3d17' } : {}}>
                  {n.count}
                </span>
              )}
            </NavLink>
          ))}
        </nav>
        <div className="conn">
          <div>
            <span className={`dot ${connected ? 'up' : 'down'}`} />
            {connected ? 'event stream live' : 'event stream down'}
            {reconnects > 0 && <span className="faint"> · {reconnects} reconnects</span>}
          </div>
          {noProvider && (
            <div style={{ marginTop: 6, color: 'var(--warn)' }}>NO MODEL PROVIDER</div>
          )}
          <div className="faint" style={{ marginTop: 6 }}>{location.pathname}</div>
        </div>
      </aside>
      <main className="main">
        <Routes>
          <Route path="/" element={<IndexRedirect />} />
          <Route path="/runs" element={<RunsScreen />} />
          <Route path="/runs/:runId/live" element={<LiveScreen />} />
          <Route path="/runs/:runId" element={<RunDetailScreen />} />
          <Route path="/policies" element={<PoliciesScreen />} />
          <Route path="/policies/:name" element={<PolicyDetailScreen />} />
          <Route path="/capabilities" element={<CapabilitiesScreen />} />
          <Route path="/authority" element={<AuthorityScreen />} />
          <Route path="/approvals" element={<ApprovalsScreen />} />
          <Route path="/experience" element={<ExperienceScreen />} />
          <Route path="/memory" element={<MemoryScreen />} />
          <Route path="/metrics" element={<MetricsScreen />} />
          <Route path="*" element={<NoMatch />} />
        </Routes>
      </main>
    </div>
  );
}

function NoMatch() {
  const { runId } = useParams();
  return (
    <div className="empty">
      <h3>Unknown route{runId ? `: ${runId}` : ''}</h3>
      <p><Link to="/">Back to console root</Link></p>
    </div>
  );
}

export default function App() {
  return <Shell />;
}
