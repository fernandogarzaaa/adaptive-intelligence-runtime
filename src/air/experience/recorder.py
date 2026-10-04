"""Experience recorder v2 (EVE-like subsystem, owned interfaces).

Every meaningful run produces an experience record derived from the run's
event history — not from agent self-report. Records carry structured,
comparable DIMENSIONS so the runtime can later answer: "we solved this class
of problem N times; what changed between successful and unsuccessful runs?"

The validated path is enforced downstream: run -> experience -> evidence ->
evaluation -> confidence assessment -> candidate knowledge. There is no
run -> memory shortcut (see air/learning/bridge.py).
"""

from __future__ import annotations

import json
import platform
import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from air import __version__
from air.allocation.allocator import extract_features
from air.events.fabric import Event, utcnow
from air.experience.provenance import NON_EVIDENTIARY, Provenance


class ExperienceRecord(BaseModel):
    id: str = Field(default_factory=lambda: "exp_" + uuid.uuid4().hex[:12])
    run_id: str
    goal: str
    initial_state: dict = Field(default_factory=dict)
    cognitive_configuration: dict = Field(default_factory=dict)
    dimensions: dict = Field(default_factory=dict)
    agents: list[dict] = Field(default_factory=list)
    actions: list[dict] = Field(default_factory=list)
    observations: list[dict] = Field(default_factory=list)
    intermediate_results: list[dict] = Field(default_factory=list)
    outcomes: dict = Field(default_factory=dict)
    cost: float = 0.0
    latency_ms: int = 0
    failures: list[dict] = Field(default_factory=list)
    verification: dict = Field(default_factory=dict)
    final_result: dict | None = None
    evaluation_refs: list[str] = Field(default_factory=list)
    assurance_refs: list[str] = Field(default_factory=list)
    runtime_version: str = __version__
    environment_version: str = Field(
        default_factory=lambda: f"{platform.system()}-{platform.release()}"
                                f"/py{platform.python_version()}")
    policy_versions: dict = Field(default_factory=dict)
    capability_versions: dict = Field(default_factory=dict)
    provenance: Provenance = Provenance.OBSERVED
    created_at: str = Field(default_factory=utcnow)


# Dimensions the learning layer is allowed to aggregate over. Fixed schema so
# comparisons across runs are meaningful.
DIMENSION_KEYS = [
    "task", "cognitive_strategy", "agent_count", "topology", "roles",
    "models", "providers", "tools", "capabilities", "budget",
    "verification_strategy", "outcome", "verified",
]


