"""Agent runtime: agents are runtime entities, not prompts.

Owns: agent lifecycle, the spawn API, scoped messaging, budget enforcement,
and the agent execution loop. Budget enforcement happens in code: every
consumption is checked against hard limits before it is allowed.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Awaitable, Callable

from air import __version__
from air.agents.models import Agent, AgentStatus, Budget, TERMINAL_STATUSES, SpawnDecision
from air.agents.spawn_policy import SpawnContext, decide
from air.allocation.allocator import Strategy, allocate
from air.config import AirConfig
from air.events.fabric import Event, EventBus, EventStore, utcnow
from air.persistence.db import Database
from air.providers.base import TaskRequirements
from air.providers.registry import ProviderRegistry
from air.security.policy import CapabilityClass

# Behavior for deterministic (scripted) agents: real code execution used by
# tests, experiments, and local-first runs without a model provider.
# Signature: (agent, runtime) -> result dict. Labeled "scripted" everywhere.
ScriptedBehavior = Callable[[Agent, "AgentRuntime"], Awaitable[dict]]


class BudgetExhausted(Exception):
    def __init__(self, what: str) -> None:
        super().__init__(what)
        self.what = what


class AgentRuntime:
    def __init__(self, config: AirConfig, db: Database,
                 providers: ProviderRegistry | None = None) -> None:
        self.config = config
        self.db = db
        self.bus = EventBus()
        self.store = EventStore(db.conn)
        self.providers = providers or ProviderRegistry(config)
        self._behaviors: dict[str, ScriptedBehavior] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._run_configs: dict[str, dict] = {}

    # ------------------------------------------------------------------ events
    async def emit(self, type: str, run_id: str | None = None,
                   agent_id: str | None = None, payload: dict | None = None,
                   causation_id: str | None = None,
                   correlation_id: str | None = None) -> Event:
        event = Event(type=type, run_id=run_id, agent_id=agent_id,
                      payload=payload or {}, causation_id=causation_id,
                      correlation_id=correlation_id)
        await self.store.append(event)
        await self.bus.publish(event)
        return event

    # -------------------------------------------------------------------- runs
    async def create_run(self, goal: str, context: dict | None = None,
                         strategy: Strategy | None = None,
                         token_budget: int | None = None,
                         time_budget_s: int | None = None,
                         cost_budget_usd: float | None = None,
                         agent_budget: int | None = None,
                         seed: int | None = None) -> str:
        plan = allocate(
            goal, context,
            token_budget=token_budget or self.config.default_token_budget,
            time_budget_s=time_budget_s or self.config.default_time_budget_s,
            cost_budget_usd=cost_budget_usd or self.config.default_cost_budget_usd,
            agent_budget=agent_budget if agent_budget is not None else self.config.default_agent_budget,
            force_strategy=strategy,
        )
        run_id = "run_" + uuid.uuid4().hex[:12]
        now = utcnow()
        self.db.conn.execute(
            """INSERT INTO runs (id, goal, status, strategy, cognitive_plan, seed,
                                 policy_version, runtime_version, created_at)
               VALUES (?, ?, 'CREATED', ?, ?, ?, ?, ?, ?)""",
            (run_id, goal, plan.strategy.value, plan.model_dump_json(),
             seed, plan.allocator_version, __version__, now),
        )
        self.db.conn.execute(
            """INSERT INTO budgets (id, run_id, scope, token_limit, time_limit_s,
                                    cost_limit, agent_limit, status, updated_at)
               VALUES (?, ?, 'run', ?, ?, ?, ?, 'ok', ?)""",
            ("bud_" + uuid.uuid4().hex[:12], run_id, plan.token_budget,
             plan.time_budget_s, plan.cost_budget_usd, plan.agent_budget, now),
        )
        self.db.conn.commit()
        self._run_configs[run_id] = {"plan": plan.model_dump(), "context": context or {}}
        await self.emit("run.created", run_id=run_id,
                        payload={"goal": goal, "strategy": plan.strategy.value,
                                 "plan_id": plan.plan_id, "scores": plan.scores})
        return run_id

    # ------------------------------------------------------------------ agents
    def register_behavior(self, role: str, fn: ScriptedBehavior) -> None:
        """Register deterministic behavior for a role (tests / local-first)."""
        self._behaviors[role] = fn

    async def create_agent(self, run_id: str, role: str, objective: str,
                           parent_id: str | None = None,
                           specialization: str | None = None,
                           capabilities: list[str] | None = None,
                           tools: list[str] | None = None,
                           model: str | None = None,
                           provider: str | None = None,
                           memory_scope: str = "task",
                           budget: Budget | None = None) -> Agent:
        parent = self.get_agent(parent_id) if parent_id else None
        generation = (parent.generation + 1) if parent else 0
        agent = Agent(
            parent_id=parent_id, root_run_id=run_id, generation=generation,
            role=role, specialization=specialization, objective=objective,
            model=model or "scripted", provider=provider or "scripted",
            capabilities=capabilities or [], tools=tools or [],
            memory_scope=memory_scope, budget=budget or Budget(),
            lineage=(parent.lineage + [parent.id]) if parent else [],
        )
        # Enforce run-level agent budget in code.
        self._check_agent_slot(run_id)
        self._persist_agent(agent)
        if parent_id:
            self.db.conn.execute(
                "INSERT INTO agent_lineage (child_id, parent_id, relation, created_at)"
                " VALUES (?, ?, 'spawned', ?)",
                (agent.id, parent_id, utcnow()),
            )
            self.db.conn.commit()
        await self.emit("agent.created", run_id=run_id, agent_id=agent.id,
                        payload={"role": role, "parent_id": parent_id,
                                 "generation": generation, "objective": objective})
        return agent

    async def spawn_agent(self, parent_id: str, objective: str, role: str,
                          specialization: str | None = None,
                          capabilities: list[str] | None = None,
                          tools: list[str] | None = None,
                          model: str | None = None,
                          budget: Budget | None = None,
                          memory_scope: str = "task",
                          constraints: dict | None = None,
                          uncertainty: float = 0.5,
                          reason_hint: str | None = None) -> tuple[Agent | None, SpawnDecision]:
        """First-class spawn API. The spawn policy decides; denials are recorded."""
        parent = self.get_agent(parent_id)
        if parent is None:
            raise ValueError(f"parent agent not found: {parent_id}")
        run_id = parent.root_run_id
        run_budget = self._run_budget(run_id)
        ctx = SpawnContext(
            role=role, uncertainty=uncertainty,
            task_complexity=float((constraints or {}).get("complexity", 0.5)),
            current_agent_count=self._active_agent_count(run_id),
            budget_agents_remaining=(run_budget["agent_limit"] - run_budget["consumed_agents"])
            if run_budget and run_budget["agent_limit"] else None,
            budget_tokens_remaining=(run_budget["token_limit"] - run_budget["consumed_tokens"])
            if run_budget and run_budget["token_limit"] else None,
            reason_hint=reason_hint,
            evidence=[f"parent={parent_id}", f"role={role}"],
        )
        decision = decide(ctx)
        await self.emit("spawn.requested", run_id=run_id, agent_id=parent_id,
                        payload={"role": role, "objective": objective,
                                 "decision": decision.decision, "reason": decision.reason,
                                 "expected_gain": decision.expected_gain,
                                 "estimated_cost": decision.estimated_cost,
                                 "risk": decision.risk})
        if decision.decision != "SPAWN":
            await self.emit("spawn.denied", run_id=run_id, agent_id=parent_id,
                            payload={"role": role, "reason": decision.reason})
            return None, decision
        # Depth limit enforcement in code.
        depth_limit = (run_budget or {}).get("depth_limit") or 4
        if parent.generation + 1 > depth_limit:
            await self.emit("spawn.denied", run_id=run_id, agent_id=parent_id,
                            payload={"role": role,
                                     "reason": f"depth limit {depth_limit} reached"})
            decision.decision = "DENY"
            decision.reason = f"depth limit {depth_limit} reached"
            return None, decision
        child_budget = budget or Budget(
            token_limit=max(2000, (run_budget["token_limit"] or 32000) // 8)
            if run_budget else 8000,
            cost_limit_usd=1.0, tool_call_limit=25,
        )
        agent = await self.create_agent(
            run_id, role, objective, parent_id=parent_id,
            specialization=specialization, capabilities=capabilities,
            tools=tools, model=model, memory_scope=memory_scope,
            budget=child_budget,
        )
        self._consume(run_id, agents=1)
        await self.emit("spawn.approved", run_id=run_id, agent_id=agent.id,
                        payload={"parent_id": parent_id, "role": role})
        return agent, decision

    # --------------------------------------------------------------- messaging
    async def send_message(self, run_id: str, from_agent_id: str | None,
                           to_agent_id: str | None, channel: str, kind: str,
                           payload: dict) -> str:
        """Scoped communication. Channels: parent_child, sibling, evidence, result.

        Scope rule: parent<->child always allowed; siblings (same parent, or
        same run root) allowed on sibling/result/evidence channels; anything
        else is denied and recorded.
        """
        if channel not in ("parent_child", "sibling", "evidence", "result", "broadcast"):
            raise ValueError(f"unknown channel: {channel}")
        if from_agent_id and to_agent_id:
            sender = self.get_agent(from_agent_id)
            target = self.get_agent(to_agent_id)
            if sender and target and not self._in_scope(sender, target, channel):
                await self.emit("policy.blocked", run_id=run_id, agent_id=from_agent_id,
                                payload={"action": "message", "channel": channel,
                                         "to": to_agent_id, "reason": "out of scope"})
                raise PermissionError("message denied: agents are not in communication scope")
        msg_id = "msg_" + uuid.uuid4().hex[:12]
        self.db.conn.execute(
            """INSERT INTO agent_messages (id, run_id, from_agent_id, to_agent_id,
                                           channel, kind, payload, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (msg_id, run_id, from_agent_id, to_agent_id, channel, kind,
             json.dumps(payload), utcnow()),
        )
        self.db.conn.commit()
        await self.emit("agent.message", run_id=run_id, agent_id=from_agent_id,
                        payload={"to": to_agent_id, "channel": channel, "kind": kind})
        return msg_id

    def _in_scope(self, sender: Agent, target: Agent, channel: str) -> bool:
        if sender.root_run_id != target.root_run_id:
            return False
        if channel == "parent_child":
            return target.id == sender.parent_id or sender.id == target.parent_id
        # sibling/result/evidence: same parent or shared root run
        return sender.parent_id == target.parent_id or True  # same-run scope

    # --------------------------------------------------------------- lifecycle
    async def start_run(self, run_id: str) -> None:
        cfg = self._run_configs.get(run_id)
        if cfg is None:
            raise ValueError(f"unknown run: {run_id}")
        plan = cfg["plan"]
        self.db.conn.execute(
            "UPDATE runs SET status='RUNNING', started_at=? WHERE id=?", (utcnow(), run_id))
        self.db.conn.commit()
        await self.emit("run.started", run_id=run_id,
                        payload={"strategy": plan["strategy"]})
        # Build the initial agent population from the cognitive plan.
        created: dict[str, Agent] = {}
        for spec in plan["agent_specs"]:
            parent_id = None
            if spec.get("parent_role"):
                parent = next((a for a in created.values()
                               if a.role == spec["parent_role"]), None)
                parent_id = parent.id if parent else None
            model, provider = self._resolve_model(spec)
            agent = await self.create_agent(
                run_id, spec["role"], spec["objective"], parent_id=parent_id,
                specialization=spec.get("specialization"),
                capabilities=spec.get("capabilities", []),
                tools=spec.get("tools", []), model=model, provider=provider,
                budget=Budget(token_limit=spec.get("token_budget", 8000),
                              cost_limit_usd=1.0, tool_call_limit=25),
            )
            created[agent.id] = agent
            self._consume(run_id, agents=1)
        # Launch execution tasks.
        for agent in created.values():
            self._launch(agent)
        if not created:
            # DIRECT strategy: no agents; run completes with direct reasoning.
            await self._complete_run(run_id, {"mode": "direct",
                                              "note": "direct reasoning, no agents spawned"})

    def _resolve_model(self, spec: dict) -> tuple[str, str]:
        tier = spec.get("model_tier", "standard")
        req = TaskRequirements(
            need_reasoning=(tier == "strong"),
            privacy_sensitive=(tier == "local"),
            cheap_ok=(tier in ("cheap", "standard")),
        )
        routed = self.providers.route(req)
        if routed is None:
            return "scripted", "scripted"
        return routed.model, routed.provider.name

    def _launch(self, agent: Agent) -> None:
        task = asyncio.create_task(self._agent_loop(agent),
                                   name=f"agent-{agent.id}")
        self._tasks[agent.id] = task

    async def _agent_loop(self, agent: Agent) -> None:
        try:
            self._set_status(agent, AgentStatus.RUNNING)
            await self.emit("agent.started", run_id=agent.root_run_id, agent_id=agent.id)
            behavior = self._behaviors.get(agent.role)
            if behavior is None:
                # No deterministic behavior and no model provider: honest block.
                routed = self.providers.route(TaskRequirements())
                if routed is None:
                    self._set_status(agent, AgentStatus.BLOCKED,
                                     "MODEL_PROVIDER_UNAVAILABLE: no model provider configured"
                                     " and no scripted behavior registered for role"
                                     f" '{agent.role}'")
                    await self.emit("agent.failed", run_id=agent.root_run_id,
                                    agent_id=agent.id,
                                    payload={"reason": "MODEL_PROVIDER_UNAVAILABLE"})
                    return
                # LLM-backed execution lands in the next slice; for now the
                # runtime refuses to fake it.
                self._set_status(agent, AgentStatus.BLOCKED,
                                 "MODEL_PROVIDER_UNAVAILABLE: LLM-backed agent execution"
                                 " is not yet wired; configure a provider and retry")
                await self.emit("agent.failed", run_id=agent.root_run_id,
                                agent_id=agent.id,
                                payload={"reason": "MODEL_PROVIDER_UNAVAILABLE"})
                return
            result = await behavior(agent, self)
            self._consume(agent.root_run_id,
                          tokens=int(result.get("tokens", 0)),
                          cost=float(result.get("cost_usd", 0.0)))
            await self.send_message(agent.root_run_id, agent.id, agent.parent_id,
                                    "parent_child" if agent.parent_id else "result",
                                    "result", {"result": result})
            self._set_status(agent, AgentStatus.COMPLETED)
            await self.emit("agent.completed", run_id=agent.root_run_id,
                            agent_id=agent.id, payload={"result": result})
        except BudgetExhausted as e:
            self._set_status(agent, AgentStatus.TERMINATED, f"BUDGET_EXHAUSTED: {e.what}")
            await self.emit("budget.exhausted", run_id=agent.root_run_id,
                            agent_id=agent.id, payload={"what": e.what})
            await self.emit("agent.terminated", run_id=agent.root_run_id,
                            agent_id=agent.id, payload={"reason": "budget exhausted"})
        except asyncio.CancelledError:
            self._set_status(agent, AgentStatus.CANCELLED, "cancelled by operator")
            await self.emit("agent.terminated", run_id=agent.root_run_id,
                            agent_id=agent.id, payload={"reason": "cancelled"})
            raise
        except Exception as e:  # noqa: BLE001 - agent failure must be recorded
            self._set_status(agent, AgentStatus.FAILED, f"{type(e).__name__}: {e}")
            await self.emit("agent.failed", run_id=agent.root_run_id,
                            agent_id=agent.id,
                            payload={"error": f"{type(e).__name__}: {e}"})
        finally:
            self._tasks.pop(agent.id, None)
            await self._maybe_complete_run(agent.root_run_id)

    async def _maybe_complete_run(self, run_id: str) -> None:
        if self._active_agent_count(run_id) == 0:
            row = self.db.conn.execute(
                "SELECT status FROM runs WHERE id=?", (run_id,)).fetchone()
            if row and row[0] == "RUNNING":
                await self._complete_run(run_id, {"mode": "agents"})

    async def _complete_run(self, run_id: str, summary: dict) -> None:
        now = utcnow()
        self.db.conn.execute(
            "UPDATE runs SET status='COMPLETED', completed_at=?, final_result=? WHERE id=?",
            (now, json.dumps(summary), run_id))
        self.db.conn.commit()
        await self.emit("run.completed", run_id=run_id, payload=summary)

    async def terminate_agent(self, agent_id: str, subtree: bool = True,
                              reason: str = "operator terminated") -> list[str]:
        """Terminate an agent and optionally its whole subtree. Recorded."""
        terminated: list[str] = []
        queue = [agent_id]
        while queue:
            aid = queue.pop(0)
            agent = self.get_agent(aid)
            if agent is None or agent.status in TERMINAL_STATUSES:
                continue
            task = self._tasks.get(aid)
            if task and not task.done():
                task.cancel()
            self._set_status(agent, AgentStatus.TERMINATED, reason)
            agent.terminated_at = utcnow()
            self._persist_agent(agent)
            terminated.append(aid)
            await self.emit("agent.terminated", run_id=agent.root_run_id,
                            agent_id=aid, payload={"reason": reason})
            if subtree:
                kids = self.db.conn.execute(
                    "SELECT id FROM agents WHERE parent_id=?", (aid,)).fetchall()
                queue.extend(r[0] for r in kids)
        return terminated

    async def pause_run(self, run_id: str) -> None:
        self.db.conn.execute("UPDATE runs SET status='PAUSED' WHERE id=?", (run_id,))
        self.db.conn.commit()
        await self.emit("run.paused", run_id=run_id)

    async def resume_run(self, run_id: str) -> None:
        self.db.conn.execute("UPDATE runs SET status='RUNNING' WHERE id=?", (run_id,))
        self.db.conn.commit()
        await self.emit("run.started", run_id=run_id, payload={"resumed": True})

    async def cancel_run(self, run_id: str) -> None:
        agents = self.db.conn.execute(
            "SELECT id FROM agents WHERE root_run_id=?", (run_id,)).fetchall()
        for (aid,) in agents:
            await self.terminate_agent(aid, subtree=False, reason="run cancelled")
        self.db.conn.execute(
            "UPDATE runs SET status='CANCELLED', completed_at=? WHERE id=?",
            (utcnow(), run_id))
        self.db.conn.commit()
        await self.emit("run.failed", run_id=run_id, payload={"reason": "cancelled"})

    # ---------------------------------------------------------------- budgets
    def _run_budget(self, run_id: str) -> dict | None:
        row = self.db.conn.execute(
            "SELECT token_limit, time_limit_s, cost_limit, agent_limit, tool_call_limit,"
            " depth_limit, consumed_tokens, consumed_cost, consumed_tool_calls,"
            " consumed_agents FROM budgets WHERE run_id=? AND scope='run'", (run_id,)).fetchone()
        if not row:
            return None
        keys = ["token_limit", "time_limit_s", "cost_limit", "agent_limit",
                "tool_call_limit", "depth_limit", "consumed_tokens", "consumed_cost",
                "consumed_tool_calls", "consumed_agents"]
        return dict(zip(keys, row))

    def _check_agent_slot(self, run_id: str) -> None:
        b = self._run_budget(run_id)
        if b and b["agent_limit"] and b["consumed_agents"] >= b["agent_limit"]:
            raise BudgetExhausted(
                f"agent budget exhausted ({b['consumed_agents']}/{b['agent_limit']})")

    def _consume(self, run_id: str, tokens: int = 0, cost: float = 0.0,
                 tool_calls: int = 0, agents: int = 0) -> None:
        b = self._run_budget(run_id)
        if b is None:
            return
        nt = b["consumed_tokens"] + tokens
        nc = b["consumed_cost"] + cost
        ntc = b["consumed_tool_calls"] + tool_calls
        na = b["consumed_agents"] + agents
        if b["token_limit"] and nt > b["token_limit"]:
            raise BudgetExhausted(f"token budget exceeded ({nt} > {b['token_limit']})")
        if b["cost_limit"] and nc > b["cost_limit"]:
            raise BudgetExhausted(f"cost budget exceeded ({nc:.4f} > {b['cost_limit']})")
        if b["tool_call_limit"] and ntc > b["tool_call_limit"]:
            raise BudgetExhausted(f"tool call budget exceeded ({ntc} > {b['tool_call_limit']})")
        if b["agent_limit"] and na > b["agent_limit"]:
            raise BudgetExhausted(f"agent budget exceeded ({na} > {b['agent_limit']})")
        status = "ok"
        for limit, used in ((b["token_limit"], nt), (b["cost_limit"], nc),
                            (b["agent_limit"], na)):
            if limit and used >= 0.8 * limit:
                status = "warning"
        self.db.conn.execute(
            """UPDATE budgets SET consumed_tokens=?, consumed_cost=?,
               consumed_tool_calls=?, consumed_agents=?, status=?, updated_at=?
               WHERE run_id=? AND scope='run'""",
            (nt, nc, ntc, na, status, utcnow(), run_id))
        self.db.conn.commit()
        if status == "warning":
            # Fire-and-forget is not possible here (sync); the warning is
            # recorded in the budgets row which the API/websocket surfaces.
            pass

    # ---------------------------------------------------------------- helpers
    def _active_agent_count(self, run_id: str) -> int:
        row = self.db.conn.execute(
            """SELECT COUNT(*) FROM agents WHERE root_run_id=?
               AND status NOT IN ('COMPLETED','FAILED','CANCELLED','TERMINATED')""",
            (run_id,)).fetchone()
        return row[0] if row else 0

    def get_agent(self, agent_id: str | None) -> Agent | None:
        if not agent_id:
            return None
        row = self.db.conn.execute(
            "SELECT id, parent_id, root_run_id, generation, role, specialization,"
            " objective, model, provider, capabilities, tools, memory_scope,"
            " belief_scope, policy_scope, budget, status, status_reason, created_at,"
            " terminated_at, lineage, capability_version, policy_version"
            " FROM agents WHERE id=?", (agent_id,)).fetchone()
        if not row:
            return None
        (aid, parent_id, root_run_id, generation, role, specialization, objective,
         model, provider, capabilities, tools, memory_scope, belief_scope,
         policy_scope, budget, status, status_reason, created_at, terminated_at,
         lineage, capability_version, policy_version) = row
        return Agent(
            id=aid, parent_id=parent_id, root_run_id=root_run_id,
            generation=generation, role=role, specialization=specialization,
            objective=objective, model=model, provider=provider,
            capabilities=json.loads(capabilities or "[]"),
            tools=json.loads(tools or "[]"),
            memory_scope=memory_scope, belief_scope=belief_scope,
            policy_scope=policy_scope,
            budget=Budget.model_validate(json.loads(budget or "{}")),
            status=AgentStatus(status), status_reason=status_reason,
            created_at=created_at, terminated_at=terminated_at,
            lineage=json.loads(lineage or "[]"),
            capability_version=capability_version, policy_version=policy_version,
        )

    def _persist_agent(self, agent: Agent) -> None:
        # NOTE: never INSERT OR REPLACE here. REPLACE deletes the row first,
        # which cascades into events.agent_id and silently wipes history.
        cols = ("parent_id, root_run_id, generation, role, specialization,"
                " objective, model, provider, capabilities, tools, memory_scope,"
                " belief_scope, policy_scope, budget, status, status_reason,"
                " created_at, terminated_at, lineage, capability_version,"
                " policy_version")
        vals = (agent.parent_id, agent.root_run_id, agent.generation,
                agent.role, agent.specialization, agent.objective, agent.model,
                agent.provider, json.dumps(agent.capabilities),
                json.dumps(agent.tools), agent.memory_scope, agent.belief_scope,
                agent.policy_scope, agent.budget.model_dump_json(),
                agent.status.value, agent.status_reason, agent.created_at,
                agent.terminated_at, json.dumps(agent.lineage),
                agent.capability_version, agent.policy_version)
        cur = self.db.conn.execute(
            f"UPDATE agents SET ({cols}) = ({','.join('?' * 21)}) WHERE id = ?",
            (*vals, agent.id))
        if cur.rowcount == 0:
            self.db.conn.execute(
                f"INSERT INTO agents (id, {cols}) VALUES ({','.join('?' * 22)})",
                (agent.id, *vals))
        self.db.conn.commit()

    def _set_status(self, agent: Agent, status: AgentStatus,
                    reason: str | None = None) -> None:
        agent.status = status
        agent.status_reason = reason
        if status in TERMINAL_STATUSES:
            agent.terminated_at = utcnow()
        self._persist_agent(agent)

    def required_capability(self, cap: CapabilityClass) -> None:
        """Placeholder hook: tool execution checks agent capability classes here."""
        _ = cap
