/* Shared UI primitives. Dense, technical, no decoration. */

import React, { useState } from 'react';

export function Panel({ title, right, children, flush }: {
  title: string; right?: React.ReactNode; children: React.ReactNode; flush?: boolean;
}) {
  return (
    <div className="panel">
      <div className="panel-h"><span>{title}</span><span>{right}</span></div>
      <div className={flush ? 'panel-b flush' : 'panel-b'}>{children}</div>
    </div>
  );
}

const STATUS_CLASS: Record<string, string> = {
  ACTIVE: 'info', RUNNING: 'info', CREATED: 'info',
  COMPLETED: 'ok', APPROVED: 'ok', GRANT: 'ok', COMMITTED: 'ok', SUPPORTED: 'ok',
  SOUND: 'ok', VALIDATED: 'ok', PROMOTED: 'ok', PASS: 'ok',
  PAUSED: 'warn', WAITING: 'warn', PENDING: 'warn', APPROVAL_PENDING: 'warn',
  BLOCKED: 'warn', UNCHECKED: 'warn', VERIFYING: 'warn',
  FAILED: 'bad', DENIED: 'bad', DENY: 'bad', CANCELLED: 'bad', TERMINATED: 'bad',
  FALSIFIED: 'bad', FAIL: 'bad', EXPLOITABLE: 'bad', INVALID: 'bad',
};

export function Badge({ status, children }: { status?: string | null; children?: React.ReactNode }) {
  const s = String(status ?? children ?? 'unknown');
  const cls = STATUS_CLASS[s.toUpperCase()] ?? '';
  return <span className={`badge ${cls}`}>{children ?? s}</span>;
}

/** Honest unknown: the backend did not report a value. Never infer. */
export function Unknown() {
  return <span className="faint">unknown</span>;
}

export function val(v: unknown): React.ReactNode {
  if (v === null || v === undefined || v === '') return <Unknown />;
  if (typeof v === 'object') return <span className="mono">{JSON.stringify(v)}</span>;
  return <span>{String(v)}</span>;
}

export function KV({ rows }: { rows: [string, React.ReactNode][] }) {
  return (
    <dl className="kv">
      {rows.map(([k, v]) => (
        <React.Fragment key={k}>
          <dt>{k}</dt>
          <dd>{v}</dd>
        </React.Fragment>
      ))}
    </dl>
  );
}

export function Hash({ id, len = 12 }: { id: string | null | undefined; len?: number }) {
  if (!id) return <Unknown />;
  return <span className="mono" title={id}>{id.slice(0, len)}{id.length > len ? '...' : ''}</span>;
}

export function Time({ iso, ago }: { iso: string | null | undefined; ago?: boolean }) {
  if (!iso) return <Unknown />;
  const d = new Date(iso);
  if (ago) {
    const s = Math.max(0, Math.round((Date.now() - d.getTime()) / 1000));
    if (s < 60) return <span className="mono">{s}s ago</span>;
    if (s < 3600) return <span className="mono">{Math.floor(s / 60)}m ago</span>;
    return <span className="mono">{Math.floor(s / 3600)}h ago</span>;
  }
  return <span className="mono">{d.toLocaleString()}</span>;
}

export function Empty({ title, children }: { title: string; children?: React.ReactNode }) {
  return (
    <div className="empty">
      <h3>{title}</h3>
      {children}
    </div>
  );
}

export function ErrorBox({ error, onRetry }: { error: Error | string; onRetry?: () => void }) {
  return (
    <div className="error-box">
      {typeof error === 'string' ? error : error.message}
      {onRetry && (
        <div style={{ marginTop: 8 }}>
          <button className="btn small" onClick={onRetry}>Retry</button>
        </div>
      )}
    </div>
  );
}

export function Loading({ label }: { label?: string }) {
  return <div className="dim" style={{ padding: 18 }}>{label ?? 'Loading...'}</div>;
}

export function Tabs({ tabs, active, onChange }: {
  tabs: { id: string; label: string }[]; active: string; onChange: (id: string) => void;
}) {
  return (
    <div className="tabs">
      {tabs.map((t) => (
        <button key={t.id} className={t.id === active ? 'active' : ''} onClick={() => onChange(t.id)}>
          {t.label}
        </button>
      ))}
    </div>
  );
}

export function ConfirmDialog({ title, body, confirmLabel, danger, onConfirm, onCancel }: {
  title: string;
  body: React.ReactNode;
  confirmLabel: string;
  danger?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <div className="dialog-backdrop" onClick={onCancel}>
      <div className="dialog" onClick={(e) => e.stopPropagation()}>
        <div className="dialog-h">{title}</div>
        <div className="dialog-b">{body}</div>
        <div className="dialog-f">
          <button className="btn" onClick={onCancel}>Cancel</button>
          <button className={danger ? 'btn danger' : 'btn primary'} onClick={onConfirm}>
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

export function JsonBlock({ data }: { data: unknown }) {
  const [open, setOpen] = useState(false);
  return (
    <div>
      <button className="btn small" onClick={() => setOpen(!open)}>
        {open ? 'Hide raw JSON' : 'Show raw JSON'}
      </button>
      {open && <pre className="json">{JSON.stringify(data, null, 2)}</pre>}
    </div>
  );
}

export function fmtMoney(usd: number | null | undefined): string {
  if (usd === null || usd === undefined) return 'unknown';
  return `$${usd.toFixed(2)}`;
}

export function fmtElapsed(ms: number): string {
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s`;
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
}
