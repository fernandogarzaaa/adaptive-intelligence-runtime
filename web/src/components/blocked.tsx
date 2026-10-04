/* Rendering for gate-blocked 409s. AIR gates refuse with
   {"detail": {"blocked": [...]}}; those reasons are operator-facing facts
   and must be shown verbatim, never replaced with a generic toast. */

import { ApiError } from '../lib/api';
import { ErrorBox } from './ui';

/** Extract gate-blocked reasons from a 409 body. Null when not a 409 or no reasons. */
export function parseBlocked(error: unknown): string[] | null {
  if (!(error instanceof ApiError) || error.status !== 409) return null;
  try {
    const body = JSON.parse(error.body) as { detail?: unknown };
    const d = body?.detail as { blocked?: unknown } | undefined;
    if (Array.isArray(d?.blocked)) return d.blocked.map(String);
  } catch {
    /* not JSON: fall through to the raw body */
  }
  return null;
}

/** Raw ApiError body shown verbatim: the backend owns the words. */
export function ApiErrorBox({ error, onRetry }: { error: Error; onRetry?: () => void }) {
  const body = error instanceof ApiError ? error.body : error.message;
  return <ErrorBox error={body} onRetry={onRetry} />;
}

/** Blocked reasons list when present, raw body otherwise. */
export function BlockedReasons({ error, onRetry }: { error: Error; onRetry?: () => void }) {
  const reasons = parseBlocked(error);
  if (!reasons) return <ApiErrorBox error={error} onRetry={onRetry} />;
  const raw = error instanceof ApiError ? error.body : error.message;
  return (
    <div className="error-box">
      <div style={{ fontWeight: 650, marginBottom: 6 }}>
        Gate refused the operation ({reasons.length} reason{reasons.length === 1 ? '' : 's'})
      </div>
      <ul style={{ margin: '0 0 8px', paddingLeft: 18 }}>
        {reasons.map((r, i) => (
          <li key={i}>{r}</li>
        ))}
      </ul>
      <div className="faint mono" style={{ fontSize: 11 }}>raw: {raw}</div>
      {onRetry && (
        <div style={{ marginTop: 8 }}>
          <button className="btn small" onClick={onRetry}>Retry</button>
        </div>
      )}
    </div>
  );
}
