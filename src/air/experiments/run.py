"""Run the baseline experiment: A/B/C under the frozen protocol.

Gates (mechanical, not honor-system):
1. The registry must hold a passing pre-run protocol_verification
   record (python -m air.experiments.verify_protocol --phase pre).
2. A pre-registration record must exist and must precede the first
   run record (python -m air.experiments.prereg).

If either gate fails, the harness refuses to drive any run.

Usage:
    python -m air.experiments.run --exp-dir <dir> --exp-id <id> \\
        [--repetitions N] [--conditions A,B,C]

Artifacts (ledgers + registry + report inputs) land under --exp-dir,
which must be OUTSIDE the repo (e.g. ~/workspace/air-experiments/<id>).
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from air.experiments import registry
from air.experiments.conditions import CONDITIONS
from air.experiments.harness import ExperimentHarness

TASKS_PATH = Path(__file__).parent / "tasks" / "v1" / "tasks.json"


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(
        description="Run the A/B/C baseline experiment")
    ap.add_argument("--exp-dir", required=True)
    ap.add_argument("--exp-id", required=True)
    ap.add_argument("--repetitions", type=int, default=5)
    ap.add_argument("--conditions", default="A,B,C")
    args = ap.parse_args()

    if "workspace/air-experiments" not in args.exp_dir.replace(
            str(Path.home()), "~"):
        print("WARNING: exp-dir should be outside the repo "
              "(~/workspace/air-experiments/<id>)", file=sys.stderr)

    tasks = json.loads(TASKS_PATH.read_text())["tasks"]
    conds = [c.strip() for c in args.conditions.split(",")]
    for c in conds:
        if c not in CONDITIONS:
            print(f"unknown condition: {c}", file=sys.stderr)
            return 2

    harness = ExperimentHarness(args.exp_dir, args.exp_id)
    ok, reason = harness.check_gates()
    if not ok:
        print(f"experiment refused: {reason}", file=sys.stderr)
        return 1
    registry.append(args.exp_dir, "experiment_started", {
        "exp_id": args.exp_id,
        "conditions": conds,
        "repetitions": args.repetitions,
        "n_tasks": len(tasks),
    })
    print(f"starting experiment {args.exp_id}: "
          f"{len(conds)} conditions x {args.repetitions} reps "
          f"x {len(tasks)} tasks = "
          f"{len(conds) * args.repetitions * len(tasks)} runs")
    asyncio.run(harness.run_all(tasks, args.repetitions, conds))
    n_runs = len(registry.load(args.exp_dir, "run"))
    print(f"experiment complete: {n_runs} runs recorded")
    print("next: python -m air.experiments.verify_protocol "
          "--exp-dir <dir> --phase post")
    print("then: python -m air.experiments.report --exp-dir <dir>")
    return 0


if __name__ == "__main__":
    sys.exit(main())
