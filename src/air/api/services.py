"""Service layer: the API's command/query boundary over AIR core.

The API is a projection and control surface over the runtime, never an
alternative execution path. Route handlers must not contain business
logic: every mutation goes through a service, and every service
delegates to the runtime or a core engine. Read models are built here
from persisted state, never from client assertions.

Authority rule: the client requests an operation; AIR independently
resolves authorization. Capabilities, verdicts, statuses, provenance,
budgets, and verification state are resolved by the runtime. Request
models must never carry them, and services must never trust them.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from typing import Any, Callable

from air.agents.runtime import AgentRuntime


# ------------------------------------------------------------------ errors

class ApiError(Exception):
    status_code = 500

    def __init__(self, detail: Any) -> None:
        super().__init__(str(detail))
        self.detail = detail


class NotFound(ApiError):
    status_code = 404


class Conflict(ApiError):
    status_code = 409


class Blocked(ApiError):
    """A core gate refused the operation (policy gate, approval, bridge)."""
    status_code = 409


class Unprocessable(ApiError):
    status_code = 422


# ------------------------------------------------------------- idempotency

class IdempotencyStore:
    """Replay store for Idempotency-Key headers.

    A repeated mutation carrying the same (key, method, path) replays the
    stored response verbatim instead of re-executing. Scope is per runtime
    database.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def replay(self, key: str, method: str, path: str) -> tuple[int, dict] | None:
        row = self._conn.execute(
            "SELECT status_code, response FROM idempotency_keys"
            " WHERE key=? AND method=? AND path=?",
            (key, method, path)).fetchone()
        if not row:
            return None
        return row[0], json.loads(row[1])

    def record(self, key: str, method: str, path: str,
               status_code: int, body: dict) -> None:
        from air.events.fabric import utcnow  # local import: keep module import light
        self._conn.execute(
            "INSERT OR IGNORE INTO idempotency_keys"
            " (key, method, path, status_code, response, created_at)"
            " VALUES (?,?,?,?,?,?)",
            (key, method, path, status_code, json.dumps(body), utcnow()))
        self._conn.commit()


# -------------------------------------------------------- event projection

def project_event(row: dict) -> dict:
    """Canonical realtime projection. Every realtime message carries the
    full envelope so a client can reconnect with a cursor and converge."""
    return {
        "event_id": row["event_id"],
        "event_type": row["type"],
        "schema_version": row["schema_version"],
        "timestamp": row["timestamp"],
        "run_id": row["run_id"],
        "agent_id": row["agent_id"],
        "causation_id": row["causation_id"],
        "correlation_id": row["correlation_id"],
        "sequence": row["sequence"],
        "payload": row["payload"],
    }


