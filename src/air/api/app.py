"""FastAPI application: the single backend contract for CLI and UI."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from air import __version__
from air.agents.runtime import AgentRuntime
from air.config import AirConfig
from air.events.fabric import Event
from air.persistence.db import Database

_runtime: AgentRuntime | None = None
_ws_clients: set[WebSocket] = set()


def get_runtime() -> AgentRuntime:
    global _runtime
    if _runtime is None:
        config = AirConfig.from_env()
        config.ensure_dirs()
        db = Database(config.db_path)
        db.migrate(Path(__file__).resolve().parents[2] / "migrations")
        _runtime = AgentRuntime(config, db)

        async def _fanout(event: Event) -> None:
            dead = []
            payload = event.model_dump()
            for ws in list(_ws_clients):
                try:
                    await ws.send_json(payload)
                except Exception:  # noqa: BLE001
                    dead.append(ws)
            for ws in dead:
                _ws_clients.discard(ws)

        _runtime.bus.subscribe("*", _fanout)
    return _runtime


def create_app() -> FastAPI:
    app = FastAPI(title="Adaptive Intelligence Runtime", version=__version__)

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "version": __version__}

    @app.get("/ready")
    def ready() -> dict:
        rt = get_runtime()
        ok, bad = rt.store.verify_chain()
        return {"ready": ok, "event_chain_ok": ok, "bad_event": bad}

    class RunRequest(BaseModel):
        goal: str
        strategy: str | None = None
        context: dict | None = None
        agent_budget: int | None = None
        seed: int | None = None

    @app.post("/runs")
    async def create_run(req: RunRequest) -> dict:
        from air.allocation.allocator import Strategy
        rt = get_runtime()
        strategy = Strategy(req.strategy) if req.strategy else None
        run_id = await rt.create_run(req.goal, context=req.context,
                                     strategy=strategy,
                                     agent_budget=req.agent_budget,
                                     seed=req.seed)
        asyncio.create_task(rt.start_run(run_id))
        return {"run_id": run_id}

    @app.get("/runs")
    def list_runs(limit: int = 20) -> list[dict]:
        rt = get_runtime()
        rows = rt.db.conn.execute(
            "SELECT id, goal, status, strategy, created_at, completed_at"
            " FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [{"id": r[0], "goal": r[1], "status": r[2], "strategy": r[3],
                 "created_at": r[4], "completed_at": r[5]} for r in rows]

    @app.get("/runs/{run_id}")
    def get_run(run_id: str) -> dict:
        rt = get_runtime()
        row = rt.db.conn.execute(
            "SELECT id, goal, status, strategy, cognitive_plan, seed, total_cost,"
            " total_tokens, error, final_result, created_at, started_at, completed_at"
            " FROM runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            raise HTTPException(404, "run not found")
        keys = ["id", "goal", "status", "strategy", "cognitive_plan", "seed",
                "total_cost", "total_tokens", "error", "final_result",
                "created_at", "started_at", "completed_at"]
        out = dict(zip(keys, row))
        for k in ("cognitive_plan", "final_result"):
            if out[k]:
                out[k] = json.loads(out[k])
        return out

    @app.post("/runs/{run_id}/pause")
    async def pause_run(run_id: str) -> dict:
        await get_runtime().pause_run(run_id)
        return {"ok": True}

    @app.post("/runs/{run_id}/resume")
    async def resume_run(run_id: str) -> dict:
        await get_runtime().resume_run(run_id)
        return {"ok": True}

    @app.post("/runs/{run_id}/cancel")
    async def cancel_run(run_id: str) -> dict:
        await get_runtime().cancel_run(run_id)
        return {"ok": True}

    @app.get("/runs/{run_id}/agents")
    def run_agents(run_id: str) -> list[dict]:
        rt = get_runtime()
        rows = rt.db.conn.execute(
            "SELECT id, parent_id, role, specialization, objective, model, provider,"
            " status, status_reason, created_at, terminated_at, generation"
            " FROM agents WHERE root_run_id=? ORDER BY created_at", (run_id,)).fetchall()
        keys = ["id", "parent_id", "role", "specialization", "objective",
                "model", "provider", "status", "status_reason",
                "created_at", "terminated_at", "generation"]
        return [dict(zip(keys, r)) for r in rows]

    @app.get("/runs/{run_id}/events")
    def run_events(run_id: str, limit: int = 500) -> list[dict]:
        return get_runtime().store.list(run_id=run_id, limit=limit)

    @app.get("/runs/{run_id}/graph")
    def run_graph(run_id: str) -> dict:
        """Cognitive graph: nodes (agents) + edges (lineage, messages)."""
        rt = get_runtime()
        agents = rt.db.conn.execute(
            "SELECT id, parent_id, role, status, model, provider, generation"
            " FROM agents WHERE root_run_id=?", (run_id,)).fetchall()
        nodes = [{"id": r[0], "parent_id": r[1], "role": r[2], "status": r[3],
                  "model": r[4], "provider": r[5], "generation": r[6]}
                 for r in agents]
        edges = [{"from": r[1], "to": r[0], "kind": "lineage"}
                 for r in agents if r[1]]
        msgs = rt.db.conn.execute(
            "SELECT from_agent_id, to_agent_id, channel, kind"
            " FROM agent_messages WHERE run_id=? LIMIT 500", (run_id,)).fetchall()
        for f, t, channel, kind in msgs:
            if f and t:
                edges.append({"from": f, "to": t, "kind": kind,
                              "channel": channel})
        return {"nodes": nodes, "edges": edges}

    @app.get("/runs/{run_id}/world")
    def run_world(run_id: str) -> dict:
        from air.world.state import WorldStateStore
        rt = get_runtime()
        return WorldStateStore(rt.db.conn, rt.store).build(run_id)

    @app.get("/experience")
    def list_experience(limit: int = 50) -> list[dict]:
        from air.experience.recorder import ExperienceRecorder
        return ExperienceRecorder(get_runtime().db.conn).list(limit=limit)

    @app.get("/experience/compare")
    def compare_experiences(ids: str) -> dict:
        """Compare experiences across structured dimensions: what changed
        between successful and unsuccessful runs?"""
        from air.experience.recorder import ExperienceRecorder
        return ExperienceRecorder(get_runtime().db.conn).compare(
            [i.strip() for i in ids.split(",") if i.strip()])

    @app.get("/experience/{exp_id}")
    def get_experience(exp_id: str) -> dict:
        from air.experience.recorder import ExperienceRecorder
        exp = ExperienceRecorder(get_runtime().db.conn).get(exp_id)
        if exp is None:
            raise HTTPException(404, "experience not found")
        return exp

    @app.post("/experience/{exp_id}/promote")
    def promote_experience(exp_id: str, namespace: str = "global",
                           knowledge: dict | None = None) -> dict:
        """Validated learning bridge: experience -> evaluated knowledge.
        Blocked unless the experience has SUPPORTED evaluation + SOUND
        assurance. There is no run -> memory shortcut."""
        from air.learning.bridge import BridgeBlocked, LearningBridge
        try:
            mem_id = LearningBridge(get_runtime().db.conn).promote_to_knowledge(
                exp_id, namespace, knowledge or {})
        except BridgeBlocked as e:
            raise HTTPException(409, {"blocked": e.reasons})
        return {"memory_id": mem_id}

    @app.get("/memory")
    def list_memory(namespace: str, scopes: str = "global",
                    type: str | None = None, q: str | None = None,
                    limit: int = 50) -> list[dict]:
        """Scoped retrieval. Returns memories WITH evidence metadata
        (why retrieved, trust, trust flags) so consumers know whether a
        memory is trustworthy."""
        from air.memory.store import MemoryStore
        rt = get_runtime()
        scope_tuple = tuple(s.strip() for s in scopes.split(",") if s.strip())
        results = MemoryStore(rt.db.conn).retrieve(
            namespace, scopes=scope_tuple or ("global",), type=type,
            query=q, limit=limit)
        return [r.model_dump() for r in results]

    class MemoryRequest(BaseModel):
        namespace: str
        type: str = "episodic"
        content: dict
        scope: str = "run"
        provenance: str = "DERIVED"
        provenance_detail: dict | None = None
        importance: float = 0.5
        confidence: float = 0.5
        source_run_id: str | None = None
        source_agent_id: str | None = None
        source_event_id: str | None = None

    @app.post("/memory")
    def create_memory(req: MemoryRequest) -> dict:
        from air.memory.store import MemoryStore
        try:
            mem = MemoryStore(get_runtime().db.conn).store(
                req.namespace, req.type, req.content, scope=req.scope,
                provenance=req.provenance,
                provenance_detail=req.provenance_detail or {},
                importance=req.importance, confidence=req.confidence,
                source_run_id=req.source_run_id,
                source_agent_id=req.source_agent_id,
                source_event_id=req.source_event_id)
        except ValueError as e:
            raise HTTPException(422, str(e))
        return {"id": mem.id}

    @app.get("/memory/{memory_id}/history")
    def memory_history(memory_id: str) -> list[dict]:
        from air.memory.store import MemoryStore
        chain = MemoryStore(get_runtime().db.conn).history(memory_id)
        if not chain:
            raise HTTPException(404, "memory not found")
        return [m.model_dump() for m in chain]

    @app.delete("/memory/{memory_id}")
    def delete_memory(memory_id: str) -> dict:
        from air.memory.store import MemoryStore
        ok = MemoryStore(get_runtime().db.conn).delete(memory_id)
        if not ok:
            raise HTTPException(404, "memory not found")
        return {"ok": True}

    @app.get("/agents")
    def list_agents(run_id: str | None = None, limit: int = 100) -> list[dict]:
        rt = get_runtime()
        q = ("SELECT id, parent_id, root_run_id, role, objective, status"
             " FROM agents")
        params: list = []
        if run_id:
            q += " WHERE root_run_id=?"
            params.append(run_id)
        q += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = rt.db.conn.execute(q, params).fetchall()
        return [{"id": r[0], "parent_id": r[1], "root_run_id": r[2],
                 "role": r[3], "objective": r[4], "status": r[5]} for r in rows]

    @app.get("/agents/{agent_id}")
    def get_agent(agent_id: str) -> dict:
        agent = get_runtime().get_agent(agent_id)
        if agent is None:
            raise HTTPException(404, "agent not found")
        return agent.model_dump()

    @app.post("/agents/{agent_id}/terminate")
    async def terminate_agent(agent_id: str, subtree: bool = True) -> dict:
        terminated = await get_runtime().terminate_agent(agent_id, subtree=subtree)
        return {"terminated": terminated}

    @app.get("/capabilities")
    def list_capabilities() -> list[dict]:
        rt = get_runtime()
        rows = rt.db.conn.execute(
            "SELECT capability_id, name, version, validation_status, confidence"
            " FROM capabilities ORDER BY updated_at DESC").fetchall()
        return [{"capability_id": r[0], "name": r[1], "version": r[2],
                 "validation_status": r[3], "confidence": r[4]} for r in rows]

    @app.get("/capabilities/{capability_id}")
    def get_capability(capability_id: str) -> dict:
        from air.capabilities.store import CapabilityStore
        cap = CapabilityStore(get_runtime().db.conn).get(capability_id)
        if cap is None:
            raise HTTPException(404, "capability not found")
        return cap.model_dump()

    class CapabilityRequest(BaseModel):
        name: str
        description: str
        effect: dict | None = None
        created_from: str | None = None

    @app.post("/capabilities")
    async def propose_capability(req: CapabilityRequest) -> dict:
        from air.capabilities.models import CapabilityEffect
        from air.capabilities.pipeline import CapabilityPipeline
        rt = get_runtime()
        pipeline = CapabilityPipeline(
            rt.db.conn,
            emit=lambda t, capability_id=None, payload=None: rt.emit(
                t, agent_id=None, payload={"capability_id": capability_id,
                                           **(payload or {})}))
        effect = CapabilityEffect(**req.effect) if req.effect else None
        cap = await pipeline.propose(req.name, req.description, effect=effect,
                                     created_from=req.created_from)
        return {"capability_id": cap.capability_id}

    @app.post("/capabilities/{capability_id}/promote")
    async def promote_capability(capability_id: str) -> dict:
        from air.capabilities.pipeline import CapabilityPipeline, GateBlocked
        rt = get_runtime()
        pipeline = CapabilityPipeline(
            rt.db.conn,
            emit=lambda t, capability_id=None, payload=None: rt.emit(
                t, payload={"capability_id": capability_id,
                            **(payload or {})}))
        try:
            cap = await pipeline.promote(capability_id)
        except GateBlocked as e:
            raise HTTPException(409, {"blocked": e.reasons})
        return {"capability_id": cap.capability_id,
                "status": cap.validation_status.value}

    @app.post("/capabilities/{capability_id}/rollback")
    async def rollback_capability(capability_id: str) -> dict:
        from air.capabilities.pipeline import CapabilityPipeline, GateBlocked
        rt = get_runtime()
        pipeline = CapabilityPipeline(
            rt.db.conn,
            emit=lambda t, capability_id=None, payload=None: rt.emit(
                t, payload={"capability_id": capability_id,
                            **(payload or {})}))
        try:
            cap = await pipeline.rollback(capability_id)
        except GateBlocked as e:
            raise HTTPException(409, {"blocked": e.reasons})
        return {"capability_id": cap.capability_id,
                "status": cap.validation_status.value}

    class EvaluationRequest(BaseModel):
        run_id: str
        suite: dict | None = None  # {name, version, cases:[{id,name,check,params}]}

    @app.post("/evaluations")
    def run_evaluation(req: EvaluationRequest) -> dict:
        from air.evaluation.suites import EvalCase, EvalSuite, Evaluator
        rt = get_runtime()
        if req.suite:
            suite = EvalSuite(name=req.suite.get("name", "ad-hoc"),
                              version=req.suite.get("version", "1.0.0"),
                              cases=[EvalCase(**c)
                                     for c in req.suite.get("cases", [])])
        else:
            suite = EvalSuite(name="default-grounding", cases=[
                EvalCase(id="c1", name="execution evidence",
                         check="event_evidence",
                         params={"required": ["tool.completed"]}),
                EvalCase(id="c2", name="no failures", check="no_failures",
                         params={}),
                EvalCase(id="c3", name="agents completed",
                         check="agents_completed", params={"min_completed": 1}),
            ])
        evaluator = Evaluator(rt.db.conn)
        evaluator.save_suite(suite)
        result = evaluator.evaluate_run(req.run_id, suite)
        return {"evaluation_id": result.id, "verdict": result.verdict.value,
                "metrics": result.metrics}

    @app.get("/evaluations/{evaluation_id}")
    def get_evaluation(evaluation_id: str) -> dict:
        rt = get_runtime()
        row = rt.db.conn.execute(
            "SELECT id, suite_id, subject, evaluator, evaluator_version,"
            " metrics, verdict, evidence, started_at, completed_at"
            " FROM evaluation_runs WHERE id=?", (evaluation_id,)).fetchone()
        if not row:
            raise HTTPException(404, "evaluation not found")
        keys = ["id", "suite_id", "subject", "evaluator", "evaluator_version",
                "metrics", "verdict", "evidence", "started_at", "completed_at"]
        out = dict(zip(keys, row))
        for k in ("subject", "metrics", "evidence"):
            out[k] = json.loads(out[k]) if out[k] else None
        return out

    @app.post("/assurance")
    def run_assurance(evaluation_id: str) -> dict:
        from air.assurance.probes import AssuranceEngine
        rt = get_runtime()
        result = AssuranceEngine(rt.db.conn).assure(evaluation_id)
        return {"assurance_id": result.id,
                "evaluator_verdict": result.evaluator_verdict.value,
                "system_verdict": result.system_verdict.value,
                "false_accepts": result.false_accepts,
                "false_rejects": result.false_rejects}

    @app.get("/assurance/{assurance_id}")
    def get_assurance(assurance_id: str) -> dict:
        rt = get_runtime()
        row = rt.db.conn.execute(
            "SELECT id, target, probes, false_accepts, false_rejects, timeouts,"
            " evaluator_verdict, system_verdict, evidence, started_at,"
            " completed_at FROM assurance_runs WHERE id=?",
            (assurance_id,)).fetchone()
        if not row:
            raise HTTPException(404, "assurance run not found")
        keys = ["id", "target", "probes", "false_accepts", "false_rejects",
                "timeouts", "evaluator_verdict", "system_verdict", "evidence",
                "started_at", "completed_at"]
        out = dict(zip(keys, row))
        for k in ("target", "probes", "evidence"):
            out[k] = json.loads(out[k]) if out[k] else None
        return out

    @app.get("/metrics")
    def metrics() -> dict:
        rt = get_runtime()
        c = rt.db.conn
        return {
            "runs": c.execute("SELECT COUNT(*) FROM runs").fetchone()[0],
            "agents": c.execute("SELECT COUNT(*) FROM agents").fetchone()[0],
            "events": c.execute("SELECT COUNT(*) FROM events").fetchone()[0],
            "spawns_approved": c.execute(
                "SELECT COUNT(*) FROM events WHERE type='spawn.approved'").fetchone()[0],
            "spawns_denied": c.execute(
                "SELECT COUNT(*) FROM events WHERE type='spawn.denied'").fetchone()[0],
        }

    @app.websocket("/ws")
    async def ws(ws: WebSocket) -> None:
        await ws.accept()
        _ws_clients.add(ws)
        try:
            while True:
                await ws.receive_text()  # keepalive; server pushes events
        except WebSocketDisconnect:
            pass
        finally:
            _ws_clients.discard(ws)

    return app
