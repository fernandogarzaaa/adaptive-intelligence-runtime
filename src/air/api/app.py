"""FastAPI application: projection and control surface over the AIR runtime.

The API is a projection/control surface over the runtime, never an
alternative execution path. Route handlers are thin: they validate input,
resolve services, and return service results. All business logic lives in
``air.api.services`` (command/query layer) and AIR core. No handler
creates agents, allocates budget, emits events, or asserts authority
(capability grants, verdicts, statuses, provenance, budgets,
verification state are resolved by the runtime, never by the client).

Realtime (WebSocket / SSE) is a projection of the canonical event
fabric: every message carries the full event envelope, and clients
reconnect with a ``last_event_id`` cursor to recover without loss.
"""

from __future__ import annotations

import asyncio
import functools
import json
import threading

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from air import __version__
from air.agents.runtime import AgentRuntime
from air.api.services import (
    AgentService,
    ApiError,
    ApprovalService,
    AssuranceService,
    CapabilityService,
    EvalService,
    EventService,
    ExplainService,
    ExperienceService,
    IdempotencyStore,
    LearningService,
    MemoryService,
    MetricsService,
    ModelService,
    PolicyService,
    RunService,
    ToolService,
    project_event,
)
from air.config import AirConfig
from air.events.fabric import Event
from air.persistence.db import Database, find_migrations_dir

_runtime: AgentRuntime | None = None
_runtime_lock = threading.Lock()

# Realtime projection clients: each connected socket/stream owns a bounded
# queue fed by the single canonical fanout subscriber. A full queue means
# the client cannot keep up; it is disconnected loudly (it reconnects with
# its last_event_id cursor) rather than silently losing events.
_ws_queues: dict[WebSocket, asyncio.Queue] = {}
_sse_queues: set[asyncio.Queue] = set()
_QUEUE_MAX = 10000


def get_runtime() -> AgentRuntime:
    global _runtime
    if _runtime is not None:
        return _runtime
    with _runtime_lock:
        if _runtime is not None:
            return _runtime
        config = AirConfig.from_env()
        config.ensure_dirs()
        db = Database(config.db_path)
        db.migrate(find_migrations_dir())
        _runtime = AgentRuntime(config, db)

        async def _fanout(event: Event) -> None:
            row = db.conn.execute(
                "SELECT rowid FROM events WHERE event_id=?",
                (event.event_id,)).fetchone()
            projected = project_event({
                "event_id": event.event_id, "type": event.type,
                "schema_version": event.schema_version,
                "timestamp": event.timestamp, "run_id": event.run_id,
                "agent_id": event.agent_id,
                "causation_id": event.causation_id,
                "correlation_id": event.correlation_id,
                "sequence": row[0] if row else None,
                "payload": event.payload,
            })
            dead_ws = []
            for ws, q in list(_ws_queues.items()):
                try:
                    q.put_nowait(projected)
                except asyncio.QueueFull:
                    dead_ws.append(ws)
            for ws in dead_ws:
                _ws_queues.pop(ws, None)
                try:
                    await ws.close(code=1013)
                except Exception:  # noqa: BLE001
                    pass
            dead_sse = []
            for q in list(_sse_queues):
                try:
                    q.put_nowait(projected)
                except asyncio.QueueFull:
                    dead_sse.append(q)
            for q in dead_sse:
                _sse_queues.discard(q)

        _runtime.bus.subscribe("*", _fanout)
    return _runtime


def _conn():
    return get_runtime().db.conn


def _rt() -> AgentRuntime:
    return get_runtime()


# ------------------------------------------------------------------ errors

def _register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code,
                            content={"detail": exc.detail})


# ------------------------------------------------------------ idempotency

def idempotent(fn):
    """Replay stored responses for repeated mutations carrying the same
    Idempotency-Key. A repeated POST must not spawn twice, promote twice,
    consume budget twice, or execute a tool twice."""
    @functools.wraps(fn)
    async def wrapper(request: Request, *args, **kwargs):
        key = request.headers.get("Idempotency-Key")
        if not key:
            return await fn(request, *args, **kwargs)
        store = IdempotencyStore(_conn())
        hit = store.replay(key, request.method, request.url.path)
        if hit:
            status, body = hit
            return JSONResponse(status_code=status,
                                content={**body, "idempotent": True})
        try:
            body = await fn(request, *args, **kwargs)
        except ApiError:
            raise
        store.record(key, request.method, request.url.path, 200, body)
        return body
    return wrapper


# ------------------------------------------------------------------ models
# Request models carry only client-settable fields. Authority fields
# (capability grants, verdicts, statuses, provenance, budgets consumed,
# verification state) are never accepted here; the runtime resolves them.

