/* Single canonical event-stream client.
   One WebSocket to /ws/events for the whole console. Cursor discipline:
   the last received event_id is persisted; every reconnect resumes with
   ?last_event_id= so the UI replays exactly the missed events and
   converges. No invented UI events: everything comes from the fabric. */

import { useEffect, useState, useSyncExternalStore } from 'react';
import { wsUrl } from './api';
import type { StreamEvent } from './types';

const CURSOR_KEY = 'air:ws:cursor';
const BUFFER_MAX = 400;

type Listener = (ev: StreamEvent) => void;

class EventStream {
  private ws: WebSocket | null = null;
  private listeners = new Set<Listener>();
  private buffer: StreamEvent[] = [];
  private reconnectTimer: number | null = null;
  private attempts = 0;
  private _connected = false;
  private _reconnects = 0;
  private stateListeners = new Set<() => void>();
  private manualClose = false;

  get cursor(): string | null {
    try {
      return localStorage.getItem(CURSOR_KEY);
    } catch {
      return null;
    }
  }

  private setCursor(id: string) {
    try {
      localStorage.setItem(CURSOR_KEY, id);
    } catch {
      /* storage unavailable: cursor lives for the session only */
    }
  }

  get connected() {
    return this._connected;
  }

  get reconnects() {
    return this._reconnects;
  }

  get recent(): StreamEvent[] {
    return this.buffer;
  }

  subscribeState(fn: () => void): () => void {
    this.stateListeners.add(fn);
    return () => this.stateListeners.delete(fn);
  }

  private emitState() {
    for (const fn of this.stateListeners) fn();
  }

  subscribe(fn: Listener): () => void {
    this.listeners.add(fn);
    if (!this.ws && !this.manualClose) this.connect();
    return () => {
      this.listeners.delete(fn);
    };
  }

  connect() {
    this.manualClose = false;
    if (this.ws) return;
    let ws: WebSocket;
    try {
      ws = new WebSocket(wsUrl(this.cursor));
    } catch {
      this.scheduleReconnect();
      return;
    }
    this.ws = ws;
    ws.onopen = () => {
      this.attempts = 0;
      this._connected = true;
      this.emitState();
    };
    ws.onmessage = (msg) => {
      try {
        const ev = JSON.parse(msg.data) as StreamEvent;
        if (ev.event_type === 'hello') {
          if (ev.last_event_id) this.setCursor(ev.last_event_id as string);
          return;
        }
        if (!ev.event_id) return;
        this.setCursor(ev.event_id);
        this.buffer.push(ev);
        if (this.buffer.length > BUFFER_MAX) {
          this.buffer.splice(0, this.buffer.length - BUFFER_MAX);
        }
        for (const fn of this.listeners) fn(ev);
        this.emitState();
      } catch {
        /* malformed frame: ignore, cursor already advanced past nothing */
      }
    };
    const down = () => {
      this._connected = false;
      this.ws = null;
      this.emitState();
      if (!this.manualClose) {
        this._reconnects += 1;
        this.scheduleReconnect();
      }
    };
    ws.onclose = down;
    ws.onerror = () => {
      try {
        ws.close();
      } catch {
        /* already closed */
      }
    };
  }

  private scheduleReconnect() {
    if (this.reconnectTimer !== null) return;
    const delay = Math.min(1000 * 2 ** this.attempts, 15000);
    this.attempts += 1;
    this.reconnectTimer = window.setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
    }, delay);
  }

  disconnect() {
    this.manualClose = true;
    if (this.reconnectTimer !== null) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    try {
      this.ws?.close();
    } catch {
      /* already closed */
    }
    this.ws = null;
    this._connected = false;
    this.emitState();
  }
}

export const eventStream = new EventStream();

/** Re-render when connection state changes. */
export function useStreamState() {
  return useSyncExternalStore(
    (cb) => eventStream.subscribeState(cb),
    () => ({ connected: eventStream.connected, reconnects: eventStream.reconnects }),
  );
}

/** Receive live events matching a predicate. */
export function useLiveEvents(filter?: (ev: StreamEvent) => boolean): StreamEvent[] {
  const [events, setEvents] = useState<StreamEvent[]>(() =>
    filter ? eventStream.recent.filter(filter) : [...eventStream.recent],
  );
  useEffect(
    () =>
      eventStream.subscribe((ev) => {
        if (!filter || filter(ev)) {
          setEvents((prev) => {
            const next = [...prev, ev];
            return next.length > BUFFER_MAX ? next.slice(next.length - BUFFER_MAX) : next;
          });
        }
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );
  return events;
}