class ExperienceRecorder:
    def __init__(self, conn, store=None) -> None:
        self._conn = conn
        # Optional EventStore sharing the same connection. When provided,
        # the experience row and its experience.created event commit
        # atomically (see record_run).
        self._store = store

    def record_run(self, run_id: str,
                   causation_id: str | None = None) -> tuple["ExperienceRecord", "Event | None"]:
        """Record the experience. Returns ``(record, event)``; when a store
        was provided, ``event`` is the persisted ``experience.created``
        event (already in the ledger -- the caller only needs to publish
        it), otherwise ``None``.

        ``causation_id`` threads the causal parent (normally the
        run.completed event that triggered recording)."""
        run = self._conn.execute(
            "SELECT goal, status, strategy, cognitive_plan, seed,"
            " policy_version, capability_versions, total_cost, total_tokens,"
            " error, final_result, created_at, started_at, completed_at"
            " FROM runs WHERE id=?", (run_id,)).fetchone()
        if not run:
            raise ValueError(f"run not found: {run_id}")
        (goal, status, strategy, plan_json, seed, policy_version,
         cap_versions_json, cost, tokens, error, final_json,
         created, started, completed) = run
        plan = json.loads(plan_json) if plan_json else {}

        agents = self._conn.execute(
            "SELECT id, parent_id, role, specialization, objective, model,"
            " provider, capabilities, tools, status, status_reason, generation,"
            " epistemic_kind"
            " FROM agents WHERE root_run_id=? ORDER BY created_at",
            (run_id,)).fetchall()
        agent_rows, roles, models, providers, tools = [], set(), set(), set(), set()
        lineage = []
        # Epistemic separation (Invariant #12): a run performed entirely
        # by non-evidentiary agents (simulators, forecasters) is not an
        # observation. Its experience retains that provenance so it can
        # inform allocation without ever verifying reality.
        agent_kinds = {}
        for r in agents:
            (aid, parent, role, spec, obj, model, provider, caps, tool_list,
             st, reason, gen, epistemic_kind) = r
            agent_kinds[aid] = Provenance(epistemic_kind or "OBSERVED")
            agent_rows.append({"id": aid, "parent_id": parent, "role": role,
                               "specialization": spec, "objective": obj,
                               "model": model, "provider": provider,
                               "capabilities": json.loads(caps or "[]"),
                               "tools": json.loads(tool_list or "[]"),
                               "status": st, "generation": gen})
            roles.add(role)
            if model:
                models.add(model)
            if provider:
                providers.add(provider)
            tools.update(json.loads(tool_list or "[]"))
            if parent:
                lineage.append([parent, aid])

        events = self._conn.execute(
            "SELECT type, agent_id, payload, timestamp FROM events"
            " WHERE run_id=? ORDER BY rowid", (run_id,)).fetchall()
        actions, observations, failures, intermediate = [], [], [], []
        for typ, agent_id, payload_json, ts in events:
            payload = json.loads(payload_json)
            entry = {"type": typ, "agent_id": agent_id, "at": ts}
            # A simulator's tool output is a measurement of the
            # simulation, never an observation of reality. Label it so.
            kind = agent_kinds.get(agent_id, Provenance.OBSERVED)
            ev_prov = (kind.value if kind in NON_EVIDENTIARY
                       else Provenance.OBSERVED.value)
            if typ.startswith("tool."):
                actions.append({**entry, "detail": payload,
                                "provenance": ev_prov})
            elif typ == "agent.message" and payload.get("kind") == "result":
                intermediate.append({**entry, "detail": payload,
                                     "provenance": ev_prov
                                     if kind in NON_EVIDENTIARY
                                     else Provenance.DERIVED.value})
            elif typ == "agent.message":
                observations.append({**entry, "detail": payload,
                                     "provenance": Provenance.DERIVED.value})
            elif typ in ("agent.failed", "budget.exhausted", "policy.blocked"):
                failures.append({**entry, "detail": payload,
                                 "provenance": ev_prov})

        tool_calls = self._conn.execute(
            "SELECT tool_name, tool_version, capability, state, latency_ms,"
            " policy_version, result_hash, verification_status, server_id,"
            " agent_id"
            " FROM tool_calls WHERE run_id=?",
            (run_id,)).fetchall()
        for (tool, tver, cap, st, lat, pver, rhash, vstat,
             server_id, tc_agent_id) in tool_calls:
            tools.add(tool)
            tc_kind = agent_kinds.get(tc_agent_id, Provenance.OBSERVED)
            actions.append({"type": "tool.call", "tool": tool,
                            "tool_version": tver, "capability": cap,
                            "status": st, "latency_ms": lat,
                            "policy_version": pver,
                            "result_hash": rhash,
                            "verification_status": vstat,
                            "server_id": server_id,
                            "provenance": (tc_kind.value
                                           if tc_kind in NON_EVIDENTIARY
                                           else Provenance.OBSERVED.value)})

        budget = self._conn.execute(
            "SELECT token_limit, time_limit_s, cost_limit, agent_limit"
            " FROM budgets WHERE run_id=? AND scope='run'", (run_id,)).fetchone()
        budget_dim = dict(zip(["token_limit", "time_limit_s", "cost_limit",
                               "agent_limit"], budget)) if budget else {}

        latency_ms = 0
        if started and completed:
            latency_ms = int((datetime.fromisoformat(completed)
                              - datetime.fromisoformat(started))
                             .total_seconds() * 1000)

        verification_strategy = plan.get("verification_strategy", "none")
        verified = status == "COMPLETED" and not any(
            "verif" in (f.get("detail") or {}).get("reason", "")
            for f in failures)

        features = extract_features(goal)
        dimensions = {
            "task": {"goal": goal[:500], "goal_length": len(goal),
                     "uncertainty": features.uncertainty,
                     "complexity": features.complexity,
                     "needs_research": features.needs_research,
                     "needs_verification": features.needs_verification,
                     "code_task": features.code_task,
                     "high_stakes": features.high_stakes},
            "cognitive_strategy": strategy,
            "agent_count": len(agent_rows),
            "topology": plan.get("topology", "unknown"),
            "agent_lineage": lineage,
            "roles": sorted(roles),
            "models": sorted(models),
            "providers": sorted(providers),
            "tools": sorted(tools),
            "capabilities": json.loads(cap_versions_json or "{}"),
            "budget": budget_dim,
            "verification_strategy": verification_strategy,
            "outcome": status,
            "verified": verified,
            "seed": seed,
        }

        # Record-level provenance (Invariant #12): if every agent that
        # acted in this run is non-evidentiary, the experience is not an
        # observation. It retains SIMULATED (or the dominant kind)
        # provenance so it can inform allocation without ever verifying
        # reality or training the learning engine as fact.
        if agent_kinds and all(k in NON_EVIDENTIARY
                               for k in agent_kinds.values()):
            record_provenance = next(iter(agent_kinds.values()))
        elif status in ("COMPLETED", "FAILED", "CANCELLED"):
            record_provenance = Provenance.OBSERVED
        else:
            record_provenance = Provenance.DERIVED

        exp = ExperienceRecord(
            run_id=run_id, goal=goal,
            initial_state={"strategy": strategy, "plan": plan},
            cognitive_configuration=plan, dimensions=dimensions,
            agents=agent_rows, actions=actions, observations=observations,
            intermediate_results=intermediate,
            outcomes={"status": status,
                      "final_result": json.loads(final_json) if final_json else None,
                      "error": error,
                      "provenance": record_provenance.value},
            cost=cost or 0.0, latency_ms=latency_ms, failures=failures,
            verification={"strategy": verification_strategy,
                          "failures": failures, "verified": verified},
            final_result=json.loads(final_json) if final_json else None,
            runtime_version=__version__,
            policy_versions={"allocator": plan.get("allocator_version"),
                             "run_policy": policy_version},
            capability_versions=json.loads(cap_versions_json or "{}"),
            provenance=record_provenance,
        )
        if self._store is None:
            self._insert_row(exp, dimensions, agent_rows, actions,
                             observations, failures, latency_ms, goal,
                             plan, final_json, policy_version,
                             cap_versions_json)
            self._conn.commit()
            return exp, None
        event = Event(type="experience.created", run_id=run_id,
                      payload={"experience_id": exp.id},
                      causation_id=causation_id,
                      correlation_id=run_id)
        with self._store.atomic():
            with self._conn:
                self._insert_row(exp, dimensions, agent_rows, actions,
                                 observations, failures, latency_ms, goal,
                                 plan, final_json, policy_version,
                                 cap_versions_json)
                self._store.insert(event)
        return exp, event

    def _insert_row(self, exp, dimensions, agent_rows, actions, observations,
                    failures, latency_ms, goal, plan, final_json,
                    policy_version, cap_versions_json) -> None:
        """INSERT the experience row without committing; the caller owns the
        transaction."""
        self._conn.execute(
            """INSERT INTO experiences (id, run_id, goal, initial_state,
               cognitive_configuration, dimensions, agents, actions,
               observations, outcomes, cost, latency_ms, failures,
               verification, final_result, evaluation_refs, assurance_refs,
               runtime_version, environment_version, policy_versions,
               capability_versions, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (exp.id, exp.run_id, goal, json.dumps(exp.initial_state),
             json.dumps(exp.cognitive_configuration), json.dumps(dimensions),
             json.dumps(agent_rows), json.dumps(actions),
             json.dumps(observations), json.dumps(exp.outcomes), exp.cost,
             latency_ms, json.dumps(failures), json.dumps(exp.verification),
             json.dumps(exp.final_result) if exp.final_result else None,
             json.dumps([]), json.dumps([]), exp.runtime_version,
             exp.environment_version, json.dumps(exp.policy_versions),
             json.dumps(exp.capability_versions), exp.created_at),
        )

    def link_evaluation(self, experience_id: str, evaluation_id: str,
                        assurance_id: str | None = None) -> None:
        """Attach evaluation/assurance refs: the evidence that this experience
        was independently assessed. Required before knowledge promotion."""
        row = self._conn.execute(
            "SELECT evaluation_refs, assurance_refs FROM experiences"
            " WHERE id=?", (experience_id,)).fetchone()
        if not row:
            raise ValueError(f"experience not found: {experience_id}")
        eval_refs = json.loads(row[0] or "[]")
        assur_refs = json.loads(row[1] or "[]")
        if evaluation_id not in eval_refs:
            eval_refs.append(evaluation_id)
        if assurance_id and assurance_id not in assur_refs:
            assur_refs.append(assurance_id)
        self._conn.execute(
            "UPDATE experiences SET evaluation_refs=?, assurance_refs=?"
            " WHERE id=?",
            (json.dumps(eval_refs), json.dumps(assur_refs), experience_id))
        self._conn.commit()

    def get(self, exp_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT id, run_id, goal, dimensions, outcomes, cost, latency_ms,"
            " failures, verification, evaluation_refs, assurance_refs,"
            " runtime_version, created_at FROM experiences WHERE id=?",
            (exp_id,)).fetchone()
        if not row:
            return None
        keys = ["id", "run_id", "goal", "dimensions", "outcomes", "cost",
                "latency_ms", "failures", "verification", "evaluation_refs",
                "assurance_refs", "runtime_version", "created_at"]
        out = dict(zip(keys, row))
        for k in ("dimensions", "outcomes", "failures", "verification",
                  "evaluation_refs", "assurance_refs"):
            out[k] = json.loads(out[k]) if out[k] else None
        return out

    def list(self, limit: int = 50) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id, run_id, goal, cost, latency_ms, created_at"
            " FROM experiences ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [{"id": r[0], "run_id": r[1], "goal": r[2], "cost": r[3],
                 "latency_ms": r[4], "created_at": r[5]} for r in rows]

    # ------------------------------------------------------------ comparison
    def compare(self, experience_ids: list[str]) -> dict:
        """Compare experiences across structured dimensions.

        Answers: "we solved this class of problem N times; what changed
        between successful and unsuccessful runs?"
        """
        exps = [self.get(eid) for eid in experience_ids]
        exps = [e for e in exps if e]
        if not exps:
            return {"compared": 0, "dimensions": {}, "differences": []}
        table: dict[str, dict[str, object]] = {}
        for e in exps:
            dims = e.get("dimensions") or {}
            for key in DIMENSION_KEYS:
                table.setdefault(key, {})[e["id"]] = dims.get(key)
        successful = [e["id"] for e in exps
                      if (e.get("dimensions") or {}).get("outcome") == "COMPLETED"]
        unsuccessful = [e["id"] for e in exps if e["id"] not in successful]
        differences = []
        for key, vals in table.items():
            uniq = {json.dumps(v, sort_keys=True, default=str)
                    for v in vals.values()}
            if len(uniq) > 1:
                succ_vals = {json.dumps(vals[i], sort_keys=True, default=str)
                             for i in successful if i in vals}
                unsucc_vals = {json.dumps(vals[i], sort_keys=True, default=str)
                               for i in unsuccessful if i in vals}
                discriminates = bool(succ_vals and unsucc_vals
                                     and succ_vals != unsucc_vals)
                differences.append({
                    "dimension": key,
                    "discriminates_outcome": discriminates,
                    "successful_values": sorted(succ_vals),
                    "unsuccessful_values": sorted(unsucc_vals),
                })
        differences.sort(key=lambda d: (not d["discriminates_outcome"],
                                        d["dimension"]))
        return {"compared": len(exps), "successful": successful,
                "unsuccessful": unsuccessful, "dimensions": table,
                "differences": differences}