class RunRequest(BaseModel):
    goal: str
    context: dict | None = None
    strategy: str | None = None
    agent_budget: int | None = None
    seed: int | None = None


class AgentCreateRequest(BaseModel):
    role: str
    objective: str
    specialization: str | None = None
    capabilities: list[str] | None = None
    tools: list[str] | None = None
    model: str | None = None
    memory_scope: str = "task"


class SpawnRequest(BaseModel):
    objective: str
    role: str
    specialization: str | None = None
    capabilities: list[str] | None = None
    tools: list[str] | None = None
    model: str | None = None
    constraints: dict | None = None
    uncertainty: float = 0.5
    reason_hint: str | None = None


class MessageRequest(BaseModel):
    to_agent_id: str | None = None
    channel: str = "sibling"
    kind: str = "note"
    payload: dict = Field(default_factory=dict)


class ToolCallRequest(BaseModel):
    tool_name: str
    args: dict = Field(default_factory=dict)


class ApprovalDecision(BaseModel):
    approved: bool
    decided_by: str = "operator"


class CapabilityProposal(BaseModel):
    name: str
    description: str
    effect: dict | None = None
    created_from: str | None = None


class EvaluationRequest(BaseModel):
    run_id: str
    suite: dict | None = None


class PolicyProposal(BaseModel):
    params: dict
    reason: str


class PolicyPromotion(BaseModel):
    """The client names persisted evidence; AIR reads the verdicts.
    Caller-supplied verdict dictionaries are not accepted."""
    version: str
    evaluation_id: str
    assurance_id: str


class RollbackRequest(BaseModel):
    reason: str
    evidence: dict | None = None
    requested_by: str = "operator"


class MemoryRequest(BaseModel):
    namespace: str
    type: str = "episodic"
    content: dict
    scope: str = "run"
    importance: float = 0.5
    confidence: float = 0.5


# ------------------------------------------------------------------- app

