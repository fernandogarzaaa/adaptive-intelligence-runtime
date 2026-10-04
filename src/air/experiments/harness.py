"""Experiment orchestration: drive runs, evaluate post-hoc, record.

One AgentRuntime per condition, each with its own database under
``<exp_dir>/ledgers/<cond>/``: physical experience-store isolation,
so no cross-condition reads are possible (contamination point 8).
Runs within a condition execute sequentially; each run is fully
isolated by run_id namespacing in the shared workspace.

Per run:
1. create_run (public) with the pinned strategy (or none for C),
   held-constant budgets, and the derived seed.
2. apply_experiment_grants: the held-constant grants policy
   (disclosed in conditions.py).
3. Seed the run's workspace namespace; register the task for
   behaviors (by run_id; no condition label anywhere).
4. start_run (public); poll runs.status until terminal or the
   harness wall-time cap fires. On timeout: cancel_run (public),
   record wall_exceeded. (The runtime stores but does not enforce
   time_limit_s; the harness enforces it externally. Documented.)
5. Post-hoc, from the immutable ledger: evaluate with the frozen
   suite, run assurance probes, compute metrics, append the run
   record to the registry.

The harness refuses to drive any run unless the registry holds a
passing pre-run protocol verification AND a pre-registration record
that precedes any run record.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import random
import time
from pathlib import Path

from air.agents.runtime import AgentRuntime
from air.config import AirConfig
from air.experiments import behaviors
from air.experiments.conditions import (
    AGENT_BUDGET,
    CONDITIONS,
    COST_BUDGET_USD,
    TOKEN_BUDGET,
    TOOL_CALL_BUDGET,
    WALL_TIME_BUDGET_S,
    apply_experiment_grants,
    register_task_for_run,
    seed_workspace,
    task_input_hash,
)
from air.experiments import evalkit, metrics, registry
from air.persistence.db import Database, find_migrations_dir

TERMINAL_RUN_STATUSES = ("COMPLETED", "FAILED", "CANCELLED")


def derive_seed(exp_id: str, condition_key: str, task_id: str,
                repetition: int) -> int:
    """Deterministic per-run seed. Recorded in pre-reg and run records.

    60 bits: fits in a SQLite INTEGER (signed 63-bit) with margin.
    """
    raw = f"{exp_id}|{condition_key}|{task_id}|{repetition}".encode()
    return int(hashlib.sha256(raw).hexdigest()[:15], 16)


class ExperimentHarness:
    def __init__(self, exp_dir: str, exp_id: str):
        self.exp_dir = exp_dir
        self.exp_id = exp_id
        self._runtimes: dict[str, AgentRuntime] = {}

    # ------------------------------------------------------------ setup
    def runtime_for(self, condition_key: str) -> AgentRuntime:
        """One runtime per condition (fresh DB = isolated ledger)."""
        if condition_key not in self._runtimes:
            data_dir = Path(self.exp_dir) / "ledgers" / condition_key
            data_dir.mkdir(parents=True, exist_ok=True)
            db_path = data_dir / "air.db"
            if db_path.exists():
                db_path.unlink()
            db = Database(db_path)
            db.migrate(find_migrations_dir())
            rt = AgentRuntime(AirConfig(data_dir=data_dir), db)
            for role in behaviors.ROLE_PHASES:
                rt.register_behavior(role, behaviors.experiment_behavior)
            self._runtimes[condition_key] = rt
        return self._runtimes[condition_key]

    def db_path_for(self, condition_key: str) -> str:
        return str(Path(self.exp_dir) / "ledgers" / condition_key / "air.db")

    # ------------------------------------------------------------ gates
    def check_gates(self) -> tuple[bool, str]:
        """Refuse to run unless verification passed and prereg precedes."""
        ok, detail = registry.verify_chain(self.exp_dir)
        if not ok:
            return False, f"registry chain broken: {detail}"
        ver = [r for r in registry.load(self.exp_dir, "protocol_verification")
               if r["payload"].get("phase") == "pre"
               and r["payload"].get("passed") is True]
        if not ver:
            return False, ("no passing pre-run protocol_verification record; "
                           "run python -m air.experiments.verify_protocol first")
        pre_seq = registry.first_seq_of(self.exp_dir, "pre_registration")
        if pre_seq is None:
            return False, ("no pre_registration record; run "
                           "python -m air.experiments.prereg first")
        run_seq = registry.first_seq_of(self.exp_dir, "run")
        if run_seq is not None and run_seq < pre_seq:
            return False, "a run record precedes pre-registration: refusing"
        return True, "gates pass: verification ok, pre-registration precedes"

    # ------------------------------------------------------------ one run
    async def drive_run(self, condition_key: str, task: dict,
                        repetition: int) -> dict:
        """Drive a single task run end-to-end. Returns the run record."""
        cfg = CONDITIONS[condition_key]
        rt = self.runtime_for(condition_key)
        seed = derive_seed(self.exp_id, condition_key, task["id"],
                           repetition)
        random.seed(seed)  # seed every RNG, even though we use little

        run_id = await rt.create_run(
            task["goal"],
            strategy=cfg.force_strategy,
            tool_call_budget=TOOL_CALL_BUDGET,
            time_budget_s=WALL_TIME_BUDGET_S,
            agent_budget=AGENT_BUDGET,
            token_budget=TOKEN_BUDGET,
            cost_budget_usd=COST_BUDGET_USD,
            seed=seed,
        )
        apply_experiment_grants(rt, run_id)
        data_dir = Path(self.exp_dir) / "ledgers" / condition_key
        seed_workspace(data_dir, run_id, task)
        register_task_for_run(run_id, task)
        input_hash = task_input_hash(task)

        wall_exceeded = False
        t0 = time.perf_counter()
        await rt.start_run(run_id)
        try:
            await asyncio.wait_for(
                self._wait_for_completion(rt, run_id),
                timeout=WALL_TIME_BUDGET_S)
        except asyncio.TimeoutError:
            wall_exceeded = True
            await rt.cancel_run(run_id)
        latency_s = time.perf_counter() - t0

        # Post-hoc evaluation + assurance from the immutable ledger.
        db_path = self.db_path_for(condition_key)
        evaluation = evalkit.evaluate_run(rt.db.conn, run_id)
        assurance = evalkit.assure_run(rt.db.conn, evaluation.id)
        m = metrics.compute_run_metrics(db_path, run_id, latency_s)

        record = {
            "run_id": run_id,
            "condition": condition_key,
            "condition_name": cfg.name,
            "task_id": task["id"],
            "task_kind": task.get("kind", ""),
            "repetition": repetition,
            "seed": seed,
            "task_input_hash": input_hash,
            "suite_id": evalkit.SUITE_ID,
            "suite_version": evalkit.SUITE_VERSION,
            "assurance_probe_ids": evalkit.probe_ids(assurance),
            "policy_version": m["policy_version"],
            "strategy": m["strategy"],
            "wall_exceeded": wall_exceeded,
            "metrics": m,
            "ledger": os.path.relpath(db_path, self.exp_dir),
        }
        return registry.append(self.exp_dir, "run", record)

    async def _wait_for_completion(self, rt: AgentRuntime,
                                   run_id: str) -> str:
        while True:
            row = rt.db.conn.execute(
                "SELECT status FROM runs WHERE id=?", (run_id,)).fetchone()
            status = row[0] if row else "UNKNOWN"
            if status in TERMINAL_RUN_STATUSES:
                return status
            await asyncio.sleep(0.05)

    # ------------------------------------------------------------ all runs
    async def run_all(self, tasks: list[dict], repetitions: int,
                      conditions: list[str] | None = None):
        """Drive every (condition, repetition, task) run in fixed order.

        Task order is fixed identically across conditions
        (contamination point 11): the task list order, documented as
        not a training signal because learning is off and ledgers are
        per-condition isolated.
        """
        ok, reason = self.check_gates()
        if not ok:
            raise RuntimeError(f"experiment refused: {reason}")
        for cond_key in (conditions or ["A", "B", "C"]):
            for rep in range(repetitions):
                for task in tasks:
                    await self.drive_run(cond_key, task, rep)