class EventService:
    """Query layer over the canonical event fabric."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def list(self, run_id: str | None = None, event_type: str | None = None,
             limit: int = 500, after_event_id: str | None = None) -> list[dict]:
        q = ("SELECT rowid, event_id, timestamp, run_id, agent_id, type,"
             " payload, causation_id, correlation_id, schema_version"
             " FROM events")
        clauses, params = [], []
        if run_id:
            clauses.append("run_id = ?")
            params.append(run_id)
        if event_type:
            clauses.append("type = ?")
            params.append(event_type)
        if after_event_id:
            clauses.append("rowid > (SELECT rowid FROM events"
                           " WHERE event_id = ?)")
            params.append(after_event_id)
        if clauses:
            q += " WHERE " + " AND ".join(clauses)
        q += " ORDER BY rowid LIMIT ?"
        params.append(limit)
        rows = self._conn.execute(q, params).fetchall()
        out = []
        for r in rows:
            out.append({
                "sequence": r[0], "event_id": r[1], "timestamp": r[2],
                "run_id": r[3], "agent_id": r[4], "type": r[5],
                "payload": json.loads(r[6]), "causation_id": r[7],
                "correlation_id": r[8], "schema_version": r[9],
            })
        return out

    def latest_event_id(self) -> str | None:
        row = self._conn.execute(
            "SELECT event_id FROM events ORDER BY rowid DESC LIMIT 1").fetchone()
        return row[0] if row else None


# ------------------------------------------------------------------ runs

class RunService:
    def __init__(self, conn: sqlite3.Connection,
                 runtime: Callable[[], AgentRuntime]) -> None:
        self._conn = conn
        self._runtime = runtime

    async def start(self, goal: str, context: dict | None = None,
                    strategy: str | None = None,
                    agent_budget: int | None = None,
                    seed: str | None = None) -> dict:
        from air.allocation.allocator import Strategy
        rt = self._runtime()
        strat = Strategy(strategy) if strategy else None
        run_id = await rt.create_run(goal, context=context, strategy=strat,
                                     agent_budget=agent_budget, seed=seed)
        asyncio.create_task(rt.start_run(run_id))
        return {"run_id": run_id}

    def get(self, run_id: str) -> dict:
        row = self._conn.execute(
            "SELECT id, goal, status, strategy, cognitive_plan, seed,"
            " total_cost, total_tokens, error, final_result,"
            " created_at, started_at, completed_at"
            " FROM runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            raise NotFound("run not found")
        keys = ["id", "goal", "status", "strategy", "cognitive_plan", "seed",
                "total_cost", "total_tokens", "error", "final_result",
                "created_at", "started_at", "completed_at"]
        out = dict(zip(keys, row))
        for k in ("cognitive_plan", "final_result"):
            if out[k]:
                out[k] = json.loads(out[k])
        return out

    def list(self, limit: int = 20) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id, goal, status, strategy, created_at, completed_at"
            " FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [{"id": r[0], "goal": r[1], "status": r[2], "strategy": r[3],
                 "created_at": r[4], "completed_at": r[5]} for r in rows]

    async def pause(self, run_id: str) -> dict:
        self.get(run_id)
        await self._runtime().pause_run(run_id)
        return {"run_id": run_id, "status": "PAUSED"}

    async def resume(self, run_id: str) -> dict:
        self.get(run_id)
        await self._runtime().resume_run(run_id)
        return {"run_id": run_id, "status": "ACTIVE"}

    async def cancel(self, run_id: str) -> dict:
        """Naturally idempotent: cancelling a terminal run returns its
        current state instead of re-executing termination."""
        run = self.get(run_id)
        if run["status"] in ("CANCELLED", "COMPLETED", "FAILED"):
            return {"run_id": run_id, "status": run["status"],
                    "idempotent": True}
        await self._runtime().cancel_run(run_id)
        return {"run_id": run_id, "status": "CANCELLED"}

    def agents(self, run_id: str) -> list[dict]:
        self.get(run_id)
        rows = self._conn.execute(
            "SELECT id, parent_id, role, specialization, objective, model,"
            " provider, status, status_reason, created_at, terminated_at,"
            " generation FROM agents WHERE root_run_id=? ORDER BY created_at",
            (run_id,)).fetchall()
        keys = ["id", "parent_id", "role", "specialization", "objective",
                "model", "provider", "status", "status_reason",
                "created_at", "terminated_at", "generation"]
        return [dict(zip(keys, r)) for r in rows]

    def world(self, run_id: str) -> dict:
        self.get(run_id)
        row = self._conn.execute(
            "SELECT state FROM world_state WHERE run_id=?", (run_id,)).fetchone()
        return json.loads(row[0]) if row and row[0] else {}


# ----------------------------------------------------------------- agents

class AgentService:
    def __init__(self, conn: sqlite3.Connection,
                 runtime: Callable[[], AgentRuntime]) -> None:
        self._conn = conn
        self._runtime = runtime

    def get(self, agent_id: str) -> dict:
        agent = self._runtime().get_agent(agent_id)
        if agent is None:
            raise NotFound("agent not found")
        return agent.model_dump()

    def list(self, run_id: str | None = None, limit: int = 100) -> list[dict]:
        q = ("SELECT id, parent_id, root_run_id, role, objective, status"
             " FROM agents")
        params: list = []
        if run_id:
            q += " WHERE root_run_id=?"
            params.append(run_id)
        q += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = self._conn.execute(q, params).fetchall()
        return [{"id": r[0], "parent_id": r[1], "root_run_id": r[2],
                 "role": r[3], "objective": r[4], "status": r[5]}
                for r in rows]

    async def create(self, run_id: str, role: str, objective: str,
                     specialization: str | None = None,
                     capabilities: list[str] | None = None,
                     tools: list[str] | None = None,
                     model: str | None = None,
                     memory_scope: str = "task") -> dict:
        """Create a root agent for a run. Security grants are never
        client-supplied: a fresh agent starts with no grants."""
        from air.agents.runtime import BudgetExhausted
        RunService(self._conn, self._runtime).get(run_id)  # 404 if unknown
        rt = self._runtime()
        try:
            agent = await rt.create_agent(
                run_id, role, objective, specialization=specialization,
                capabilities=capabilities or [], tools=tools or [],
                model=model, memory_scope=memory_scope)
        except BudgetExhausted as e:
            raise Conflict(str(e))
        return {"agent_id": agent.id, "role": agent.role,
                "status": agent.status.value}

    async def spawn(self, parent_id: str, objective: str, role: str,
                    specialization: str | None = None,
                    capabilities: list[str] | None = None,
                    tools: list[str] | None = None,
                    model: str | None = None,
                    constraints: dict | None = None,
                    uncertainty: float = 0.5,
                    reason_hint: str | None = None) -> dict:
        """Spawn goes through the runtime's spawn policy. The client
        requests; the policy decides. Granted security capabilities are
        never client-supplied."""
        rt = self._runtime()
        try:
            agent, decision = await rt.spawn_agent(
                parent_id, objective, role,
                specialization=specialization, capabilities=capabilities,
                tools=tools, model=model, constraints=constraints,
                uncertainty=uncertainty, reason_hint=reason_hint)
        except ValueError as e:
            raise NotFound(str(e))
        return {
            "agent_id": agent.id if agent else None,
            "decision": decision.decision,
            "reason": decision.reason,
            "expected_gain": decision.expected_gain,
            "estimated_cost": decision.estimated_cost,
            "risk": decision.risk,
            "decision_id": decision.decision_id,
        }

    async def terminate(self, agent_id: str, subtree: bool = True) -> dict:
        terminated = await self._runtime().terminate_agent(
            agent_id, subtree=subtree)
        return {"agent_id": agent_id, "terminated": terminated}

    async def message(self, run_id: str, from_agent_id: str | None,
                      to_agent_id: str | None, channel: str, kind: str,
                      payload: dict) -> dict:
        msg_id = await self._runtime().send_message(
            run_id, from_agent_id, to_agent_id, channel, kind, payload)
        return {"message_id": msg_id}


# ------------------------------------------------------------------ tools

class ToolService:
    def __init__(self, conn: sqlite3.Connection,
                 runtime: Callable[[], AgentRuntime]) -> None:
        self._conn = conn
        self._runtime = runtime

    def list(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id, name, server, capability_class, enabled, policy_status"
            " FROM tools ORDER BY name").fetchall()
        return [{"id": r[0], "name": r[1], "server": r[2],
                 "capability_class": r[3], "enabled": bool(r[4]),
                 "policy_status": r[5]} for r in rows]

    async def call(self, agent_id: str, tool_name: str, args: dict) -> dict:
        """The client requests a tool call; the gateway independently
        resolves authorization, budget, approvals, and verification."""
        try:
            result = await self._runtime().call_tool(agent_id, tool_name, args)
        except ValueError as e:
            raise NotFound(str(e))
        return result.model_dump() if hasattr(result, "model_dump") else result

    def get_call(self, call_id: str) -> dict:
        row = self._conn.execute(
            "SELECT id, run_id, agent_id, tool_name, server_id, capability,"
            " state, args_redacted, args_hash, result_redacted, provenance,"
            " authorization_decision, policy_version, approval_id,"
            " verification_status, error, latency_ms, created_at, completed_at"
            " FROM tool_calls WHERE id=?", (call_id,)).fetchone()
        if not row:
            raise NotFound("tool call not found")
        keys = ["id", "run_id", "agent_id", "tool_name", "server_id",
                "capability", "state", "args_redacted", "args_hash",
                "result_redacted", "provenance", "authorization_decision",
                "policy_version", "approval_id", "verification_status",
                "error", "latency_ms", "created_at", "completed_at"]
        out = dict(zip(keys, row))
        for k in ("args_redacted", "result_redacted", "provenance",
                  "authorization_decision"):
            if out[k]:
                out[k] = json.loads(out[k])
        return out

    def list_calls(self, run_id: str | None = None,
                   agent_id: str | None = None, limit: int = 100) -> list[dict]:
        q = ("SELECT id, run_id, agent_id, tool_name, capability, state,"
             " verification_status, created_at FROM tool_calls")
        clauses, params = [], []
        if run_id:
            clauses.append("run_id = ?")
            params.append(run_id)
        if agent_id:
            clauses.append("agent_id = ?")
            params.append(agent_id)
        if clauses:
            q += " WHERE " + " AND ".join(clauses)
        q += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = self._conn.execute(q, params).fetchall()
        return [{"id": r[0], "run_id": r[1], "agent_id": r[2],
                 "tool_name": r[3], "capability": r[4], "state": r[5],
                 "verification_status": r[6], "created_at": r[7]}
                for r in rows]


# -------------------------------------------------------------- approvals

class ApprovalService:
    def __init__(self, conn: sqlite3.Connection,
                 runtime: Callable[[], AgentRuntime]) -> None:
        self._conn = conn
        self._runtime = runtime

    def _store(self):
        from air.security.approvals import ApprovalStore
        return ApprovalStore(self._conn)

    def pending(self) -> list[dict]:
        return self._store().pending()

    def get(self, approval_id: str) -> dict:
        rec = self._store().get(approval_id)
        if rec is None:
            raise NotFound("approval not found")
        return rec

    async def decide(self, approval_id: str, approved: bool,
                     decided_by: str = "operator") -> dict:
        """Idempotent by nature: deciding an already-decided approval with
        the same decision replays the current record; a conflicting
        decision is a 409, never a second execution."""
        from air.security.approvals import ApprovalDenied
        store = self._store()
        rec = store.get(approval_id)
        if rec is None:
            raise NotFound("approval not found")
        if rec["status"] != "PENDING":
            want = "APPROVED" if approved else "DENIED"
            if rec["status"] == want:
                return {**rec, "idempotent": True}
            raise Conflict(
                f"approval {approval_id} already {rec['status']};"
                f" cannot re-decide as {want}")
        try:
            store.decide(approval_id, approved, decided_by)
        except ApprovalDenied as e:
            raise Conflict(str(e))
        rec = store.get(approval_id)
        # Resume a tool call that was parked on this approval.
        resume_note = None
        if approved and rec["kind"] == "tool_call":
            gateway = self._runtime().tool_gateway()
            try:
                await gateway.resume(approval_id)
            except KeyError as e:
                # The approval is decided regardless; there was simply no
                # parked gateway call to resume (e.g. decided twice across
                # a restart, or approved outside the gateway flow).
                resume_note = str(e)
        rec = {**rec, "idempotent": False}
        if resume_note:
            rec["resume_note"] = resume_note
        return rec


# --------------------------------------------------------------- policies

class PolicyService:
    def __init__(self, conn: sqlite3.Connection,
                 runtime: Callable[[], AgentRuntime]) -> None:
        self._conn = conn
        self._runtime = runtime

    def _store(self):
        from air.learning.policies import PolicyStore
        rt = self._runtime()
        return PolicyStore(self._conn,
                           emit=lambda t, payload=None: rt.emit(
                               t, payload=payload))

    def list(self) -> list[dict]:
        store = self._store()
        rows = self._conn.execute(
            "SELECT name, current_version FROM policies").fetchall()
        return [{"name": r[0], "current_version": r[1],
                 "effects": store.current_effects(r[0])} for r in rows]

    def get(self, name: str) -> dict:
        store = self._store()
        cur = store.current(name)
        if cur is None:
            raise NotFound("policy not found")
        return {"current": cur.model_dump(),
                "history": [v.model_dump() for v in store.history(name)]}

    async def propose(self, name: str, params: dict, reason: str) -> dict:
        ver = await self._store().propose(name, params, reason)
        return {"policy": name, "version": ver.version,
                "status": ver.status}

    def _persisted_evidence(self, name: str, version: str,
                            evaluation_id: str,
                            assurance_id: str) -> tuple[dict, dict]:
        """Resolve verdicts from persisted rows. The client names the
        evidence; AIR reads what it says. Caller-supplied verdict dicts
        are never accepted."""
        ev = self._conn.execute(
            "SELECT id, verdict, metrics FROM evaluation_runs WHERE id=?",
            (evaluation_id,)).fetchone()
        if not ev:
            raise NotFound(f"evaluation {evaluation_id} not found")
        ar = self._conn.execute(
            "SELECT id, evaluator_verdict, system_verdict FROM assurance_runs"
            " WHERE id=?", (assurance_id,)).fetchone()
        if not ar:
            raise NotFound(f"assurance {assurance_id} not found")
        evaluation = {"evaluation_id": ev[0], "verdict": ev[1],
                      "metrics": json.loads(ev[2]) if ev[2] else {}}
        assurance = {"assurance_id": ar[0],
                     "evaluator_verdict": ar[1], "system_verdict": ar[2]}
        return evaluation, assurance

    async def promote(self, name: str, version: str,
                      evaluation_id: str, assurance_id: str) -> dict:
        from air.learning.policies import GateBlocked
        evaluation, assurance = self._persisted_evidence(
            name, version, evaluation_id, assurance_id)
        try:
            ver = await self._store().promote(
                name, version, evaluation=evaluation, assurance=assurance)
        except GateBlocked as e:
            raise Blocked({"blocked": e.reasons})
        return {"policy": name, "version": ver.version,
                "status": ver.status}

    async def rollback(self, name: str) -> dict:
        from air.learning.policies import GateBlocked
        try:
            ver = await self._store().rollback(name)
        except GateBlocked as e:
            raise Blocked({"blocked": e.reasons})
        return {"policy": name, "version": ver.version,
                "status": ver.status}

    async def request_rollback(self, name: str, reason: str,
                               evidence: dict | None = None,
                               requested_by: str = "operator") -> dict:
        from air.learning.policies import GateBlocked
        try:
            return await self._store().request_rollback(
                name, reason, evidence or {}, requested_by)
        except GateBlocked as e:
            raise Blocked({"blocked": e.reasons})

    async def approve_rollback(self, rollback_id: str,
                               approved_by: str = "operator") -> dict:
        """Idempotent: approving an already-approved rollback replays the
        resulting version instead of flipping twice."""
        from air.learning.policies import GateBlocked
        row = self._conn.execute(
            "SELECT status, to_version FROM policy_rollbacks WHERE id=?",
            (rollback_id,)).fetchone()
        if not row:
            raise NotFound("rollback not found")
        if row[0] == "APPROVED":
            return {"version": row[1], "status": "PROMOTED",
                    "idempotent": True}
        try:
            ver = await self._store().approve_rollback(rollback_id,
                                                       approved_by)
        except GateBlocked as e:
            raise Blocked({"blocked": e.reasons})
        return {"version": ver.version, "status": ver.status}

    def provenance(self, name: str) -> dict:
        return self._store().provenance_chain(name)

    def evaluate(self, name: str, version: str,
                 baseline: str | None = None) -> dict:
        from air.learning.policy_eval import evaluate_policy_candidate
        ev_id, verdict, dimensions = evaluate_policy_candidate(
            self._conn, name, version, baseline_version=baseline)
        return {"evaluation_id": ev_id, "verdict": verdict.value,
                "dimensions": dimensions}


# ------------------------------------------------------------ capabilities

class CapabilityService:
    def __init__(self, conn: sqlite3.Connection,
                 runtime: Callable[[], AgentRuntime]) -> None:
        self._conn = conn
        self._runtime = runtime

    def _pipeline(self):
        from air.capabilities.pipeline import CapabilityPipeline
        rt = self._runtime()
        return CapabilityPipeline(
            self._conn,
            emit=lambda t, capability_id=None, payload=None: rt.emit(
                t, payload={"capability_id": capability_id,
                            **(payload or {})}))

    def list(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT capability_id, name, version, validation_status, confidence"
            " FROM capabilities ORDER BY updated_at DESC").fetchall()
        return [{"capability_id": r[0], "name": r[1], "version": r[2],
                 "validation_status": r[3], "confidence": r[4]} for r in rows]

    def get(self, capability_id: str) -> dict:
        from air.capabilities.store import CapabilityStore
        cap = CapabilityStore(self._conn).get(capability_id)
        if cap is None:
            raise NotFound("capability not found")
        return cap.model_dump()

    async def propose(self, name: str, description: str,
                      effect: dict | None = None,
                      created_from: str | None = None) -> dict:
        from air.capabilities.models import CapabilityEffect
        eff = CapabilityEffect(**effect) if effect else None
        cap = await self._pipeline().propose(
            name, description, effect=eff, created_from=created_from)
        return {"capability_id": cap.capability_id}

    async def promote(self, capability_id: str) -> dict:
        from air.capabilities.pipeline import GateBlocked
        try:
            cap = await self._pipeline().promote(capability_id)
        except GateBlocked as e:
            raise Blocked({"blocked": e.reasons})
        return {"capability_id": cap.capability_id,
                "status": cap.validation_status.value}

    async def rollback(self, capability_id: str) -> dict:
        from air.capabilities.pipeline import GateBlocked
        try:
            cap = await self._pipeline().rollback(capability_id)
        except GateBlocked as e:
            raise Blocked({"blocked": e.reasons})
        return {"capability_id": cap.capability_id,
                "status": cap.validation_status.value}


# ------------------------------------------------------------ experience

class ExperienceService:
    def __init__(self, conn: sqlite3.Connection,
                 runtime: Callable[[], AgentRuntime]) -> None:
        self._conn = conn
        self._runtime = runtime

    def list(self, run_id: str | None = None, limit: int = 50) -> list[dict]:
        from air.experience.recorder import ExperienceRecorder
        rec = ExperienceRecorder(self._conn)
        if run_id:
            rows = self._conn.execute(
                "SELECT id FROM experiences WHERE run_id=? ORDER BY created_at"
                " DESC LIMIT ?", (run_id, limit)).fetchall()
            return [rec.get(r[0]) for r in rows if rec.get(r[0])]
        return rec.list(limit=limit)

    def get(self, exp_id: str) -> dict:
        from air.experience.recorder import ExperienceRecorder
        exp = ExperienceRecorder(self._conn).get(exp_id)
        if exp is None:
            raise NotFound("experience not found")
        return exp

    def compare(self, experience_ids: list[str]) -> dict:
        from air.experience.recorder import ExperienceRecorder
        return ExperienceRecorder(self._conn).compare(experience_ids)

    def promote_to_knowledge(self, exp_id: str,
                             namespace: str = "global",
                             knowledge: dict | None = None) -> dict:
        from air.learning.bridge import BridgeBlocked, LearningBridge
        try:
            mem_id = LearningBridge(self._conn).promote_to_knowledge(
                exp_id, namespace, knowledge or {})
        except BridgeBlocked as e:
            raise Blocked({"blocked": e.reasons})
        return {"memory_id": mem_id}


# --------------------------------------------------------------- memory

class MemoryService:
    """Direct memory writes are operator assertions. Provenance is always
    USER_ASSERTED here: DERIVED/OBSERVED provenance is minted only by the
    learning bridge from evaluated experience."""

    def __init__(self, conn: sqlite3.Connection,
                 runtime: Callable[[], AgentRuntime]) -> None:
        self._conn = conn
        self._runtime = runtime

    def retrieve(self, namespace: str, scopes: tuple[str, ...],
                 type: str | None, q: str | None, limit: int) -> list[dict]:
        from air.memory.store import MemoryStore
        results = MemoryStore(self._conn).retrieve(
            namespace, scopes=scopes, type=type, query=q, limit=limit)
        return [r.model_dump() for r in results]

    def store(self, namespace: str, type: str, content: dict,
              scope: str = "run", importance: float = 0.5,
              confidence: float = 0.5) -> dict:
        from air.memory.store import MemoryStore
        try:
            mem = MemoryStore(self._conn).store(
                namespace, type, content, scope=scope,
                provenance="USER_ASSERTED",
                provenance_detail={"asserted_via": "api"},
                importance=importance, confidence=confidence)
        except ValueError as e:
            raise Unprocessable(str(e))
        return {"id": mem.id}

    def history(self, memory_id: str) -> list[dict]:
        from air.memory.store import MemoryStore
        chain = MemoryStore(self._conn).history(memory_id)
        if not chain:
            raise NotFound("memory not found")
        return [m.model_dump() for m in chain]

    def forget(self, memory_id: str) -> dict:
        from air.memory.store import MemoryStore
        ok = MemoryStore(self._conn).forget(memory_id)
        if not ok:
            raise NotFound("memory not found")
        return {"ok": True}


# ---------------------------------------------------- evaluation/assurance

class EvalService:
    def __init__(self, conn: sqlite3.Connection,
                 runtime: Callable[[], AgentRuntime]) -> None:
        self._conn = conn
        self._runtime = runtime

    def run(self, run_id: str, suite: dict | None = None) -> dict:
        from air.evaluation.suites import EvalCase, EvalSuite, Evaluator
        if suite:
            suite_obj = EvalSuite(
                name=suite.get("name", "ad-hoc"),
                version=suite.get("version", "1.0.0"),
                cases=[EvalCase(**c) for c in suite.get("cases", [])])
        else:
            suite_obj = EvalSuite(name="default-grounding", cases=[
                EvalCase(id="c1", name="execution evidence",
                         check="event_evidence",
                         params={"required": ["tool.completed"]}),
                EvalCase(id="c2", name="no failures", check="no_failures",
                         params={}),
                EvalCase(id="c3", name="agents completed",
                         check="agents_completed",
                         params={"min_completed": 1}),
            ])
        evaluator = Evaluator(self._conn)
        evaluator.save_suite(suite_obj)
        result = evaluator.evaluate_run(run_id, suite_obj)
        return {"evaluation_id": result.id, "verdict": result.verdict.value,
                "metrics": result.metrics}

    def get(self, evaluation_id: str) -> dict:
        row = self._conn.execute(
            "SELECT id, suite_id, subject, evaluator, evaluator_version,"
            " metrics, verdict, evidence, started_at, completed_at"
            " FROM evaluation_runs WHERE id=?", (evaluation_id,)).fetchone()
        if not row:
            raise NotFound("evaluation not found")
        keys = ["id", "suite_id", "subject", "evaluator",
                "evaluator_version", "metrics", "verdict", "evidence",
                "started_at", "completed_at"]
        out = dict(zip(keys, row))
        for k in ("subject", "metrics", "evidence"):
            out[k] = json.loads(out[k]) if out[k] else None
        return out


class AssuranceService:
    def __init__(self, conn: sqlite3.Connection,
                 runtime: Callable[[], AgentRuntime]) -> None:
        self._conn = conn
        self._runtime = runtime

    def run(self, evaluation_id: str) -> dict:
        from air.assurance.probes import AssuranceEngine
        result = AssuranceEngine(self._conn).assure(evaluation_id)
        return {"assurance_id": result.id,
                "evaluator_verdict": result.evaluator_verdict.value,
                "system_verdict": result.system_verdict.value,
                "false_accepts": result.false_accepts,
                "false_rejects": result.false_rejects}

    def get(self, assurance_id: str) -> dict:
        row = self._conn.execute(
            "SELECT id, target, probes, false_accepts, false_rejects,"
            " timeouts, evaluator_verdict, system_verdict, evidence,"
            " started_at, completed_at FROM assurance_runs WHERE id=?",
            (assurance_id,)).fetchone()
        if not row:
            raise NotFound("assurance run not found")
        keys = ["id", "target", "probes", "false_accepts", "false_rejects",
                "timeouts", "evaluator_verdict", "system_verdict",
                "evidence", "started_at", "completed_at"]
        out = dict(zip(keys, row))
        for k in ("target", "probes", "evidence"):
            out[k] = json.loads(out[k]) if out[k] else None
        return out


# --------------------------------------------------------------- models

class ModelService:
    def __init__(self, conn: sqlite3.Connection,
                 runtime: Callable[[], AgentRuntime]) -> None:
        self._conn = conn
        self._runtime = runtime

    def list(self) -> list[dict]:
        rt = self._runtime()
        out = []
        for name in rt.providers.names():
            p = rt.providers.get(name)
            caps = p.capabilities().model_dump() if p else {}
            out.append({"provider": name, "model": "configured",
                        "capabilities": caps,
                        "local": caps.get("local", False)})
        for name, reason in rt.providers.unavailable.items():
            out.append({"provider": name, "model": "unavailable",
                        "reason": reason})
        return out


# -------------------------------------------------------------- learning

class LearningService:
    def __init__(self, conn: sqlite3.Connection,
                 runtime: Callable[[], AgentRuntime]) -> None:
        self._conn = conn
        self._runtime = runtime

    def analyze(self) -> dict:
        from air.learning.engine import LearningEngine
        return LearningEngine(self._conn).analyze()

    async def propose(self) -> dict:
        from air.learning.engine import LearningEngine
        rt = self._runtime()
        engine = LearningEngine(
            self._conn,
            emit=lambda t, payload=None: rt.emit(t, payload=payload))
        proposal = await engine.propose_policy_update()
        if proposal is None:
            return {"proposed": False,
                    "reason": "insufficient evidence or no changes warranted"}
        return {"proposed": True, **proposal}


# --------------------------------------------------------------- metrics

class MetricsService:
    def __init__(self, conn: sqlite3.Connection,
                 runtime: Callable[[], AgentRuntime]) -> None:
        self._conn = conn
        self._runtime = runtime

    def get(self) -> dict:
        c = self._conn
        return {
            "runs": c.execute("SELECT COUNT(*) FROM runs").fetchone()[0],
            "agents": c.execute("SELECT COUNT(*) FROM agents").fetchone()[0],
            "events": c.execute("SELECT COUNT(*) FROM events").fetchone()[0],
            "spawns_approved": c.execute(
                "SELECT COUNT(*) FROM events"
                " WHERE type='spawn.approved'").fetchone()[0],
            "spawns_denied": c.execute(
                "SELECT COUNT(*) FROM events"
                " WHERE type='spawn.denied'").fetchone()[0],
            "tool_calls": c.execute(
                "SELECT COUNT(*) FROM tool_calls").fetchone()[0],
        }


# --------------------------------------------------------------- explain

class ExplainService:
    """Structured decision evidence. Not chain-of-thought: every field is
    read from persisted runtime state (events, audit rows, gates)."""

    def __init__(self, conn: sqlite3.Connection,
                 runtime: Callable[[], AgentRuntime]) -> None:
        self._conn = conn
        self._runtime = runtime

    def explain_run(self, run_id: str) -> dict:
        run = RunService(self._conn, self._runtime).get(run_id)
        plan = run.get("cognitive_plan") or {}
        events = EventService(self._conn).list(run_id=run_id, limit=10000)
        by_type: dict[str, int] = {}
        for e in events:
            by_type[e["type"]] = by_type.get(e["type"], 0) + 1
        return {
            "subject": "run",
            "id": run_id,
            "decision": {"status": run["status"],
                         "strategy": run["strategy"]},
            "inputs": {"goal": run["goal"], "seed": run["seed"]},
            "constraints": {"agent_budget": plan.get("agent_budget")},
            "candidate_strategies": plan.get("candidates"),
            "selected_strategy": run["strategy"],
            "scores": plan.get("scores"),
            "policy_version": plan.get("policy_version"),
            "budget_state": {"total_cost": run["total_cost"],
                             "total_tokens": run["total_tokens"]},
            "verification_state": {"error": run["error"]},
            "evidence_references": {
                "event_count": len(events),
                "event_types": by_type,
                "first_event_id": events[0]["event_id"] if events else None,
                "last_event_id": events[-1]["event_id"] if events else None,
            },
        }

    def explain_agent(self, agent_id: str) -> dict:
        agent = AgentService(self._conn, self._runtime).get(agent_id)
        return {
            "subject": "agent",
            "id": agent_id,
            "decision": {"status": agent["status"],
                         "status_reason": agent.get("status_reason")},
            "inputs": {"role": agent["role"],
                       "objective": agent["objective"],
                       "specialization": agent.get("specialization")},
            "constraints": {"memory_scope": agent.get("memory_scope"),
                            "budget": agent.get("budget")},
            "authorization_checks": {
                "capabilities": agent.get("capabilities"),
                "granted_capabilities": agent.get("granted_capabilities"),
                "tools": agent.get("tools"),
            },
            "policy_version": agent.get("policy_version"),
            "evidence_references": {
                "lineage": agent.get("lineage"),
                "parent_id": agent.get("parent_id"),
                "root_run_id": agent.get("root_run_id"),
            },
        }

    def explain_spawn_decision(self, decision_id: str) -> dict:
        rows = self._conn.execute(
            "SELECT type, payload, timestamp FROM events"
            " WHERE type IN ('spawn.requested','spawn.approved','spawn.denied')"
            " AND json_extract(payload, '$.decision_id') = ?"
            " ORDER BY rowid", (decision_id,)).fetchall()
        if not rows:
            raise NotFound("spawn decision not found")
        requested = json.loads(rows[0][1])
        return {
            "subject": "spawn_decision",
            "id": decision_id,
            "decision": requested.get("decision"),
            "inputs": requested.get("inputs"),
            "constraints": {
                "spawn_threshold": (requested.get("inputs") or {})
                .get("spawn_threshold"),
                "budget_agents_remaining": (requested.get("inputs") or {})
                .get("budget_agents_remaining"),
            },
            "scores": {
                "expected_gain": requested.get("expected_gain"),
                "estimated_cost": requested.get("estimated_cost"),
                "risk": requested.get("risk"),
            },
            "reason": requested.get("reason"),
            "evidence_references": {
                "evidence": requested.get("evidence"),
                "events": [{"type": r[0], "timestamp": r[2]} for r in rows],
            },
        }

    def explain_tool_authorization(self, call_id: str) -> dict:
        call = ToolService(self._conn, self._runtime).get_call(call_id)
        auth = call.get("authorization_decision") or {}
        return {
            "subject": "tool_call",
            "id": call_id,
            "decision": {"state": call["state"],
                         "approved": auth.get("approved")},
            "inputs": {"tool_name": call["tool_name"],
                       "capability": call["capability"],
                       "server_id": call["server_id"]},
            "authorization_checks": auth.get("checks") or auth,
            "policy_version": call.get("policy_version"),
            "budget_state": {"approval_id": call.get("approval_id")},
            "verification_state": {
                "verification_status": call.get("verification_status")},
            "evidence_references": {
                "args_hash": call.get("args_hash"),
                "provenance": call.get("provenance"),
                "run_id": call.get("run_id"),
                "agent_id": call.get("agent_id"),
            },
        }

    def explain_experience(self, exp_id: str) -> dict:
        exp = ExperienceService(self._conn, self._runtime).get(exp_id)
        memories = self._conn.execute(
            "SELECT id, type, created_at FROM memories"
            " WHERE json_extract(provenance, '$.source_experience') = ?",
            (exp_id,)).fetchall()
        return {
            "subject": "experience",
            "id": exp_id,
            "decision": {"goal": exp["goal"], "cost": exp["cost"],
                         "latency_ms": exp["latency_ms"]},
            "inputs": {"run_id": exp["run_id"],
                       "dimensions": exp.get("dimensions")},
            "verification_state": exp.get("verification"),
            "evidence_references": {
                "evaluation_refs": exp.get("evaluation_refs"),
                "assurance_refs": exp.get("assurance_refs"),
                "derived_memories": [{"id": r[0], "type": r[1],
                                      "created_at": r[2]} for r in memories],
            },
        }

    def explain_capability(self, capability_id: str) -> dict:
        cap = CapabilityService(self._conn, self._runtime).get(capability_id)
        events = self._conn.execute(
            "SELECT type, timestamp FROM events"
            " WHERE json_extract(payload, '$.capability_id') = ?"
            " ORDER BY rowid", (capability_id,)).fetchall()
        return {
            "subject": "capability",
            "id": capability_id,
            "decision": {"validation_status": cap.get("validation_status"),
                         "confidence": cap.get("confidence")},
            "inputs": {"name": cap.get("name"),
                       "description": cap.get("description")},
            "evidence_references": {
                "created_from": cap.get("created_from"),
                "validation_runs": cap.get("validation_runs"),
                "events": [{"type": r[0], "timestamp": r[1]} for r in events],
            },
        }
