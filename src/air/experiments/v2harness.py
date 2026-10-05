"""v2 experiment harness: D1 -> Learning -> D2 -> Learning -> D3.

Implements docs/PREREGISTRATION_DSERIES_V2.md. Real AgentRuntime runs
with the frozen scripted behaviors, real tool calls, real
evidence-grounded evaluation, real assurance probes. The v2 learner
(frozen) consumes LearningEvidence built from verdicts; it never
sees goal text, task ids, or set membership.

Organization space (preregistered scope): three pinned strategies.
Parent = argmax of the real score_strategies over the three.
Candidate = base scores + v2 rule adjustments, hard requirements
filtering for feasibility before scoring.

Usage:
    python -m air.experiments.v2harness prereg-check   # verify freeze
    python -m air.experiments.v2harness run-d1
    python -m air.experiments.v2harness learn-1
    python -m air.experiments.v2harness run-s1
    python -m air.experiments.v2harness promote-1
    python -m air.experiments.v2harness run-d2
    python -m air.experiments.v2harness learn-2
    python -m air.experiments.v2harness run-s2
    python -m air.experiments.v2harness promote-2
    python -m air.experiments.v2harness run-d3
    python -m air.experiments.v2harness report

Each step appends to the append-only JSONL ledger and refuses to run
unless all prior steps are recorded. Steps are idempotent: a step
with a completed ledger record is skipped, never re-run.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
import time
from pathlib import Path

from air.agents.runtime import AgentRuntime
from air.allocation.allocator import (
    Strategy,
    build_plan,
    extract_features as goal_features,
    score_strategies,
)
from air.config import AirConfig
from air.experiments import behaviors, evalkit
from air.experiments.conditions import (
    AGENT_BUDGET,
    COST_BUDGET_USD,
    TOKEN_BUDGET,
    TOOL_CALL_BUDGET,
    WALL_TIME_BUDGET_S,
    apply_experiment_grants,
    register_task_for_run,
    seed_workspace,
)
from air.experiments import metrics as exp_metrics
from air.learning_v2 import attribution, evaluation, extraction, generation, promotion
from air.learning_v2.contracts import (
    AllocationRule,
    OrganizationProperties,
    PolicyCandidate,
    resolve_rules,
)
from air.learning_v2.evaluation import EvalTask
from air.learning_v2.promotion import PromotionLog
from air.persistence.db import Database, find_migrations_dir

import os

EXP_ID = os.environ.get("AIR_EXP_ID", "dseries-v2")
EXP_DIR = Path.home() / "workspace" / "air-experiments" / EXP_ID
TASKS_DIR = Path(__file__).parent / "tasks" / "v2"

STRATEGIES = [Strategy.SINGLE_AGENT, Strategy.HIERARCHICAL_AGENTS,
              Strategy.PARALLEL_AGENTS]
STRATEGY_NAMES = [s.value for s in STRATEGIES]
INCOHERENT = "INCOHERENT"

# Role phases -> abstract capabilities (frozen ROLE_PHASES mapping).
PHASE_CAPABILITIES = {
    "prepare": frozenset({"READ"}),
    "produce": frozenset({"WRITE", "EXECUTE"}),
    "verify": frozenset({"READ"}),
}


# ---------------------------------------------------------------------------
# Pure functions: features, organizations, policies.
# ---------------------------------------------------------------------------

def extract_features(task: dict) -> dict:
    """Mechanical class-A features from task structure only. Never reads
    `kind` (absent) or goal text."""
    ops = task["operations"]
    phases = {o["phase"] for o in ops}
    produce_ops = [o for o in ops if o["phase"] == "produce"]
    return {
        "requires_output_artifact": len(task["artifacts"]) > 0,
        "output_artifact_count": len(task["artifacts"]),
        "has_named_output_targets": len(task["artifacts"]) > 0,
        "requested_write_effect": "create" in task["effects"],
        "requested_read_effect": any(o["tool"] == "fs.read" for o in ops),
        "requested_execute_effect": "execute" in task["effects"],
        "independent_work_unit_count": len(produce_ops),
        "dependency_depth": len(phases) - 1,
        "multi_step": len(ops) > 1,
    }


def eval_class(task: dict) -> str:
    """Preregistered performance classes, mechanical from features."""
    f = extract_features(task)
    if f["output_artifact_count"] > 1:
        return "multi"
    if f["requires_output_artifact"]:
        return "single"
    if f["requested_execute_effect"]:
        return "exec"
    return "read"


def org_properties(strategy: Strategy) -> OrganizationProperties:
    """OrganizationProperties computed from the real allocator plan."""
    plan = build_plan("dummy goal for plan inspection", strategy,
                      goal_features("dummy goal for plan inspection"),
                      token_budget=TOKEN_BUDGET,
                      time_budget_s=WALL_TIME_BUDGET_S,
                      cost_budget_usd=COST_BUDGET_USD,
                      agent_budget=AGENT_BUDGET)
    roles = frozenset(spec.role for spec in plan.agent_specs)
    caps: set[str] = set()
    for role in roles:
        for phase in behaviors.ROLE_PHASES.get(role, ()):
            caps |= PHASE_CAPABILITIES[phase]
    return OrganizationProperties(
        strategy=strategy.value, roles=roles,
        capabilities=frozenset(caps), topology=plan.topology)


ORG_PROPS = {s.value: org_properties(s) for s in STRATEGIES}


def base_scores(goal: str) -> dict[str, float]:
    """The real v1 allocator scores, restricted to the experiment's
    organization space."""
    scores = score_strategies(goal_features(goal), AGENT_BUDGET)
    return {s.value: round(scores[s], 4) for s in STRATEGIES}


def _feasible(strategies: list[str], requirements) -> list[str]:
    from air.learning_v2.contracts import RuleAction
    out = []
    for name in strategies:
        org = ORG_PROPS[name]
        ok = True
        for req in requirements:
            if req.action is RuleAction.REQUIRE_CAPABILITY:
                ok &= req.target in org.capabilities
            elif req.action is RuleAction.REQUIRE_ROLE:
                ok &= req.target in org.roles
        if ok:
            out.append(name)
    return out


def choose_strategy(task: dict, rules: list[AllocationRule]) -> str:
    """Parent (no rules) or candidate policy choice. Returns a strategy
    name, or INCOHERENT when hard requirements eliminate every
    organization: a distinct outcome, never silent."""
    features = extract_features(task)
    scores = base_scores(task["goal"])
    resolved = resolve_rules(rules, features)
    feasible = _feasible(STRATEGY_NAMES, resolved["requirements"])
    if not feasible:
        return INCOHERENT
    adjusted = {n: scores[n] + resolved["adjustments"].get(n, 0.0)
                for n in feasible}
    # Deterministic: highest adjusted score, tiebreak by name.
    return sorted(adjusted, key=lambda n: (-adjusted[n], n))[0]


def parent_choose(task: dict) -> str:
    return choose_strategy(task, [])


# ---------------------------------------------------------------------------
# Ledger.
# ---------------------------------------------------------------------------

def _ledger_path() -> Path:
    EXP_DIR.mkdir(parents=True, exist_ok=True)
    return EXP_DIR / "ledger.jsonl"


def ledger_append(kind: str, payload: dict) -> dict:
    path = _ledger_path()
    seq = 0
    if path.exists():
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    seq = max(seq, json.loads(line)["seq"])
    record = {"seq": seq + 1, "kind": kind, "payload": payload}
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")
    return record


def ledger_load(kind: str | None = None) -> list[dict]:
    path = _ledger_path()
    if not path.exists():
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                r = json.loads(line)
                if kind is None or r["kind"] == kind:
                    out.append(r)
    return out


def step_done(step: str) -> bool:
    return any(r["payload"].get("step") == step
               for r in ledger_load("step_complete"))


def require_steps(*steps: str) -> None:
    missing = [s for s in steps if not step_done(s)]
    if missing:
        raise RuntimeError(f"refusing: prior steps not recorded: {missing}")


# ---------------------------------------------------------------------------
# Execution: real runs.
# ---------------------------------------------------------------------------

class Runner:
    """One AgentRuntime per phase (fresh DB = isolated ledger)."""

    def __init__(self, phase: str):
        self.phase = phase
        data_dir = EXP_DIR / "ledgers" / phase
        data_dir.mkdir(parents=True, exist_ok=True)
        db_path = data_dir / "air.db"
        if db_path.exists():
            db_path.unlink()
        self.db_path = str(db_path)
        db = Database(db_path)
        db.migrate(find_migrations_dir())
        self.rt = AgentRuntime(AirConfig(data_dir=data_dir), db)
        for role in behaviors.ROLE_PHASES:
            self.rt.register_behavior(role, behaviors.experiment_behavior)
        self.data_dir = data_dir

    async def drive_run(self, task: dict, strategy_name: str,
                        policy_label: str, seed: int) -> dict:
        rt = self.rt
        run_id = await rt.create_run(
            task["goal"],
            strategy=Strategy(strategy_name),
            tool_call_budget=TOOL_CALL_BUDGET,
            time_budget_s=WALL_TIME_BUDGET_S,
            agent_budget=AGENT_BUDGET,
            token_budget=TOKEN_BUDGET,
            cost_budget_usd=COST_BUDGET_USD,
            seed=seed,
        )
        apply_experiment_grants(rt, run_id)
        seed_workspace(self.data_dir, run_id, task)
        register_task_for_run(run_id, task)
        t0 = time.perf_counter()
        await rt.start_run(run_id)
        wall_exceeded = False
        try:
            await asyncio.wait_for(self._wait(rt, run_id),
                                   timeout=WALL_TIME_BUDGET_S)
        except asyncio.TimeoutError:
            wall_exceeded = True
            await rt.cancel_run(run_id)
        latency_s = time.perf_counter() - t0

        evaluation = evalkit.evaluate_run(rt.db.conn, run_id)
        assurance = evalkit.assure_run(rt.db.conn, evaluation.id)
        m = exp_metrics.compute_run_metrics(self.db_path, run_id, latency_s)
        # Execution facts for the v2.1 outcome certificate (mechanical,
        # from the run ledger; not evaluator interpretations).
        run_status = rt.db.conn.execute(
            "SELECT status FROM runs WHERE id=?", (run_id,)).fetchone()
        agents_completed = rt.db.conn.execute(
            "SELECT COUNT(*) FROM agents WHERE root_run_id=? AND status=?",
            (run_id, "COMPLETED")).fetchone()[0]
        return {
            "run_id": run_id,
            "phase": self.phase,
            "task_id": task["id"],
            "policy": policy_label,
            "strategy": strategy_name,
            "seed": seed,
            "wall_exceeded": wall_exceeded,
            "evaluation_verdict": evaluation.verdict.value,
            "assurance_verdict": assurance.evaluator_verdict.value,
            "evaluation_id": evaluation.id,
            "assurance_id": assurance.id,
            "metrics": m,
            "ledger": str(Path(self.db_path).relative_to(EXP_DIR)),
            "run_completed": bool(run_status and run_status[0] == "COMPLETED"),
            "agents_completed": int(agents_completed),
        }

    async def _wait(self, rt, run_id: str) -> str:
        while True:
            row = rt.db.conn.execute(
                "SELECT status FROM runs WHERE id=?", (run_id,)).fetchone()
            if (row[0] if row else "UNKNOWN") in (
                    "COMPLETED", "FAILED", "CANCELLED"):
                return row[0]
            await asyncio.sleep(0.05)


def derive_seed(*parts: str) -> int:
    raw = "|".join([EXP_ID, *parts]).encode()
    return int(hashlib.sha256(raw).hexdigest()[:15], 16)


# Canonical tool-effect vocabulary (air.evidence.validity._TOOL_EFFECTS),
# normalized to claim vocabulary for the v2.1 outcome certificate.
# The certificate compares required effects (from task claims) against
# observed effects (from tool completions) in one vocabulary.
_TOOL_EFFECT_TO_CLAIM = {
    "fs.read": "observe",
    "fs.write": "create",      # tool emits "create/modify"; claims say "create"
    "shell.exec": "execute",
}


def build_certificate_inputs(run: dict, task: dict) -> dict:
    """Mechanical inputs for the v2.1 outcome-failure certificate.

    Required outcomes come from the task's explicit claims (effects +
    artifacts). Observed effects come from the run ledger's successful
    tool completions. Target satisfaction is a direct filesystem check
    in the run's workspace: a required target is satisfied iff the file
    exists. Every required target is positively checked; an unchecked
    target excludes (it never certifies).
    """
    claims = task.get("claims", [])
    required_effects = sorted({e for c in claims for e in c.get("effects", [])})
    required_targets = sorted({a for c in claims for a in c.get("artifacts", [])})

    db_path = EXP_DIR / run["ledger"]
    observed: set[str] = set()
    if db_path.exists():
        import sqlite3
        conn = sqlite3.connect(db_path)
        try:
            rows = conn.execute(
                "SELECT tool_name FROM tool_calls "
                "WHERE run_id=? AND verification_status=? AND "
                "(error IS NULL OR error='')",
                (run["run_id"], "PASSED")).fetchall()
        finally:
            conn.close()
        for (tname,) in rows:
            eff = _TOOL_EFFECT_TO_CLAIM.get(tname)
            if eff:
                observed.add(eff)

    # Direct filesystem check: the run's workspace namespace.
    workspace = EXP_DIR / "ledgers" / run["phase"] / run["run_id"]
    targets_satisfied = {}
    for t in required_targets:
        # Guard against path traversal; task targets are relative names.
        assert ".." not in t and not t.startswith("/"), \
            f"unsafe target name: {t!r}"
        targets_satisfied[t] = (workspace / t).is_file()

    return {
        "required_effects": required_effects,
        "required_targets": required_targets,
        "observed_effects": sorted(observed),
        "targets_satisfied": targets_satisfied,
        "run_completed": bool(run.get("run_completed", False)),
        "agents_completed": int(run.get("agents_completed", 0)),
    }


def to_learning_evidence(run: dict, task: dict, evidence_id: str):
    org = ORG_PROPS[run["strategy"]]
    m = run["metrics"]
    cert = build_certificate_inputs(run, task)
    return extraction.extract_experience(
        id=evidence_id,
        task_features=extract_features(task),
        allocation={"strategy": run["strategy"],
                    "scores": base_scores(task["goal"])},
        organization={"strategy": org.strategy,
                      "roles": sorted(org.roles),
                      "capabilities": sorted(org.capabilities),
                      "topology": org.topology},
        resources={"cost": m["cost_usd"],
                   "latency_ms": round((m["latency_s"] or 0) * 1000, 1),
                   "agent_count": m["n_agents"]},
        evaluation_verdict=run["evaluation_verdict"],
        assurance_verdict=run["assurance_verdict"],
        required_effects=cert["required_effects"],
        required_targets=cert["required_targets"],
        observed_effects=cert["observed_effects"],
        targets_satisfied=cert["targets_satisfied"],
        run_completed=cert["run_completed"],
        agents_completed=cert["agents_completed"],
    )


def load_tasks(name: str) -> list[dict]:
    payload = json.loads((TASKS_DIR / f"{name}.json").read_text())
    return payload["tasks"]


# ---------------------------------------------------------------------------
# Learning + promotion steps.
# ---------------------------------------------------------------------------

def learn_step(step_name: str, training_runs: list[dict],
               tasks_by_id: dict, parent_version: str,
               candidate_id: str) -> dict:
    """Extract -> attribute -> generate -> candidate. Returns the ledger
    payload (candidate or NO_CANDIDATE)."""
    records = []
    for i, run in enumerate(training_runs):
        task = tasks_by_id[run["task_id"]]
        records.append(to_learning_evidence(run, task, f"ev_{step_name}_{i:03d}"))
    attributions = attribution.attribute(records)
    rules = generation.generate_rules(attributions)
    payload: dict = {
        "step": step_name,
        "parent_version": parent_version,
        "n_records": len(records),
        "n_attributions": len(attributions),
        "n_robust": sum(1 for a in attributions
                        if a.outcome.value == "association" and a.robust),
        "n_rules": len(rules),
    }
    if not rules:
        payload["outcome"] = "NO_CANDIDATE"
        payload["note"] = ("the evidence was not sufficient for an "
                           "admissible candidate: legitimate result")
        return payload
    candidate = generation.build_candidate(
        id=candidate_id, parent_version=parent_version,
        hypothesis=("Task-conditioned organization learned from verified "
                    "experience under the preregistered protocol."),
        rules=rules, attributions=attributions)
    payload["outcome"] = "CANDIDATE"
    payload["candidate"] = candidate.canonical()
    payload["rules"] = [
        {"id": r.id, "rule_hash": r.rule_hash,
         "when": [c.canonical() for c in r.when],
         "scope": [c.canonical() for c in r.scope],
         "then": r.then.canonical(),
         "evidence_strength": r.evidence_strength,
         "attribution_id": r.constraints.get("attribution_id")}
        for r in rules]
    return payload


def policy_assurance(candidate: dict, training_ids: set[str]) -> tuple[str, list[str]]:
    """Mechanical assurance over the candidate policy artifact."""
    from air.learning_v2.contracts import canonical_hash
    reasons = []
    for r in candidate["rules"]:
        recomputed = canonical_hash({
            "when": r["when"], "then": r["then"], "scope": r["scope"],
            "rule_schema_version": "allocation-rule/v1",
            "feature_schema_version": "task-features/v1",
            "org_property_schema_version": "org-properties/v1",
            "policy_semantics_version": "policy-semantics/v1",
        })
        if recomputed != r["rule_hash"]:
            reasons.append(f"rule {r['id']}: hash mismatch")
        for cond in r["when"] + r["scope"]:
            if cond["feature"] not in (
                    "requires_output_artifact", "output_artifact_count",
                    "has_named_output_targets", "requested_write_effect",
                    "requested_read_effect", "requested_execute_effect",
                    "independent_work_unit_count", "dependency_depth",
                    "multi_step"):
                reasons.append(f"rule {r['id']}: non-class-A feature "
                               f"{cond['feature']}")
    if not candidate["rules"]:
        reasons.append("candidate has no rules")
    verdict = "SOUND" if not reasons else "UNSOUND"
    return verdict, reasons


# ---------------------------------------------------------------------------
# Phase runners.
# ---------------------------------------------------------------------------

async def _run_matrix(tasks: list[dict], phase: str,
                      chooser) -> list[dict]:
    """Run each task under each strategy (D1) or under a policy choice."""
    runner = Runner(phase)
    runs = []
    for task in tasks:
        strategies = chooser(task)
        for strat in strategies:
            if strat == INCOHERENT:
                runs.append({"run_id": None, "phase": phase,
                             "task_id": task["id"], "policy": "candidate",
                             "strategy": INCOHERENT, "incoherent": True,
                             "seed": derive_seed(phase, task["id"], "inc")})
                continue
            seed = derive_seed(phase, task["id"], strat)
            rec = await runner.drive_run(task, strat, chooser.__name__, seed)
            runs.append(rec)
            ledger_append("run", rec)
    return runs


def cmd_run_d1():
    require_steps()
    if step_done("d1"):
        print("d1 already recorded; skipping")
        return
    tasks = load_tasks("d1")
    runs = asyncio.run(_run_matrix(
        tasks, "d1", lambda t: STRATEGY_NAMES))
    ledger_append("step_complete", {"step": "d1", "n_runs": len(runs)})
    print(f"d1: {len(runs)} runs")


def _candidate_from_ledger(learn_step_name: str) -> PolicyCandidate | None:
    recs = [r for r in ledger_load("learn") if r["payload"].get("step")
            == learn_step_name]
    if not recs or recs[-1]["payload"].get("outcome") != "CANDIDATE":
        return None
    return _rebuild_candidate(recs[-1]["payload"]["candidate"])


def _rebuild_candidate(data: dict) -> PolicyCandidate:
    from air.learning_v2.contracts import (
        AllocationRule, AtomicCondition, Operator, RuleAction, RuleThen)
    rules = []
    for r in data["rules"]:
        rules.append(AllocationRule(
            id=r["id"],
            when=tuple(AtomicCondition(c["feature"], Operator(c["op"]),
                                      c["value"]) for c in r["when"]),
            then=RuleThen(RuleAction(r["then"]["action"]),
                          r["then"]["target"],
                          delta=r["then"].get("delta", 0.0)),
            scope=tuple(AtomicCondition(c["feature"], Operator(c["op"]),
                                        c["value"]) for c in r["scope"]),
            evidence=tuple(r["evidence"]),
            evidence_strength=r["evidence_strength"],
            uncertainty=r["uncertainty"],
            constraints=r["constraints"],
            priority=r["priority"]))
    return PolicyCandidate(
        id=data["id"], parent_version=data["parent_version"],
        rules=tuple(rules), hypothesis=data["hypothesis"],
        source_experiences=tuple(data["source_experiences"]),
        attribution_ids=tuple(data["attribution_ids"]))


def _active_rules() -> tuple[list[AllocationRule], str]:
    """Current active policy: promoted candidate rules, or [] for v1."""
    promos = [r for r in ledger_load("promotion")
              if r["payload"].get("decision") == "PROMOTE"]
    if not promos:
        return [], "v1"
    last = promos[-1]["payload"]
    # v2.2 format: policy_version dict with rules
    # v2.1 format: candidate dict with rules, version string
    if "policy_version" in last and isinstance(last["policy_version"], dict):
        pv = last["policy_version"]
        rules = _rebuild_rules(pv["rules"])
        return rules, pv["version"]
    else:
        cand = _rebuild_candidate(last["candidate"])
        return list(cand.rules), last["version"]


def _rebuild_rules(rules_data: list[dict]) -> list:
    """Rebuild AllocationRule objects from serialized rule dicts."""
    from air.learning_v2.contracts import (
        AllocationRule, AtomicCondition, Operator, RuleAction, RuleThen)
    rules = []
    for r in rules_data:
        # Handle both serialized formats (with nested condition dicts)
        def _cond(c):
            if isinstance(c, dict):
                return AtomicCondition(c["feature"], Operator(c["op"]), c["value"])
            return c
        when = r["when"]
        # Check if when contains dicts or already-formed conditions
        if when and isinstance(when[0], dict):
            when_t = tuple(_cond(c) for c in when)
        else:
            when_t = tuple(when)
        scope = r["scope"]
        if scope and isinstance(scope[0], dict):
            scope_t = tuple(_cond(c) for c in scope)
        else:
            scope_t = tuple(scope)
        then_d = r["then"]
        if isinstance(then_d, dict):
            then = RuleThen(RuleAction(then_d["action"]), then_d["target"],
                            delta=then_d.get("delta", 0.0))
        else:
            then = then_d
        rules.append(AllocationRule(
            id=r["id"], when=when_t, then=then, scope=scope_t,
            evidence=tuple(r["evidence"]),
            evidence_strength=r["evidence_strength"],
            uncertainty=r["uncertainty"],
            constraints=r["constraints"],
            priority=r["priority"]))
    return rules


def cmd_learn_1():
    require_steps("d1")
    if step_done("learn-1"):
        print("learn-1 already recorded; skipping")
        return
    runs = [r["payload"] for r in ledger_load("run")
            if r["payload"].get("phase") == "d1"]
    tasks_by_id = {t["id"]: t for t in load_tasks("d1")}
    payload = learn_step("learn-1", runs, tasks_by_id, "v1", "cand_001")
    ledger_append("learn", payload)
    ledger_append("step_complete", {"step": "learn-1",
                                   "outcome": payload["outcome"]})
    print(f"learn-1: {payload['outcome']} "
          f"({payload['n_rules']} rules)")


async def _intervention(pool: str, phase: str,
                        candidate: PolicyCandidate | None,
                        step_name: str):
    """Sealed promotion intervention: parent vs candidate on the same
    sealed tasks, paired outcomes, exact label pairing (no
    strategy-guessing in the oracle)."""
    tasks = load_tasks(pool)
    by_id = {t["id"]: t for t in tasks}
    cand_rules = list(candidate.rules) if candidate else []

    runner = Runner(phase)
    # results[(task_id, label)] -> run record or {"incoherent": True}
    results: dict[tuple[str, str], dict] = {}
    for task in tasks:
        p_strat = parent_choose(task)
        c_strat = (choose_strategy(task, cand_rules)
                   if candidate is not None else p_strat)
        for label, strat in (("parent", p_strat), ("candidate", c_strat)):
            if strat == INCOHERENT:
                results[(task["id"], label)] = {"incoherent": True,
                                                "task_id": task["id"]}
                ledger_append("incoherence", {
                    "phase": phase, "task_id": task["id"], "policy": label,
                    "note": "candidate hard requirements eliminated every "
                            "organization: distinct outcome, recorded"})
                continue
            seed = derive_seed(phase, task["id"], label)
            rec = await runner.drive_run(task, strat, label, seed)
            results[(task["id"], label)] = rec
            ledger_append("run", rec)

    outcomes = []
    for task in tasks:
        et_id = task["id"]
        p_strat = parent_choose(task)
        c_strat = (choose_strategy(task, cand_rules)
                   if candidate is not None else p_strat)
        p_rec = results[(et_id, "parent")]
        c_rec = results[(et_id, "candidate")]
        outcomes.append({
            "task_id": et_id,
            "eval_class": eval_class(task),
            "stratum": task.get("stratum"),  # v2.2: preregistered stratum label
            "safety": False,
            "differ": p_strat != c_strat,
            "parent_strategy": p_strat,
            "candidate_strategy": c_strat,
            "parent_verified": _verified_of(p_rec),
            "candidate_verified": _verified_of(c_rec),
            "parent_resources": _resources_of(p_rec),
            "candidate_resources": _resources_of(c_rec),
            "candidate_safety_violations": [],
        })
    parent_version = _active_version()
    return _evaluate_from_outcomes(
        step_name,
        candidate.id if candidate is not None else "none",
        parent_version, pool, outcomes)


def _verified_of(rec: dict):
    if rec.get("incoherent"):
        return False
    ev, av = rec["evaluation_verdict"], rec["assurance_verdict"]
    if av != "SOUND":
        return None
    return True if ev == "SUPPORTED" else (False if ev == "FALSIFIED" else None)


def _resources_of(rec: dict):
    if rec.get("incoherent"):
        return {"cost": 0.0, "latency_ms": 0.0, "agent_count": 0}
    m = rec["metrics"]
    return {"cost": m["cost_usd"],
            "latency_ms": round((m["latency_s"] or 0) * 1000, 1),
            "agent_count": m["n_agents"]}


def _active_version() -> str:
    _, v = _active_rules()
    return v


def _evaluate_from_outcomes(eval_id: str, candidate_id: str,
                            parent_version: str, pool: str,
                            outcomes: list[dict]):
    """Statistical machinery identical to evaluation.evaluate_paired,
    over precomputed real outcomes (exact label pairing)."""
    from air.learning_v2.contracts import DiscriminatingEvaluation
    from air.learning_v2.evaluation import (
        mcnemar_exact_onesided, wilson_lower)
    alpha, eps = 0.05, 0.1
    delta_ids = sorted(o["task_id"] for o in outcomes if o["differ"])
    stats: dict = {"paired": True, "alpha": alpha,
                   "non_inferiority_eps": eps, "classes": {}}
    verdict, notes = "PASS", []
    if not delta_ids:
        return DiscriminatingEvaluation(
            id=eval_id, candidate_id=candidate_id,
            parent_version=parent_version, sealed_pool_id=pool,
            decision_delta_task_ids=(),
            paired_outcomes=tuple(outcomes),
            statistics={**stats, "verdict_reason":
                        "empty decision delta: promotion refused"},
            verdict="VACUOUS")
    classes: dict[str, list[dict]] = {}
    for o in outcomes:
        classes.setdefault(o["eval_class"], []).append(o)
    improved = []
    for cls in sorted(classes):
        rows = classes[cls]
        pairs = [(r["parent_verified"], r["candidate_verified"])
                 for r in rows
                 if r["parent_verified"] is not None
                 and r["candidate_verified"] is not None]
        if not pairs:
            stats["classes"][cls] = {"n_paired": 0}
            continue
        pv = [p for p, _ in pairs]
        cv = [c for _, c in pairs]
        p_rate = sum(pv) / len(pv)
        c_rate = sum(cv) / len(cv)
        c_lb = wilson_lower(sum(cv), len(cv))
        non_inf = c_lb >= round(p_rate - eps, 4)
        b = sum(1 for a, x in pairs if a and not x)
        c_ = sum(1 for a, x in pairs if not a and x)
        p_val = mcnemar_exact_onesided(b, c_)
        disc = any(r["differ"] for r in rows)
        cls_stats = {"n_paired": len(pairs),
                     "parent_rate": round(p_rate, 4),
                     "candidate_rate": round(c_rate, 4),
                     "candidate_wilson_lower": c_lb,
                     "non_inferior": non_inf,
                     "mcnemar_b": b, "mcnemar_c": c_,
                     "mcnemar_p_one_sided": p_val}
        stats["classes"][cls] = cls_stats
        if not non_inf:
            verdict, notes = "FAIL", notes + [
                f"class {cls} not non-inferior"]
        if disc and c_ > b and p_val < alpha:
            improved.append(cls)
            cls_stats["improved"] = True
    stats["improved_classes"] = improved
    if not improved:
        verdict, notes = "FAIL", notes + ["no class improved at alpha=0.05"]
    pc = sum(o["parent_resources"]["cost"] for o in outcomes) / len(outcomes)
    cc = sum(o["candidate_resources"]["cost"] for o in outcomes) / len(outcomes)
    pl = sum(o["parent_resources"]["latency_ms"] for o in outcomes) / len(outcomes)
    cl = sum(o["candidate_resources"]["latency_ms"] for o in outcomes) / len(outcomes)
    stats["resources"] = {
        "parent_mean_cost": round(pc, 4), "candidate_mean_cost": round(cc, 4),
        "parent_mean_latency_ms": round(pl, 2),
        "candidate_mean_latency_ms": round(cl, 2)}
    if pc > 0 and cc > pc * 1.25:
        verdict, notes = "FAIL", notes + ["cost bound exceeded"]
    if pl > 0 and cl > pl * 1.25:
        verdict, notes = "FAIL", notes + ["latency bound exceeded"]
    stats["verdict_reason"] = "; ".join(notes) if notes else \
        "improvement on >=1 class, non-inferior elsewhere, resources bounded"
    return DiscriminatingEvaluation(
        id=eval_id, candidate_id=candidate_id,
        parent_version=parent_version, sealed_pool_id=pool,
        decision_delta_task_ids=tuple(delta_ids),
        paired_outcomes=tuple(outcomes), statistics=stats, verdict=verdict)


def cmd_run_s1():
    require_steps("d1", "learn-1")
    if step_done("s1"):
        print("s1 already recorded; skipping")
        return
    candidate = _candidate_from_ledger("learn-1")
    if candidate is None:
        ledger_append("evaluation", {"step": "s1", "verdict": "SKIPPED",
                                     "reason": "no candidate to evaluate"})
        ledger_append("step_complete", {"step": "s1", "verdict": "SKIPPED"})
        print("s1: SKIPPED (no candidate)")
        return
    ev = asyncio.run(_intervention("s1", "s1", candidate, "eval_s1"))
    ledger_append("evaluation", {"step": "s1", **ev.canonical()})
    ledger_append("step_complete", {"step": "s1",
                                   "verdict": ev.verdict})
    print(f"s1: {ev.verdict}")


def cmd_promote_1():
    require_steps("d1", "learn-1", "s1")
    if step_done("promote-1"):
        print("promote-1 already recorded; skipping")
        return
    learn = [r for r in ledger_load("learn")
             if r["payload"].get("step") == "learn-1"][-1]["payload"]
    if learn["outcome"] != "CANDIDATE":
        ledger_append("promotion", {"step": "promote-1",
                                    "decision": "NO_CANDIDATE",
                                    "reasons": ["no admissible candidate"]})
        ledger_append("step_complete", {"step": "promote-1",
                                       "decision": "NO_CANDIDATE"})
        print("promote-1: NO_CANDIDATE")
        return
    ev_recs = [r for r in ledger_load("evaluation")
               if r["payload"].get("step") == "s1"]
    if not ev_recs or ev_recs[-1]["payload"].get("verdict") == "SKIPPED":
        ledger_append("promotion", {"step": "promote-1",
                                    "decision": "NO_CANDIDATE",
                                    "reasons": ["no candidate evaluated"]})
        ledger_append("step_complete", {"step": "promote-1",
                                       "decision": "NO_CANDIDATE"})
        print("promote-1: NO_CANDIDATE")
        return
    ev_data = ev_recs[-1]["payload"]
    candidate = _rebuild_candidate(learn["candidate"])
    training_ids = {f"ev_learn-1_{i:03d}" for i in range(learn["n_records"])}
    av, reasons = policy_assurance(learn["candidate"], training_ids)
    decision = promotion.decide_promotion(
        id="promo_001", candidate=candidate,
        evaluation=_rebuild_evaluation(ev_data),
        assurance_verdict=av, assurance_id="asr_promo_001")
    payload = {"step": "promote-1", "decision": decision.decision,
               "reasons": list(decision.reasons),
               "evaluation_id": decision.evaluation_id,
               "assurance_verdict": av, "assurance_reasons": reasons}
    if decision.decision == "PROMOTE":
        payload["candidate"] = learn["candidate"]
        payload["version"] = decision.policy_version.version
        payload["policy_version"] = decision.policy_version.canonical()
    ledger_append("promotion", payload)
    ledger_append("step_complete", {"step": "promote-1",
                                   "decision": decision.decision})
    print(f"promote-1: {decision.decision}")


def _rebuild_evaluation(data: dict):
    from air.learning_v2.contracts import DiscriminatingEvaluation
    return DiscriminatingEvaluation(
        id=data["id"], candidate_id=data["candidate_id"],
        parent_version=data["parent_version"],
        sealed_pool_id=data["sealed_pool_id"],
        decision_delta_task_ids=tuple(data["decision_delta_task_ids"]),
        paired_outcomes=tuple(data["paired_outcomes"]),
        statistics=data["statistics"], verdict=data["verdict"])


def cmd_run_d2():
    # v2.2: D2 follows the v2.2 S1 promotion (s1-v2-2, promote-1-v2-2).
    # The v2.1 s1/promote-1 steps are quarantined and do not gate D2.
    require_steps("d1", "learn-1", "s1-v2-2", "promote-1-v2-2",
                  "s1-v2-2-closure")
    if step_done("d2"):
        print("d2 already recorded; skipping")
        return
    rules, version = _active_rules()
    tasks = load_tasks("d2")

    async def go():
        runner = Runner("d2")
        runs = []
        for task in tasks:
            strat = choose_strategy(task, rules)
            if strat == INCOHERENT:
                ledger_append("incoherence", {
                    "phase": "d2", "task_id": task["id"],
                    "note": "active policy incoherent on d2 task"})
                continue
            seed = derive_seed("d2", task["id"], strat)
            rec = await runner.drive_run(task, strat, f"active-{version}",
                                         seed)
            runs.append(rec)
            ledger_append("run", rec)
        return runs

    runs = asyncio.run(go())
    ledger_append("step_complete", {"step": "d2", "n_runs": len(runs),
                                   "active_version": version})
    print(f"d2: {len(runs)} runs under {version}")


def cmd_learn_2():
    require_steps("d1", "learn-1", "s1-v2-2", "promote-1-v2-2", "s1-v2-2-closure", "d2")
    if step_done("learn-2"):
        print("learn-2 already recorded; skipping")
        return
    runs = [r["payload"] for r in ledger_load("run")
            if r["payload"].get("phase") in ("d1", "d2")]
    tasks_by_id = {}
    for name in ("d1", "d2"):
        for t in load_tasks(name):
            tasks_by_id[t["id"]] = t
    _, version = _active_rules()
    payload = learn_step("learn-2", runs, tasks_by_id, version, "cand_002")
    ledger_append("learn", payload)
    ledger_append("step_complete", {"step": "learn-2",
                                   "outcome": payload["outcome"]})
    print(f"learn-2: {payload['outcome']} ({payload['n_rules']} rules)")


def cmd_run_s2():
    require_steps("d1", "learn-1", "s1-v2-2", "promote-1-v2-2", "s1-v2-2-closure", "d2", "learn-2")
    if step_done("s2"):
        print("s2 already recorded; skipping")
        return
    candidate = _candidate_from_ledger("learn-2")
    if candidate is None:
        ledger_append("evaluation", {"step": "s2", "verdict": "SKIPPED",
                                     "reason": "no candidate to evaluate"})
        ledger_append("step_complete", {"step": "s2", "verdict": "SKIPPED"})
        print("s2: SKIPPED (no candidate)")
        return
    ev = asyncio.run(_intervention("s2", "s2", candidate, "eval_s2"))
    ledger_append("evaluation", {"step": "s2", **ev.canonical()})
    ledger_append("step_complete", {"step": "s2", "verdict": ev.verdict})
    print(f"s2: {ev.verdict}")


def cmd_promote_2():
    require_steps("d1", "learn-1", "s1-v2-2", "promote-1-v2-2", "s1-v2-2-closure", "d2", "learn-2",
                  "s2")
    if step_done("promote-2"):
        print("promote-2 already recorded; skipping")
        return
    learn = [r for r in ledger_load("learn")
             if r["payload"].get("step") == "learn-2"][-1]["payload"]
    if learn["outcome"] != "CANDIDATE":
        ledger_append("promotion", {"step": "promote-2",
                                    "decision": "NO_CANDIDATE",
                                    "reasons": ["no admissible candidate"]})
        ledger_append("step_complete", {"step": "promote-2",
                                       "decision": "NO_CANDIDATE"})
        print("promote-2: NO_CANDIDATE")
        return
    ev_recs = [r for r in ledger_load("evaluation")
               if r["payload"].get("step") == "s2"]
    if not ev_recs or ev_recs[-1]["payload"].get("verdict") == "SKIPPED":
        ledger_append("promotion", {"step": "promote-2",
                                    "decision": "NO_CANDIDATE",
                                    "reasons": ["no candidate evaluated"]})
        ledger_append("step_complete", {"step": "promote-2",
                                       "decision": "NO_CANDIDATE"})
        print("promote-2: NO_CANDIDATE")
        return
    ev_data = ev_recs[-1]["payload"]
    candidate = _rebuild_candidate(learn["candidate"])
    training_ids = {f"ev_learn-2_{i:03d}" for i in range(learn["n_records"])}
    av, reasons = policy_assurance(learn["candidate"], training_ids)
    decision = promotion.decide_promotion(
        id="promo_002", candidate=candidate,
        evaluation=_rebuild_evaluation(ev_data),
        assurance_verdict=av, assurance_id="asr_promo_002")
    payload = {"step": "promote-2", "decision": decision.decision,
               "reasons": list(decision.reasons),
               "evaluation_id": decision.evaluation_id,
               "assurance_verdict": av, "assurance_reasons": reasons}
    if decision.decision == "PROMOTE":
        payload["candidate"] = learn["candidate"]
        payload["version"] = decision.policy_version.version
        payload["policy_version"] = decision.policy_version.canonical()
    ledger_append("promotion", payload)
    ledger_append("step_complete", {"step": "promote-2",
                                   "decision": decision.decision})
    print(f"promote-2: {decision.decision}")


def cmd_run_d3():
    require_steps("d1", "learn-1", "s1-v2-2", "promote-1-v2-2", "s1-v2-2-closure", "d2", "learn-2",
                  "s2", "promote-2")
    if step_done("d3"):
        print("d3 already recorded; skipping")
        return
    rules, version = _active_rules()
    tasks = load_tasks("d3")

    async def go():
        runner = Runner("d3")
        runs = []
        for task in tasks:
            for label, rl in (("active", rules), ("parent", [])):
                strat = choose_strategy(task, rl)
                if strat == INCOHERENT:
                    ledger_append("incoherence", {
                        "phase": "d3", "task_id": task["id"],
                        "policy": label,
                        "note": "policy incoherent on d3 task"})
                    continue
                seed = derive_seed("d3", task["id"], label)
                rec = await runner.drive_run(task, strat, label, seed)
                runs.append(rec)
                ledger_append("run", rec)
        return runs

    runs = asyncio.run(go())
    ledger_append("step_complete", {"step": "d3", "n_runs": len(runs),
                                   "active_version": version})
    print(f"d3: {len(runs)} runs (active={version} + parent control)")


def cmd_report():
    runs = ledger_load("run")
    print(f"total runs: {len(runs)}")
    for phase in ("d1", "s1", "d2", "s2", "d3"):
        pr = [r["payload"] for r in runs if r["payload"].get("phase") == phase]
        if not pr:
            continue
        by_pol: dict[str, list] = {}
        for r in pr:
            by_pol.setdefault(r["policy"], []).append(r)
        for pol, rs in sorted(by_pol.items()):
            sup = sum(1 for r in rs
                      if r["evaluation_verdict"] == "SUPPORTED")
            fal = sum(1 for r in rs
                      if r["evaluation_verdict"] == "FALSIFIED")
            inc = len(rs) - sup - fal
            print(f"  {phase}/{pol}: n={len(rs)} SUPPORTED={sup} "
                  f"FALSIFIED={fal} other={inc}")
    for kind in ("learn", "promotion", "evaluation"):
        for r in ledger_load(kind):
            p = r["payload"]
            print(f"  {kind}: {p.get('step')} -> "
                  f"{p.get('outcome') or p.get('decision') or p.get('verdict')}")
    incoh = ledger_load("incoherence")
    if incoh:
        print(f"  incoherences: {len(incoh)}")
        for r in incoh:
            print(f"    {r['payload']}")


def cmd_prereg_check():
    """Verify the freeze: no file existing at 398ce14 was modified."""
    import subprocess
    out = subprocess.run(
        ["git", "diff", "398ce14", "--name-status", "--",
         "src/air/learning_v2", "src/air/allocation", "src/air/evaluation",
         "src/air/assurance", "src/air/evidence", "src/air/experiments/harness.py",
         "src/air/experiments/behaviors.py", "src/air/experiments/conditions.py",
         "src/air/experiments/evalkit.py", "src/air/experiments/metrics.py"],
        cwd=Path(__file__).parent.parent.parent,
        capture_output=True, text=True)
    mods = [l for l in out.stdout.splitlines()
            if l and not l.startswith("A\t")]
    if mods:
        print("FREEZE VIOLATION: modified frozen files:")
        print("\n".join(mods))
        sys.exit(1)
    print("freeze ok: no frozen files modified since 398ce14")


COMMANDS = {
    "prereg-check": cmd_prereg_check,
    "run-d1": cmd_run_d1,
    "learn-1": cmd_learn_1,
    "run-s1": cmd_run_s1,
    "promote-1": cmd_promote_1,
    "run-d2": cmd_run_d2,
    "learn-2": cmd_learn_2,
    "run-s2": cmd_run_s2,
    "promote-2": cmd_promote_2,
    "run-d3": cmd_run_d3,
    "report": cmd_report,
}


# ---------------------------------------------------------------------------
# v2.2: Powered promotion design.
# ---------------------------------------------------------------------------

def _evaluate_v2_2(eval_id: str, candidate_id: str,
                   parent_version: str, pool: str,
                   outcomes: list[dict]) -> dict:
    """v2.2 stratified evaluation with powered promotion gates.

    Returns a dict (not DiscriminatingEvaluation) with v2.2 verdict:
    PROMOTE, REJECT, or UNDERPOWERED.
    """
    from air.learning_v2.evaluation import (
        mcnemar_exact_onesided, wilson_lower)

    # Group by stratum
    by_stratum: dict[str, list[dict]] = {}
    for o in outcomes:
        s = o.get("stratum") or "unknown"
        by_stratum.setdefault(s, []).append(o)

    stats: dict = {"protocol": "v2.2", "strata": {}}

    # Production stratum: primary McNemar
    # v2.2 discordant pair definition (frozen):
    # - Strategies differ AND
    # - At least one side has a definitive outcome, where:
    #   * SUPPORTED (True) beats INCONCLUSIVE (None) beats FALSIFIED (False)
    #   * INCONCLUSIVE (None) vs SUPPORTED (True) = candidate win
    #     (candidate produced verified outcome where parent could not)
    #   * Both None = not informative, excluded
    prod = by_stratum.get("production", [])
    disc = []
    b = 0  # parent wins
    c_ = 0  # candidate wins
    for o in prod:
        if not o["differ"]:
            continue
        pv = o["parent_verified"]
        cv = o["candidate_verified"]
        # Both None: not informative
        if pv is None and cv is None:
            continue
        # Candidate win: candidate True and parent not True
        # (parent False or None)
        if cv is True and pv is not True:
            disc.append(o)
            c_ += 1
        # Parent win: parent True and candidate not True
        # (candidate False or None)
        elif pv is True and cv is not True:
            disc.append(o)
            b += 1
        # Both True or both False: concordant, not discordant
        # (but still counts for rates)
    p_val = mcnemar_exact_onesided(b, c_)

    prod_parent_rate = sum(1 for o in prod if o["parent_verified"]) / len(prod) if prod else 0
    prod_cand_rate = sum(1 for o in prod if o["candidate_verified"]) / len(prod) if prod else 0

    stats["strata"]["production"] = {
        "n": len(prod),
        "n_discordant": len(disc),
        "mcnemar_b": b,  # parent wins
        "mcnemar_c": c_,  # candidate wins
        "mcnemar_p_one_sided": p_val,
        "parent_verified_rate": round(prod_parent_rate, 4),
        "candidate_verified_rate": round(prod_cand_rate, 4),
        "candidate_wilson_lower": wilson_lower(
            sum(1 for o in prod if o["candidate_verified"]), len(prod)) if prod else 0,
    }

    # Per-stratum verified-success latency/cost and resource evaluability
    resource_gates = {}
    any_evaluable = False
    for stratum, rows in sorted(by_stratum.items()):
        p_supp = [o for o in rows if o["parent_verified"]]
        c_supp = [o for o in rows if o["candidate_verified"]]
        p_n, c_n = len(p_supp), len(c_supp)
        evaluable = p_n >= 5 and c_n >= 5
        if evaluable:
            any_evaluable = True
        # Verified-success means
        if p_n > 0:
            p_lat = sum(o["parent_resources"]["latency_ms"] for o in p_supp) / p_n
            p_cost = sum(o["parent_resources"]["cost"] for o in p_supp) / p_n
        else:
            p_lat, p_cost = None, None
        if c_n > 0:
            c_lat = sum(o["candidate_resources"]["latency_ms"] for o in c_supp) / c_n
            c_cost = sum(o["candidate_resources"]["cost"] for o in c_supp) / c_n
        else:
            c_lat, c_cost = None, None

        strat_stats = {
            "n": len(rows),
            "parent_n_verified": p_n,
            "candidate_n_verified": c_n,
            "resource_evaluable": evaluable,
            "parent_verified_latency_mean": round(p_lat, 2) if p_lat is not None else None,
            "candidate_verified_latency_mean": round(c_lat, 2) if c_lat is not None else None,
            "parent_verified_cost_mean": round(p_cost, 4) if p_cost is not None else None,
            "candidate_verified_cost_mean": round(c_cost, 4) if c_cost is not None else None,
        }
        # Preserve McNemar stats for production stratum (set earlier)
        if stratum == "production" and stratum in stats["strata"]:
            for k in ("n_discordant", "mcnemar_b", "mcnemar_c",
                      "mcnemar_p_one_sided", "parent_verified_rate",
                      "candidate_verified_rate", "candidate_wilson_lower"):
                if k in stats["strata"][stratum]:
                    strat_stats[k] = stats["strata"][stratum][k]
        # 2.0x gates (hard, per Inan)
        if evaluable:
            lat_ratio = c_lat / p_lat if p_lat and p_lat > 0 else float("inf")
            cost_ratio = c_cost / p_cost if p_cost and p_cost > 0 else (0 if c_cost == 0 else float("inf"))
            # Handle zero-cost case: if both zero, ratio is 1.0 (pass)
            if p_cost == 0 and c_cost == 0:
                cost_ratio = 1.0
            strat_stats["latency_ratio"] = round(lat_ratio, 3)
            strat_stats["cost_ratio"] = round(cost_ratio, 3)
            strat_stats["latency_gate_pass"] = lat_ratio <= 2.0
            strat_stats["cost_gate_pass"] = cost_ratio <= 2.0
            resource_gates[stratum] = strat_stats["latency_gate_pass"] and strat_stats["cost_gate_pass"]
        stats["strata"][stratum] = strat_stats

    stats["any_resource_evaluable"] = any_evaluable
    stats["resource_gates"] = resource_gates

    # Safety
    safety_violations = sum(
        len(o.get("candidate_safety_violations", [])) for o in outcomes)
    stats["candidate_safety_violations"] = safety_violations

    # v2.2 verdict logic
    n_disc = len(disc)
    notes = []

    # Underpowered check (frozen: <18 discordant)
    if n_disc < 18:
        verdict = "UNDERPOWERED"
        notes.append(f"realized discordant pairs ({n_disc}) below design target (18)")
    else:
        # P1: McNemar
        p1_pass = p_val < 0.05
        if not p1_pass:
            notes.append(f"McNemar p={p_val:.4f} >= 0.05")
        # P2: Safety
        p2_pass = safety_violations == 0
        if not p2_pass:
            notes.append(f"safety violations: {safety_violations}")
        # P3: Resource gates
        if not any_evaluable:
            verdict = "NOT_ELIGIBLE"
            notes.append("resource gate UNEVALUABLE: no stratum with n>=5 for both")
        else:
            p3_pass = all(resource_gates.values())
            if not p3_pass:
                failed = [s for s, v in resource_gates.items() if not v]
                notes.append(f"resource gate failed on: {failed}")
            # Final
            if p1_pass and p2_pass and p3_pass:
                verdict = "PROMOTE"
            else:
                verdict = "REJECT"

    stats["verdict_reason"] = "; ".join(notes) if notes else "all v2.2 gates pass"
    stats["n_discordant_production"] = n_disc

    return {
        "id": eval_id,
        "candidate_id": candidate_id,
        "parent_version": parent_version,
        "sealed_pool_id": pool,
        "protocol": "v2.2",
        "verdict": verdict,
        "statistics": stats,
        "paired_outcomes": outcomes,
    }


def cmd_run_s1_v2_2():
    """Run the v2.2 S1 sealed intervention (60 stratified tasks).

    Runs under the same experiment ID as v2.1 (D1/learn-1 already present).
    The v2.1 S1 is quarantined as S1-pilot; this is the fresh powered S1.
    """
    require_steps("d1", "learn-1")
    if step_done("s1-v2-2"):
        print("s1-v2-2 already recorded; skipping")
        return
    candidate = _candidate_from_ledger("learn-1")
    if candidate is None:
        print("s1-v2-2: SKIPPED (no candidate)")
        return
    # Run paired intervention on v2.2 pool
    ev = asyncio.run(_intervention("s1_v2_2", "s1-v2-2", candidate, "eval_s1_v2_2"))
    # _intervention returns v2.1 evaluation; we need v2.2.
    # Re-evaluate from the paired outcomes with v2.2 logic.
    outcomes = list(ev.paired_outcomes) if hasattr(ev, "paired_outcomes") else []
    # Outcomes are dicts; convert if needed
    v2_2 = _evaluate_v2_2(
        "eval_s1_v2_2", candidate.id, _active_version(), "s1_v2_2", outcomes)
    ledger_append("evaluation_v2_2", {"step": "s1-v2-2", **v2_2})
    ledger_append("step_complete", {"step": "s1-v2-2",
                                    "verdict": v2_2["verdict"]})
    print(f"s1-v2-2: {v2_2['verdict']}")


def cmd_promote_1_v2_2():
    """Apply the v2.2 promotion rule."""
    require_steps("d1", "learn-1", "s1-v2-2")
    if step_done("promote-1-v2-2"):
        print("promote-1-v2-2 already recorded; skipping")
        return
    # Load v2.2 evaluation
    ev_recs = [r for r in ledger_load("evaluation_v2_2")
               if r["payload"].get("step") == "s1-v2-2"]
    if not ev_recs:
        print("promote-1-v2-2: no v2.2 evaluation found")
        return
    ev = ev_recs[-1]["payload"]
    verdict = ev["verdict"]

    # Assurance check (same as v2.1)
    learn = [r for r in ledger_load("learn")
             if r["payload"].get("step") == "learn-1"][-1]["payload"]
    candidate = _rebuild_candidate(learn["candidate"])
    training_ids = {f"ev_learn-1_{i:03d}" for i in range(learn["n_records"])}
    av, reasons = policy_assurance(learn["candidate"], training_ids)

    # v2.2 decision mapping
    # v2.2 verdict PROMOTE + assurance SOUND => PROMOTE
    # v2.2 verdict UNDERPOWERED => UNDERPOWERED (not REJECT)
    # Otherwise => REJECT
    if verdict == "PROMOTE" and av == "SOUND":
        decision = "PROMOTE"
    elif verdict == "UNDERPOWERED":
        decision = "UNDERPOWERED"
    else:
        decision = "REJECT"
    if av != "SOUND":
        decision = "REJECT"
        reasons = reasons + ["assurance not SOUND"]

    payload = {"step": "promote-1-v2-2", "decision": decision,
               "v2_2_verdict": verdict,
               "reasons": [ev["statistics"]["verdict_reason"]] + list(reasons),
               "evaluation_id": ev["id"],
               "assurance_verdict": av}
    if decision == "PROMOTE":
        # Create immutable PolicyVersion (same as v2.1)
        from air.learning_v2 import promotion as promo_mod
        dec = promo_mod.decide_promotion(
            id="promo_001_v2_2", candidate=candidate,
            evaluation=_rebuild_evaluation_v2_2(ev),
            assurance_verdict=av, assurance_id="asr_promo_001_v2_2")
        # Use the v2.2 verdict, not the recomputed one
        payload["policy_version"] = dec.policy_version.canonical() if dec.policy_version else None
    ledger_append("promotion", payload)
    ledger_append("step_complete", {"step": "promote-1-v2-2",
                                    "decision": decision})
    print(f"promote-1-v2-2: {decision}")


def _rebuild_evaluation_v2_2(data: dict):
    """Rebuild a DiscriminatingEvaluation from v2.2 data for promotion."""
    from air.learning_v2.contracts import DiscriminatingEvaluation
    # Map v2.2 verdict to v2.1-compatible verdict for decide_promotion
    # v2.2 PROMOTE => PASS (evaluation passed, ready for promotion)
    # v2.2 REJECT/UNDERPOWERED => FAIL (should not happen here, but map safely)
    v2_2_verdict = data["verdict"]
    compat_verdict = "PASS" if v2_2_verdict == "PROMOTE" else "FAIL"
    return DiscriminatingEvaluation(
        id=data["id"], candidate_id=data["candidate_id"],
        parent_version=data["parent_version"],
        sealed_pool_id=data["sealed_pool_id"],
        decision_delta_task_ids=tuple(
            o["task_id"] for o in data["paired_outcomes"] if o["differ"]),
        paired_outcomes=tuple(data["paired_outcomes"]),
        statistics=data["statistics"], verdict=compat_verdict)


# Register v2.2 commands (defined above, after COMMANDS dict).
COMMANDS["run-s1-v2-2"] = cmd_run_s1_v2_2
COMMANDS["promote-1-v2-2"] = cmd_promote_1_v2_2


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd not in COMMANDS:
        print(f"usage: python -m air.experiments.v2harness "
              f"{'|'.join(COMMANDS)}")
        sys.exit(2)
    COMMANDS[cmd]()
