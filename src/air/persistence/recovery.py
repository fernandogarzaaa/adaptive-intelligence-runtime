"""Crash recovery: reconcile states that cannot survive a process restart.

Runs once per process, inside ``AgentRuntime.__init__``. A crash can leave:

- tool calls in a non-terminal execution state (REQUESTED through
  VERIFIED): their handler ran in the dead process, so no live code will
  ever complete them;
- agents in CREATED/RUNNING: their asyncio task lived in the dead process,
  so nothing will ever drive them to a terminal state (and a run whose
  agents are stuck RUNNING would never complete).

Both are moved to terminal states here, atomically with their recovery
events in a single transaction: a crash before the commit leaves the stuck
state (a later restart reconciles it again -- the operation is
idempotent); a crash after leaves the terminal state plus its event.
Never a hybrid.

Deliberately NOT touched: APPROVAL_PENDING tool calls (a legitimate rest
state -- the operator may still approve after restart; ``resume()`` fails
closed if the in-memory call context is gone), BLOCKED/WAITING/PAUSED
agents (their tasks already returned; they are honestly waiting, not
in-flight), and run rows (the operator sees the failed agents and decides;
runs are never auto-completed by recovery).

Budget accounting: a tool call that reached RESERVED already consumed one
budget unit for a genuine dispatch attempt. The unit stays consumed -- it
was spent, not leaked -- and the call is never re-dispatched, so there is
no double-spend. The reservation record stays on the row for audit.
"""

from __future__ import annotations

from air.events.fabric import Event, utcnow

# Tool-call states from which no live handler can return after a restart.
# Every state except the terminal ones and APPROVAL_PENDING is transient
# inside execute()/resume(), which only ever run in-process: after a
# restart, no live code will ever advance them. APPROVAL_PENDING is a
# legitimate rest state (the operator may still approve; resume() fails
# closed if the in-memory call context is gone).
_STUCK_TOOL_STATES = ("REQUESTED", "VALIDATED", "RESERVED", "DISPATCHED",
                      "OBSERVED", "VERIFIED")
# Agent states that imply a live task in this process.
_STUCK_AGENT_STATES = ("CREATED", "RUNNING")


def reconcile(conn, store) -> dict:
    """Move crash-stuck tool calls and agents to terminal states.

    ``conn`` and ``store`` must share the same underlying connection.
    Returns ``{"interrupted_tool_calls": [...], "failed_agents": [...]}``.
    The recovery events are persisted atomically with the state changes;
    live fanout is unnecessary (no subscribers exist yet at startup) --
    later subscribers see the events via ledger replay.
    """
    with store.atomic():
        with conn:
            placeholders = ",".join("?" for _ in _STUCK_TOOL_STATES)
            stuck_calls = conn.execute(
                "SELECT id, run_id, agent_id, tool_name FROM tool_calls"
                f" WHERE state IN ({placeholders})", _STUCK_TOOL_STATES).fetchall()
            for call_id, run_id, agent_id, tool_name in stuck_calls:
                conn.execute(
                    "UPDATE tool_calls SET state='INTERRUPTED',"
                    " error=?, completed_at=? WHERE id=?",
                    ("restart: no live handler; the call did not complete",
                     utcnow(), call_id))
                store.insert(Event(
                    type="tool.interrupted", run_id=run_id, agent_id=agent_id,
                    # Declared root: the interruption was caused by the
                    # process crash, which is not an event. No fabricated
                    # causal parent is inserted.
                    causation_id=None,
                    correlation_id=run_id,
                    payload={"call_id": call_id, "tool": tool_name,
                             "reason": "restart: handler gone mid-execution"}))
            stuck_agents = conn.execute(
                "SELECT id, root_run_id, role FROM agents"
                " WHERE status IN ('CREATED','RUNNING')").fetchall()
            for agent_id, run_id, role in stuck_agents:
                conn.execute(
                    "UPDATE agents SET status='FAILED', status_reason=?,"
                    " terminated_at=? WHERE id=?",
                    ("restart: agent did not reach a terminal state",
                     utcnow(), agent_id))
                store.insert(Event(
                    type="agent.failed", run_id=run_id, agent_id=agent_id,
                    # Declared root: the failure was caused by the restart,
                    # which is not an event. No fabricated causal parent.
                    causation_id=None,
                    correlation_id=run_id,
                    payload={"role": role,
                             "error": "restart: agent did not reach a"
                                      " terminal state"}))
        # conn.__exit__ commits state changes + events atomically.
    return {"interrupted_tool_calls": [c[0] for c in stuck_calls],
            "failed_agents": [a[0] for a in stuck_agents]}
