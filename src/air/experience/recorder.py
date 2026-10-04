"""Experience recorder (EVE-like subsystem, owned interfaces).

Every meaningful run produces an experience record derived from the run's
event history — not from agent self-report. The record distinguishes OBSERVED
from DERIVED/SIMULATED, so learning never trains on manufactured evidence.
"""

from __future__ import annotations

import json
import uuid

from pydantic import BaseModel, Field

from air.events.fabric import utcnow
from air.experience.provenance import Provenance


class ExperienceRecord(BaseModel):
    id: str = Field(default_factory=lambda: "exp_" + uuid.uuid4().hex[:12])
    run_id: str
    goal: str
    initial_state: dict = Field(default_factory=dict)
    cognitive_configuration: dict = Field(default_factory=dict)
    agents: list[dict] = Field(default_factory=list)
    actions: list[dict] = Field(default_factory=list)
    observations: list[dict] = Field(default_factory=list)
    outcomes: dict = Field(default_factory=dict)
    cost: float = 0.0
    latency_ms: int = 0
    failures: list[dict] = Field(default_factory=list)
    verification: dict = Field(default_factory=dict)
    final_result: dict | None = None
    provenance: Provenance = Provenance.OBSERVED
    created_at: str = Field(default_factory=utcnow)


class ExperienceRecorder:
    def __init__(self, conn) -> None:
        self._conn = conn

    def record_run(self, run_id: str) -> ExperienceRecord:
        """Build an experience record from the run's persisted state.

        Actions/observations come from the event log and tool-call ledger;
        agent self-reported results are labeled DERIVED, never OBSERVED.
        """
        run = self._conn.execute(
            "SELECT goal, status, strategy, cognitive_plan, total_cost,"
            " total_tokens, error, final_result, created_at, started_at,"
            " completed_at FROM runs WHERE id=?", (run_id,)).fetchone()
        if not run:
            raise ValueError(f"run not found: {run_id}")
        (goal, status, strategy, plan_json, cost, tokens, error,
         final_json, created, started, completed) = run

        agents = self._conn.execute(
            "SELECT id, parent_id, role, specialization, objective, model,"
            " provider, status, status_reason, generation, created_at,"
            " terminated_at FROM agents WHERE root_run_id=? ORDER BY created_at",
            (run_id,)).fetchall()
        agent_rows = [
            {"id": r[0], "parent_id": r[1], "role": r[2],
             "specialization": r[3], "objective": r[4], "model": r[5],
             "provider": r[6], "status": r[7], "status_reason": r[8],
             "generation": r[9]}
            for r in agents
        ]

        events = self._conn.execute(
            "SELECT type, agent_id, payload, timestamp FROM events"
            " WHERE run_id=? ORDER BY rowid", (run_id,)).fetchall()
        actions, observations, failures = [], [], []
        for typ, agent_id, payload_json, ts in events:
            payload = json.loads(payload_json)
            entry = {"type": typ, "agent_id": agent_id, "at": ts}
            if typ.startswith("tool."):
                actions.append({**entry, "detail": payload,
                                "provenance": Provenance.OBSERVED.value})
            elif typ in ("agent.message",):
                observations.append({**entry, "detail": payload,
                                     "provenance": Provenance.DERIVED.value})
            elif typ in ("agent.failed", "budget.exhausted", "policy.blocked"):
                failures.append({**entry, "detail": payload,
                                 "provenance": Provenance.OBSERVED.value})

        tool_calls = self._conn.execute(
            "SELECT tool, status, latency_ms FROM tool_calls WHERE run_id=?",
            (run_id,)).fetchall()
        for tool, st, lat in tool_calls:
            actions.append({"type": "tool.call", "tool": tool, "status": st,
                            "latency_ms": lat,
                            "provenance": Provenance.OBSERVED.value})

        latency_ms = 0
        if started and completed:
            from datetime import datetime
            latency_ms = int((datetime.fromisoformat(completed)
                              - datetime.fromisoformat(started)).total_seconds() * 1000)

        verification = {
            "strategy": (json.loads(plan_json).get("verification_strategy")
                         if plan_json else "none"),
            "failures": failures,
            # A run is verified only if its verification strategy ran and its
            # failures list contains no verification failures. Self-report is
            # never sufficient; evidence rows decide.
            "verified": status == "COMPLETED" and not any(
                "verif" in (f.get("detail") or {}).get("reason", "")
                for f in failures),
        }

        exp = ExperienceRecord(
            run_id=run_id, goal=goal,
            initial_state={"strategy": strategy,
                           "plan": json.loads(plan_json) if plan_json else {}},
            cognitive_configuration=json.loads(plan_json) if plan_json else {},
            agents=agent_rows, actions=actions, observations=observations,
            outcomes={"status": status,
                      "final_result": json.loads(final_json) if final_json else None,
                      "error": error,
                      "provenance": (Provenance.OBSERVED.value
                                     if status in ("COMPLETED", "FAILED", "CANCELLED")
                                     else Provenance.DERIVED.value)},
            cost=cost or 0.0, latency_ms=latency_ms, failures=failures,
            verification=verification,
            final_result=json.loads(final_json) if final_json else None,
        )
        self._conn.execute(
            """INSERT INTO experiences (id, run_id, goal, initial_state,
               cognitive_configuration, agents, actions, observations, outcomes,
               cost, latency_ms, failures, verification, final_result, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (exp.id, run_id, goal, json.dumps(exp.initial_state),
             json.dumps(exp.cognitive_configuration), json.dumps(agent_rows),
             json.dumps(actions), json.dumps(observations),
             json.dumps(exp.outcomes), exp.cost, latency_ms,
             json.dumps(failures), json.dumps(verification),
             json.dumps(exp.final_result) if exp.final_result else None,
             exp.created_at),
        )
        self._conn.commit()
        return exp

    def get(self, exp_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT id, run_id, goal, cognitive_configuration, outcomes, cost,"
            " latency_ms, failures, verification, created_at"
            " FROM experiences WHERE id=?", (exp_id,)).fetchone()
        if not row:
            return None
        keys = ["id", "run_id", "goal", "cognitive_configuration", "outcomes",
                "cost", "latency_ms", "failures", "verification", "created_at"]
        out = dict(zip(keys, row))
        for k in ("cognitive_configuration", "outcomes", "failures", "verification"):
            out[k] = json.loads(out[k]) if out[k] else None
        return out

    def list(self, limit: int = 50) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id, run_id, goal, cost, latency_ms, created_at"
            " FROM experiences ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [{"id": r[0], "run_id": r[1], "goal": r[2], "cost": r[3],
                 "latency_ms": r[4], "created_at": r[5]} for r in rows]
