/* Data fetching with live refresh. Screens fetch from the backend and
   re-fetch when relevant fabric events arrive (plus optional polling).
   The UI never caches authority: every refresh re-reads backend state. */

import { useCallback, useEffect, useRef, useState } from 'react';
import { eventStream } from './ws';
import type { StreamEvent } from './types';

interface UseApiOptions {
  /** Refresh when an event matches. Default: any event for the run. */
  watch?: (ev: StreamEvent) => boolean;
  /** Extra poll interval in ms. Default 0 (off). */
  pollMs?: number;
  /** Skip the initial fetch. */
  skip?: boolean;
}

export function useApi<T>(fn: () => Promise<T>, deps: unknown[], opts: UseApiOptions = {}) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(!opts.skip);
  const fnRef = useRef(fn);
  fnRef.current = fn;
  const watchRef = useRef(opts.watch);
  watchRef.current = opts.watch;

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const d = await fnRef.current();
      setData(d);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e : new Error(String(e)));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (opts.skip) return;
    let cancelled = false;
    (async () => {
      setLoading(true);
      try {
        const d = await fnRef.current();
        if (!cancelled) {
          setData(d);
          setError(null);
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e : new Error(String(e)));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  useEffect(
    () =>
      eventStream.subscribe((ev) => {
        const w = watchRef.current;
        if (!w || w(ev)) refresh();
      }),
    [refresh],
  );

  useEffect(() => {
    if (!opts.pollMs) return;
    const t = window.setInterval(refresh, opts.pollMs);
    return () => clearInterval(t);
  }, [refresh, opts.pollMs]);

  return { data, error, loading, refresh };
}

/** Watch all events for one run. */
export const watchRun = (runId: string) => (ev: StreamEvent) => ev.run_id === runId;
