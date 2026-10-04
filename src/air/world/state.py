"""Versioned world state: event -> reducer -> state.

The runtime's current knowledge of a run (agents, goals, resources, beliefs,
pending decisions) is derived by reducing the event log, never by mutating
global state. Snapshots are versioned and persisted per run.
"""

from __future__ import annotations

import json
import uuid

from air.events.fabric import utcnow


def _empty_state() -> dict:
    return {
        "run": {"status": "CREATED"},
        "agents": {},          # agent_id -> {role, status, parent_id, generation}
        "topology": [],        # [parent_id, child_id] edges
        "messages": 0,
        "tool_calls": {"started": 0, "completed": 0, "failed": 0},
        "spawns": {"requested": 0, "approved": 0, "denied": 0},
        "pending_decisions": [],  # approvals requested, spawn denials to review
        "budget": {},
        "failures": [],
        "capabilities": {"proposed": 0, "promoted": 0, "rejected": 0},
    }


def reduce_events(events: list[dict]) -> dict:
    """Pure reducer: list of event dicts -> world state dict.

    Unknown schema_versions are rejected loudly: applying v1 semantics
    to a future version's payload would silently corrupt state.
    """
    from air.events.fabric import SCHEMA_VERSION
    state = _empty_state()
    for e in events:
        sv = e.get("schema_version", SCHEMA_VERSION)
        if sv != SCHEMA_VERSION:
            raise ValueError(
                f"cannot reduce event {e.get('event_id')}: unknown "
                f"schema_version {sv} (reducer knows {SCHEMA_VERSION})")
        typ = e["type"]
        p = e.get("payload", {})
        aid = e.get("agent_id")
        if typ == "run.created":
            state["run"] = {"status": "CREATED", "goal": p.get("goal"),
                            "strategy": p.get("strategy")}
        elif typ == "run.started":
            state["run"]["status"] = "RUNNING"
        elif typ == "run.completed":
            state["run"]["status"] = "COMPLETED"
        elif typ == "run.failed":
            state["run"]["status"] = "FAILED"
            state["failures"].append({"reason": p.get("reason")})
        elif typ == "agent.created":
            state["agents"][aid] = {"role": p.get("role"), "status": "CREATED",
                                    "parent_id": p.get("parent_id"),
                                    "generation": p.get("generation", 0)}
            if p.get("parent_id"):
                state["topology"].append([p["parent_id"], aid])
        elif typ == "agent.started":
            if aid in state["agents"]:
                state["agents"][aid]["status"] = "RUNNING"
        elif typ == "agent.blocked":
            if aid in state["agents"]:
                state["agents"][aid]["status"] = "BLOCKED"
        elif typ == "agent.message":
            state["messages"] += 1
        elif typ == "agent.completed":
            if aid in state["agents"]:
                state["agents"][aid]["status"] = "COMPLETED"
        elif typ == "agent.failed":
            if aid in state["agents"]:
                state["agents"][aid]["status"] = "FAILED"
            state["failures"].append({"agent_id": aid,
                                      "reason": (p.get("error") or p.get("reason"))})
        elif typ == "agent.terminated":
            if aid in state["agents"]:
                state["agents"][aid]["status"] = "TERMINATED"
        elif typ == "spawn.requested":
            state["spawns"]["requested"] += 1
        elif typ == "spawn.approved":
            state["spawns"]["approved"] += 1
        elif typ == "spawn.denied":
            state["spawns"]["denied"] += 1
            state["pending_decisions"].append(
                {"kind": "spawn_denied", "role": p.get("role"),
                 "reason": p.get("reason"), "at": e.get("timestamp")})
        elif typ == "tool.started":
            state["tool_calls"]["started"] += 1
        elif typ == "tool.completed":
            state["tool_calls"]["completed"] += 1
        elif typ == "tool.failed":
            state["tool_calls"]["failed"] += 1
        elif typ == "budget.exhausted":
            state["budget"]["exhausted"] = True
            state["failures"].append({"agent_id": aid, "reason": "budget exhausted"})
        elif typ == "budget.warning":
            state["budget"]["warning"] = True
        elif typ == "approval.requested":
            state["pending_decisions"].append(
                {"kind": p.get("kind"), "subject": p.get("subject"),
                 "at": e.get("timestamp")})
        elif typ == "capability.proposed":
            state["capabilities"]["proposed"] += 1
        elif typ == "capability.promoted":
            state["capabilities"]["promoted"] += 1
        elif typ == "capability.rejected":
            state["capabilities"]["rejected"] += 1
    state["agent_count"] = len(state["agents"])
    state["active_agents"] = sum(
        1 for a in state["agents"].values()
        if a["status"] not in ("COMPLETED", "FAILED", "CANCELLED", "TERMINATED"))
    return state


class WorldStateStore:
    def __init__(self, conn, event_store) -> None:
        self._conn = conn
        self._events = event_store

    def build(self, run_id: str) -> dict:
        """Reduce the run's full event history into current state."""
        return reduce_events(self._events.list(run_id=run_id, limit=100_000))

    def snapshot(self, run_id: str) -> dict:
        """Persist a versioned snapshot; returns {version, state}."""
        state = self.build(run_id)
        row = self._conn.execute(
            "SELECT MAX(version) FROM world_states WHERE run_id=?",
            (run_id,)).fetchone()
        version = (row[0] or 0) + 1
        self._conn.execute(
            "INSERT INTO world_states (id, run_id, version, state, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            ("ws_" + uuid.uuid4().hex[:12], run_id, version,
             json.dumps(state), utcnow()),
        )
        self._conn.commit()
        return {"version": version, "state": state}

    def latest(self, run_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT version, state, created_at FROM world_states"
            " WHERE run_id=? ORDER BY version DESC LIMIT 1", (run_id,)).fetchone()
        if not row:
            return None
        return {"version": row[0], "state": json.loads(row[1]),
                "created_at": row[2]}
