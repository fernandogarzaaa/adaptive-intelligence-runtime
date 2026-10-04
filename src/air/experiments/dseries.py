"""D-series learning experiment driver (d-series-v1).

    D1 -> Learning 1 -> D2 -> Learning 2 -> D3

D1 reproduces the baseline-C failure under cognitive-allocation@v1.
Each learning boundary runs the PRODUCT learning machinery unmodified:
LearningEngine.propose_policy_update() -> trial validation under the
candidate -> evaluate_policy_candidate -> assurance -> promotion gate.
Only knowledge passing the existing gates influences later phases.

D3 uses tasks frozen before D1 (tasks/d3, hash in the pre-reg) to test
generalization vs overfitting.

Product/allocator code is FROZEN: this module only drives the public
entrypoints and the learning pipeline. It never hand-edits allocation.

Usage:
    python -m air.experiments.dseries_prereg --exp-dir <dir>   # FIRST
    python -m air.experiments.dseries --exp-dir <dir> --phase D1
    python -m air.experiments.dseries --exp-dir <dir> --boundary L1
    python -m air.experiments.dseries --exp-dir <dir> --phase D2
    python -m air.experiments.dseries --exp-dir <dir> --boundary L2
    python -m air.experiments.dseries --exp-dir <dir> --phase D3
    python -m air.experiments.dseries --exp-dir <dir> --report
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import random
import shutil
import sqlite3
import sys
import time
from pathlib import Path

from air.agents.runtime import AgentRuntime
from air.config import AirConfig
from air.experiments import behaviors, evalkit, metrics, registry
from air.experiments.conditions import (
    AGENT_BUDGET,
    COST_BUDGET_USD,
    TOKEN_BUDGET,
    TOOL_CALL_BUDGET,
    WALL_TIME_BUDGET_S,
    apply_experiment_grants,
    register_task_for_run,
    seed_workspace,
    task_input_hash,
)
from air.experiments.harness import (
    TERMINAL_RUN_STATUSES,
    derive_seed,
)
from air.persistence.db import Database, find_migrations_dir

EXP_ID = "d-series-v1"
V1_TASKS = Path(__file__).parent / "tasks" / "v1" / "tasks.json"
D3_TASKS = Path(__file__).parent / "tasks" / "d3" / "tasks.json"

# Learning-boundary validation: the known failures plus a success and
# the negative control. Task-agnostic selection: covers failure,
# success, and designed-failure shapes.
VALIDATION_TASKS = ["t05", "t07", "t01", "t16"]
VALIDATION_REPS = 2

POLICY_NAME = "cognitive-allocation"


def load_tasks(which: str) -> list[dict]:
    path = V1_TASKS if which == "v1" else D3_TASKS
    return json.loads(path.read_text())["tasks"]


class DSeriesDriver:
    """Drives one phase (D1/D2/D3) against its own isolated ledger DB."""

    def __init__(self, exp_dir: str):
        self.exp_dir = exp_dir
        self._runtimes: dict[str, AgentRuntime] = {}

    def db_path(self, phase: str) -> Path:
        return Path(self.exp_dir) / "ledgers" / phase / "air.db"

    def runtime_for(self, phase: str) -> AgentRuntime:
        if phase not in self._runtimes:
            data_dir = Path(self.exp_dir) / "ledgers" / phase
            data_dir.mkdir(parents=True, exist_ok=True)
            db_path = data_dir / "air.db"
            if db_path.exists():
                db_path.unlink()
            db = Database(db_path)
            db.migrate(find_migrations_dir())
            rt = AgentRuntime(AirConfig(data_dir=data_dir), db)
            for role in behaviors.ROLE_PHASES:
                rt.register_behavior(role, behaviors.experiment_behavior)
            self._runtimes[phase] = rt
        return self._runtimes[phase]

    def seed_policy_from(self, phase: str, source_db: str) -> None:
        """Copy the policy tables from a learning-boundary DB into the
        phase DB. The phase inherits exactly the promoted policy state;
        nothing else crosses the boundary."""
        dest = self.db_path(phase)
        src = sqlite3.connect(source_db)
        dst = sqlite3.connect(str(dest))
        try:
            for table in ("policies", "policy_versions"):
                rows = src.execute(f"SELECT * FROM {table}").fetchall()
                cols = [c[1] for c in src.execute(
                    f"PRAGMA table_info({table})").fetchall()]
                dst.execute(f"DELETE FROM {table}")
                q = (f"INSERT INTO {table} ({','.join(cols)}) VALUES "
                     f"({','.join('?' * len(cols))})")
                dst.executemany(q, rows)
            dst.commit()
        finally:
            src.close()
            dst.close()

    async def drive_run(self, phase: str, task: dict, repetition: int) -> dict:
        """One task run under the phase's policy. Mirrors the baseline
        harness: public entrypoints only, post-hoc evaluation."""
        rt = self.runtime_for(phase)
        seed = derive_seed(EXP_ID, phase, task["id"], repetition)
        random.seed(seed)
        run_id = await rt.create_run(
            task["goal"],
            strategy=None,  # dynamic, like baseline C
            tool_call_budget=TOOL_CALL_BUDGET,
            time_budget_s=WALL_TIME_BUDGET_S,
            agent_budget=AGENT_BUDGET,
            token_budget=TOKEN_BUDGET,
            cost_budget_usd=COST_BUDGET_USD,
            seed=seed,
        )
        apply_experiment_grants(rt, run_id)
        data_dir = Path(rt.config.data_dir)
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

        db_path = Path(rt.db.path)
        evaluation = evalkit.evaluate_run(rt.db.conn, run_id)
        assurance = evalkit.assure_run(rt.db.conn, evaluation.id)
        m = metrics.compute_run_metrics(str(db_path), run_id, latency_s)
        record = {
            "run_id": run_id,
            "phase": phase,
            "task_id": task["id"],
            "task_kind": task.get("kind", ""),
            "repetition": repetition,
            "seed": seed,
            "task_input_hash": input_hash,
            "suite_id": evalkit.SUITE_ID,
            "suite_version": evalkit.SUITE_VERSION,
            "policy_version": m["policy_version"],
            "strategy": m["strategy"],
            "wall_exceeded": wall_exceeded,
            "metrics": m,
            "ledger": os.path.relpath(str(db_path), self.exp_dir),
        }
        return registry.append(self.exp_dir, "run", record)

    async def _wait_for_completion(self, rt: AgentRuntime, run_id: str) -> str:
        while True:
            row = rt.db.conn.execute(
                "SELECT status FROM runs WHERE id=?", (run_id,)).fetchone()
            status = row[0] if row else "UNKNOWN"
            if status in TERMINAL_RUN_STATUSES:
                return status
            await asyncio.sleep(0.05)

    async def run_phase(self, phase: str, tasks: list[dict],
                        repetitions: int) -> None:
        for rep in range(repetitions):
            for task in tasks:
                rec = await self.drive_run(phase, task, repetition=rep)
                print(f"  {phase} {task['id']} rep{rep}: "
                      f"{rec['payload']['strategy']} "
                      f"{rec['payload']['metrics'].get('verdict', '?')}",
                      flush=True)


async def learning_boundary(exp_dir: str, label: str,
                            source_phase: str) -> dict:
    """Run one learning boundary (L1 after D1, L2 after D2).

    Uses ONLY the product machinery plus task-agnostic plumbing:
    LearningEngine.propose_policy_update -> trial validation under the
    candidate -> evaluate_policy_candidate -> assurance ->
    promotion gate. Returns the attribution record (9 fields).
    """
    from air.learning.engine import LearningEngine
    from air.learning.policies import PolicyStore
    from air.learning.policy_eval import evaluate_policy_candidate

    exp_path = Path(exp_dir)
    work_dir = exp_path / "learning" / label.lower()
    work_dir.mkdir(parents=True, exist_ok=True)
    # Work on a COPY: the phase ledger stays pristine.
    src_db = exp_path / "ledgers" / source_phase / "air.db"
    work_db = work_dir / "air.db"
    if work_db.exists():
        work_db.unlink()
    shutil.copy(str(src_db), str(work_db))

    conn = sqlite3.connect(str(work_db))
    attribution: dict = {
        "policy_version": None,
        "parent_policy_version": None,
        "source_experiences": [],
        "hypothesis": "",
        "proposed_change": {},
        "expected_effect": {},
        "evaluation_id": None,
        "assurance_id": None,
        "promotion_decision": "NOT_PROPOSED",
    }
    try:
        engine = LearningEngine(conn)
        proposal = await engine.propose_policy_update()
        if proposal is None:
            attribution["promotion_decision"] = (
                "NOT_PROPOSED: engine found insufficient evidence or no changes")
            _write_attribution(work_dir, attribution)
            registry.append(exp_dir, "learning_boundary",
                            {"label": label, "attribution": attribution})
            return attribution

        store = PolicyStore(conn)
        ver = store.get_version(store.ensure(POLICY_NAME), proposal["version"])
        attribution.update({
            "policy_version": ver.version,
            "parent_policy_version": ver.parent_version,
            "source_experiences": list(ver.source_experiences),
            "hypothesis": ver.hypothesis,
            "proposed_change": dict(ver.changes),
            "expected_effect": dict(ver.expected_effect),
        })

        # Trial validation under the candidate (held-out validation is
        # the designed step; trial activation is recorded, not a promotion).
        trial_note = ("trial activation of candidate v%s for held-out "
                      "validation; not a promotion" % ver.version)
        conn.execute("UPDATE policies SET current_version=? WHERE name=?",
                     (ver.version, POLICY_NAME))
        conn.commit()
        registry.append(exp_dir, "learning_boundary",
                        {"label": label, "event": "trial_activation",
                         "version": ver.version, "note": trial_note})
        try:
            driver = DSeriesDriver(exp_dir)
            # Point the driver at the learning DB for validation runs.
            driver._runtimes["__val__"] = _runtime_on_db(work_db, exp_dir)
            v1_tasks = {t["id"]: t for t in load_tasks("v1")}
            for rep in range(VALIDATION_REPS):
                for tid in VALIDATION_TASKS:
                    await driver.drive_run("__val__", v1_tasks[tid], rep)
        finally:
            conn.execute("UPDATE policies SET current_version=? WHERE name=?",
                         (ver.parent_version, POLICY_NAME))
            conn.commit()

        # Independent evaluation of the candidate vs the parent.
        eval_id, verdict, dims = evaluate_policy_candidate(
            conn, POLICY_NAME, ver.version)
        attribution["evaluation_id"] = eval_id
        # Assurance over the policy evaluation.
        assurance = evalkit.assure_run(conn, eval_id)
        attribution["assurance_id"] = assurance.id
        # Promotion gate.
        try:
            await store.promote(
                POLICY_NAME, ver.version,
                evaluation={"id": eval_id, "verdict": verdict.value},
                assurance={"id": assurance.id,
                           "evaluator_verdict": assurance.evaluator_verdict.value,
                           "system_verdict": assurance.system_verdict.value})
            attribution["promotion_decision"] = "PROMOTED"
        except Exception as e:  # GateBlocked or any gate failure
            attribution["promotion_decision"] = f"BLOCKED: {e}"
    finally:
        conn.close()
    _write_attribution(work_dir, attribution)
    registry.append(exp_dir, "learning_boundary",
                    {"label": label, "attribution": attribution})
    # The next phase seeds its policy tables from this DB's final state.
    return attribution


def _runtime_on_db(db_path: Path, exp_dir: str) -> AgentRuntime:
    """An AgentRuntime bound to an existing DB (for validation runs)."""
    data_dir = db_path.parent
    db = Database(db_path)
    rt = AgentRuntime(AirConfig(data_dir=data_dir), db)
    for role in behaviors.ROLE_PHASES:
        rt.register_behavior(role, behaviors.experiment_behavior)
    return rt


def _write_attribution(work_dir: Path, attribution: dict) -> None:
    with open(work_dir / "attribution.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(attribution, sort_keys=True) + "\n")


def main() -> None:
    args = sys.argv[1:]
    exp_dir = phase = boundary = None
    do_report = False
    for i, a in enumerate(args):
        if a == "--exp-dir" and i + 1 < len(args):
            exp_dir = args[i + 1]
        elif a == "--phase" and i + 1 < len(args):
            phase = args[i + 1]
        elif a == "--boundary" and i + 1 < len(args):
            boundary = args[i + 1]
        elif a == "--report":
            do_report = True
    if not exp_dir:
        print("usage: python -m air.experiments.dseries --exp-dir <dir> "
              "[--phase D1|D2|D3 | --boundary L1|L2 | --report]",
              file=sys.stderr)
        sys.exit(2)
    if phase:
        tasks = load_tasks("d3" if phase == "D3" else "v1")
        # D2/D3 seed policy tables from the preceding learning boundary.
        driver = DSeriesDriver(exp_dir)
        if phase in ("D2", "D3"):
            src_label = "l1" if phase == "D2" else "l2"
            src_db = Path(exp_dir) / "learning" / src_label / "air.db"
            if not src_db.exists():
                print(f"no learning boundary db for {phase}: run --boundary "
                      f"{src_label.upper()} first", file=sys.stderr)
                sys.exit(2)
            # Create the phase DB first, then seed policy tables into it.
            driver.runtime_for(phase)
            driver.seed_policy_from(phase, str(src_db))
            cur = sqlite3.connect(
                str(driver.db_path(phase))).execute(
                "SELECT current_version FROM policies WHERE name=?",
                (POLICY_NAME,)).fetchone()
            print(f"{phase} policy state: {POLICY_NAME}@{cur[0]}")
        asyncio.run(driver.run_phase(phase, tasks, 5))
    elif boundary:
        label = boundary  # L1 or L2
        source = "D1" if label == "L1" else "D2"
        attribution = asyncio.run(learning_boundary(exp_dir, label, source))
        print(f"{label} attribution: {json.dumps(attribution, indent=1)[:800]}")
    elif do_report:
        from air.experiments import dseries_report
        dseries_report.write_report(exp_dir)
    else:
        print("specify --phase, --boundary, or --report", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