def create_app() -> FastAPI:
    app = FastAPI(title="Adaptive Intelligence Runtime", version=__version__)
    _register_error_handlers(app)

    # ---------------------------------------------------------------- runs
    @app.post("/runs")
    @idempotent
    async def create_run(request: Request, req: RunRequest) -> dict:
        return await RunService(_conn(), _rt).start(
            req.goal, context=req.context, strategy=req.strategy,
            agent_budget=req.agent_budget, seed=req.seed)

    @app.get("/runs")
    def list_runs(limit: int = 20) -> list[dict]:
        return RunService(_conn(), _rt).list(limit=limit)

    @app.get("/runs/{run_id}")
    def get_run(run_id: str) -> dict:
        return RunService(_conn(), _rt).get(run_id)

    @app.post("/runs/{run_id}/pause")
    @idempotent
    async def pause_run(request: Request, run_id: str) -> dict:
        return await RunService(_conn(), _rt).pause(run_id)

    @app.post("/runs/{run_id}/resume")
    @idempotent
    async def resume_run(request: Request, run_id: str) -> dict:
        return await RunService(_conn(), _rt).resume(run_id)

    @app.post("/runs/{run_id}/cancel")
    @idempotent
    async def cancel_run(request: Request, run_id: str) -> dict:
        return await RunService(_conn(), _rt).cancel(run_id)

    @app.get("/runs/{run_id}/agents")
    def run_agents(run_id: str) -> list[dict]:
        return RunService(_conn(), _rt).agents(run_id)

    @app.get("/runs/{run_id}/events")
    def run_events(run_id: str, limit: int = 500,
                   after_event_id: str | None = None) -> list[dict]:
        return EventService(_conn()).list(run_id=run_id, limit=limit,
                                          after_event_id=after_event_id)

    @app.get("/runs/{run_id}/world")
    def run_world(run_id: str) -> dict:
        return RunService(_conn(), _rt).world(run_id)

    @app.get("/runs/{run_id}/explain")
    def explain_run(run_id: str) -> dict:
        return ExplainService(_conn(), _rt).explain_run(run_id)

    # ---------------------------------------------------------------- graph
    @app.get("/runs/{run_id}/graph")
    def run_graph(run_id: str) -> dict:
        agents = RunService(_conn(), _rt).agents(run_id)
        nodes = [{"id": a["id"], "role": a["role"], "status": a["status"],
                  "generation": a["generation"]} for a in agents]
        edges = [{"from": a["parent_id"], "to": a["id"]}
                 for a in agents if a["parent_id"]]
        return {"nodes": nodes, "edges": edges}

    # --------------------------------------------------------------- agents
    @app.get("/agents")
    def list_agents(run_id: str | None = None,
                    limit: int = 100) -> list[dict]:
        return AgentService(_conn(), _rt).list(run_id=run_id, limit=limit)

    @app.get("/agents/{agent_id}")
    def get_agent(agent_id: str) -> dict:
        return AgentService(_conn(), _rt).get(agent_id)

    @app.get("/agents/{agent_id}/explain")
    def explain_agent(agent_id: str) -> dict:
        return ExplainService(_conn(), _rt).explain_agent(agent_id)

    @app.post("/runs/{run_id}/agents")
    @idempotent
    async def create_agent(request: Request, run_id: str,
                           req: AgentCreateRequest) -> dict:
        return await AgentService(_conn(), _rt).create(
            run_id, req.role, req.objective,
            specialization=req.specialization,
            capabilities=req.capabilities, tools=req.tools, model=req.model,
            memory_scope=req.memory_scope)

    @app.post("/agents/{parent_id}/spawn")
    @idempotent
    async def spawn_agent(request: Request, parent_id: str,
                          req: SpawnRequest) -> dict:
        return await AgentService(_conn(), _rt).spawn(
            parent_id, req.objective, req.role,
            specialization=req.specialization,
            capabilities=req.capabilities, tools=req.tools, model=req.model,
            constraints=req.constraints, uncertainty=req.uncertainty,
            reason_hint=req.reason_hint)

    @app.post("/agents/{agent_id}/terminate")
    @idempotent
    async def terminate_agent(request: Request, agent_id: str,
                              subtree: bool = True) -> dict:
        return await AgentService(_conn(), _rt).terminate(agent_id, subtree)

    @app.post("/agents/{agent_id}/message")
    async def send_agent_message(agent_id: str, req: MessageRequest,
                                 run_id: str) -> dict:
        return await AgentService(_conn(), _rt).message(
            run_id, agent_id, req.to_agent_id, req.channel, req.kind,
            req.payload)

    @app.get("/spawn-decisions/{decision_id}")
    def explain_spawn_decision(decision_id: str) -> dict:
        return ExplainService(_conn(), _rt).explain_spawn_decision(decision_id)

    # ---------------------------------------------------------------- tools
    @app.get("/tools")
    def list_tools() -> list[dict]:
        return ToolService(_conn(), _rt).list()

    @app.post("/agents/{agent_id}/tools/call")
    @idempotent
    async def call_tool(request: Request, agent_id: str,
                        req: ToolCallRequest) -> dict:
        return await ToolService(_conn(), _rt).call(
            agent_id, req.tool_name, req.args)

    @app.get("/tool-calls")
    def list_tool_calls(run_id: str | None = None,
                        agent_id: str | None = None,
                        limit: int = 100) -> list[dict]:
        return ToolService(_conn(), _rt).list_calls(
            run_id=run_id, agent_id=agent_id, limit=limit)

    @app.get("/tool-calls/{call_id}")
    def get_tool_call(call_id: str) -> dict:
        return ToolService(_conn(), _rt).get_call(call_id)

    @app.get("/tool-calls/{call_id}/authorization")
    def explain_tool_authorization(call_id: str) -> dict:
        return ExplainService(_conn(), _rt).explain_tool_authorization(call_id)

    # ------------------------------------------------------------ approvals
    @app.get("/approvals")
    def list_approvals() -> list[dict]:
        return ApprovalService(_conn(), _rt).pending()

    @app.get("/approvals/{approval_id}")
    def get_approval(approval_id: str) -> dict:
        return ApprovalService(_conn(), _rt).get(approval_id)

    @app.post("/approvals/{approval_id}/decide")
    @idempotent
    async def decide_approval(request: Request, approval_id: str,
                              req: ApprovalDecision) -> dict:
        return await ApprovalService(_conn(), _rt).decide(
            approval_id, req.approved, req.decided_by)

    # ---------------------------------------------------------- experience
    @app.get("/experience")
    def list_experience(run_id: str | None = None,
                        limit: int = 50) -> list[dict]:
        return ExperienceService(_conn(), _rt).list(run_id=run_id, limit=limit)

    @app.get("/experience/compare")
    def compare_experiences(ids: str) -> dict:
        """Compare experiences across structured dimensions: what changed
        between successful and unsuccessful runs?"""
        return ExperienceService(_conn(), _rt).compare(
            [i.strip() for i in ids.split(",") if i.strip()])

    @app.get("/experience/{exp_id}")
    def get_experience(exp_id: str) -> dict:
        return ExperienceService(_conn(), _rt).get(exp_id)

    @app.get("/experiences/{exp_id}/lineage")
    def explain_experience(exp_id: str) -> dict:
        return ExplainService(_conn(), _rt).explain_experience(exp_id)

    @app.post("/experience/{exp_id}/promote")
    @idempotent
    async def promote_experience(request: Request, exp_id: str,
                                 namespace: str = "global",
                                 knowledge: dict | None = None) -> dict:
        """Validated learning bridge: experience -> evaluated knowledge.
        Blocked unless the experience has SUPPORTED evaluation + SOUND
        assurance. There is no run -> memory shortcut."""
        return ExperienceService(_conn(), _rt).promote_to_knowledge(
            exp_id, namespace, knowledge or {})

    # --------------------------------------------------------------- memory
    @app.get("/memory")
    def list_memory(namespace: str, scopes: str = "global",
                    type: str | None = None, q: str | None = None,
                    limit: int = 50) -> list[dict]:
        """Scoped retrieval. Returns memories WITH evidence metadata
        (why retrieved, trust, trust flags) so consumers know whether a
        memory is trustworthy."""
        scope_tuple = tuple(s.strip() for s in scopes.split(",") if s.strip())
        return MemoryService(_conn(), _rt).retrieve(
            namespace, scopes=scope_tuple or ("global",), type=type, q=q,
            limit=limit)

    @app.post("/memory")
    def create_memory(req: MemoryRequest) -> dict:
        """Direct writes are operator assertions (USER_ASSERTED). Evaluated
        knowledge enters only through the learning bridge."""
        return MemoryService(_conn(), _rt).store(
            req.namespace, req.type, req.content, scope=req.scope,
            importance=req.importance, confidence=req.confidence)

    @app.get("/memory/{memory_id}/history")
    def memory_history(memory_id: str) -> list[dict]:
        return MemoryService(_conn(), _rt).history(memory_id)

    @app.delete("/memory/{memory_id}")
    def delete_memory(memory_id: str) -> dict:
        return MemoryService(_conn(), _rt).forget(memory_id)

    # ---------------------------------------------------------- capabilities
    @app.get("/capabilities")
    def list_capabilities() -> list[dict]:
        return CapabilityService(_conn(), _rt).list()

    @app.get("/capabilities/{capability_id}")
    def get_capability(capability_id: str) -> dict:
        return CapabilityService(_conn(), _rt).get(capability_id)

    @app.get("/capabilities/{capability_id}/provenance")
    def explain_capability(capability_id: str) -> dict:
        return ExplainService(_conn(), _rt).explain_capability(capability_id)

    @app.post("/capabilities")
    @idempotent
    async def propose_capability(request: Request,
                                 req: CapabilityProposal) -> dict:
        if req.created_from:
            try:
                ExperienceService(_conn(), _rt).get(req.created_from)
            except ApiError:
                raise HTTPException(
                    422, "created_from references unknown experience:"
                        f" {req.created_from}")
        return await CapabilityService(_conn(), _rt).propose(
            req.name, req.description, effect=req.effect,
            created_from=req.created_from)

    @app.post("/capabilities/{capability_id}/promote")
    @idempotent
    async def promote_capability(request: Request,
                                 capability_id: str) -> dict:
        return await CapabilityService(_conn(), _rt).promote(capability_id)

    @app.post("/capabilities/{capability_id}/rollback")
    @idempotent
    async def rollback_capability(request: Request,
                                  capability_id: str) -> dict:
        return await CapabilityService(_conn(), _rt).rollback(capability_id)

    # ---------------------------------------------------- evaluation/assurance
    @app.post("/evaluations")
    @idempotent
    async def run_evaluation(request: Request,
                             req: EvaluationRequest) -> dict:
        return EvalService(_conn(), _rt).run(req.run_id, req.suite)

    @app.get("/evaluations/{evaluation_id}")
    def get_evaluation(evaluation_id: str) -> dict:
        return EvalService(_conn(), _rt).get(evaluation_id)

    @app.post("/assurance")
    @idempotent
    async def run_assurance(request: Request, evaluation_id: str) -> dict:
        return AssuranceService(_conn(), _rt).run(evaluation_id)

    @app.get("/assurance/{assurance_id}")
    def get_assurance(assurance_id: str) -> dict:
        return AssuranceService(_conn(), _rt).get(assurance_id)

    # --------------------------------------------------------------- models
    @app.get("/models")
    def list_models() -> list[dict]:
        return ModelService(_conn(), _rt).list()

    # -------------------------------------------------------------- policies
    @app.get("/policies")
    def list_policies() -> list[dict]:
        return PolicyService(_conn(), _rt).list()

    @app.get("/policies/{name}")
    def get_policy(name: str) -> dict:
        return PolicyService(_conn(), _rt).get(name)

    @app.post("/policies/{name}/propose")
    @idempotent
    async def propose_policy(request: Request, name: str,
                             req: PolicyProposal) -> dict:
        return await PolicyService(_conn(), _rt).propose(
            name, req.params, req.reason)

    @app.post("/policies/{name}/promote")
    @idempotent
    async def promote_policy(request: Request, name: str,
                             req: PolicyPromotion) -> dict:
        return await PolicyService(_conn(), _rt).promote(
            name, req.version, req.evaluation_id, req.assurance_id)

    @app.post("/policies/{name}/rollback")
    @idempotent
    async def rollback_policy(request: Request, name: str) -> dict:
        return await PolicyService(_conn(), _rt).rollback(name)

    @app.post("/policies/{name}/rollback/request")
    @idempotent
    async def request_rollback(request: Request, name: str,
                               req: RollbackRequest) -> dict:
        return await PolicyService(_conn(), _rt).request_rollback(
            name, req.reason, req.evidence, req.requested_by)

    @app.post("/policies/rollback/{rollback_id}/approve")
    @idempotent
    async def approve_rollback(request: Request, rollback_id: str,
                               approved_by: str = "operator") -> dict:
        return await PolicyService(_conn(), _rt).approve_rollback(
            rollback_id, approved_by)

    @app.get("/policies/{name}/provenance")
    def policy_provenance(name: str) -> dict:
        """Why is this policy active? Complete provenance chain."""
        return PolicyService(_conn(), _rt).provenance(name)

    @app.post("/policies/{name}/evaluate")
    @idempotent
    async def evaluate_policy(request: Request, name: str, version: str,
                              baseline: str | None = None) -> dict:
        """Multi-dimensional guardrailed evaluation of a policy candidate."""
        return PolicyService(_conn(), _rt).evaluate(name, version, baseline)

    # -------------------------------------------------------------- learning
    @app.post("/learning/analyze")
    def learning_analyze() -> dict:
        return LearningService(_conn(), _rt).analyze()

    @app.post("/learning/propose")
    @idempotent
    async def learning_propose(request: Request) -> dict:
        return await LearningService(_conn(), _rt).propose()

    # --------------------------------------------------------------- metrics
    @app.get("/metrics")
    def metrics() -> dict:
        return MetricsService(_conn(), _rt).get()

    # ---------------------------------------------------------------- health
    @app.get("/health")
    def health() -> dict:
        return {"ok": True, "version": __version__}

    @app.get("/ready")
    def ready() -> dict:
        rt = get_runtime()
        ok, bad = rt.store.verify_chain()
        return {"ok": ok, "bad_event": bad}

    # -------------------------------------------------------------- realtime
    @app.websocket("/ws/events")
    async def ws_events(ws: WebSocket,
                        last_event_id: str | None = None) -> None:
        """Event-fabric projection. Replays every event after
        ``last_event_id``, then streams live events with the full
        canonical envelope. Reconnect with the last received event_id to
        converge without loss."""
        await ws.accept()
        rt = get_runtime()
        es = EventService(rt.db.conn)
        try:
            if last_event_id:
                for e in es.list(limit=10000, after_event_id=last_event_id):
                    await ws.send_json(project_event(e))
            else:
                await ws.send_json({"event_type": "hello",
                                    "last_event_id": es.latest_event_id()})
            q: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_MAX)
            _ws_queues[ws] = q
            while True:
                projected = await q.get()
                await ws.send_json(projected)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            _ws_queues.pop(ws, None)

    @app.get("/events/stream")
    async def sse_events(request: Request,
                         last_event_id: str | None = None) -> StreamingResponse:
        """Server-sent projection of the same canonical event fabric."""
        rt = get_runtime()
        es = EventService(rt.db.conn)

        async def gen():
            q: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_MAX)
            _sse_queues.add(q)
            try:
                if last_event_id:
                    for e in es.list(limit=10000,
                                     after_event_id=last_event_id):
                        yield (f"id: {e['event_id']}\n"
                               f"event: {e['type']}\n"
                               f"data: {json.dumps(project_event(e))}\n\n")
                else:
                    yield (f"event: hello\ndata: "
                           f"{json.dumps({'last_event_id': es.latest_event_id()})}\n\n")
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        projected = await asyncio.wait_for(q.get(), 15.0)
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
                        continue
                    yield (f"id: {projected['event_id']}\n"
                           f"event: {projected['event_type']}\n"
                           f"data: {json.dumps(projected)}\n\n")
            finally:
                _sse_queues.discard(q)

        return StreamingResponse(gen(), media_type="text/event-stream")

    return app
