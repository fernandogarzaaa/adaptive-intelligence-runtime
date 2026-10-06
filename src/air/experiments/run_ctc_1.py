"""CTC-1 execution: frozen pv2 (active) vs v1 parent on 48 CTC tasks.

This module is SEPARATE from the frozen harness (v2harness.py).
It imports only frozen functions and adds no scientific logic.

Execution rules (preregistered, Inan 2026-10-06):
1. Run all 48 tasks from the sealed pool.
2. Do not regenerate failed tasks.
3. Do not modify pv2, v1, allocator logic, evaluator, assurance
   logic, or task definitions.
4. Do not inspect outcomes and then change execution parameters.
5. Preserve every raw run, including failures and protocol anomalies.
6. Keep the frozen prediction artifact completely separate from
   execution results.
7. Record the full decision decomposition for every run.
8. Apply the preregistered interpretation matrix mechanically
   (in the analysis script, not during execution).

Usage:
    PYTHONPATH=src AIR_EXP_ID=dseries-v2-1 python -m air.experiments.run_ctc_1
"""

import asyncio

from air.experiments.v2harness import (
    Runner,
    _active_rules,
    choose_strategy,
    decision_decomposition,
    derive_seed,
    ledger_append,
    load_tasks,
    require_steps,
    step_done,
    INCOHERENT,
)


def main():
    # CTC-1 requires its own freeze (which itself requires pv2 frozen).
    require_steps("ctc-1-freeze")
    if step_done("ctc-1"):
        print("ctc-1 already recorded; skipping")
        return
    rules, version = _active_rules()
    assert version == "v2", f"CTC-1 requires frozen pv2, got {version}"
    assert len(rules) == 12, f"CTC-1 requires 12 frozen rules, got {len(rules)}"
    tasks = load_tasks("ctc_1")
    assert len(tasks) == 48, f"CTC-1 requires 48 sealed tasks, got {len(tasks)}"
    print(f"ctc-1: {len(tasks)} tasks, policy={version} "
          f"({len(rules)} rules, frozen)")

    async def go():
        runner = Runner("ctc-1")
        for task in tasks:
            # Record decision decomposition BEFORE execution (frozen audit)
            decomp = decision_decomposition(task, rules)
            ledger_append("ctc1_decomposition", decomp)
            for label, rl in (("active", rules), ("parent", [])):
                strat = choose_strategy(task, rl)
                if strat == INCOHERENT:
                    ledger_append("incoherence", {
                        "phase": "ctc-1", "task_id": task["id"],
                        "policy": label,
                        "note": "policy incoherent on ctc-1 task"})
                    continue
                seed = derive_seed("ctc-1", task["id"], label)
                rec = await runner.drive_run(task, strat, label, seed)
                ledger_append("run", rec)

    asyncio.run(go())
    ledger_append("step_complete", {"step": "ctc-1",
                                    "n_tasks": len(tasks)})
    print(f"ctc-1: {len(tasks)} tasks x 2 policies complete")


if __name__ == "__main__":
    main()
