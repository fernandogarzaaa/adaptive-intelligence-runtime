"""Pre-registration writer (docs/RESEARCH_PROTOCOL.md section 7).

Writes the pre-registration record BEFORE any condition runs. The
experiment harness refuses to drive runs unless a pre-registration
record precedes the first run record in the registry chain.

Records: task set hash + version; the exact three condition configs;
the metrics to be reported; runs per condition; the seed scheme; the
stopping rule; the held-constant pins. Deviations after this point
must be recorded with a reason before the affected runs are
analyzed (a ``prereg_amendment`` record type exists for that; none
is expected in the shakedown).

Usage:
    python -m air.experiments.prereg --exp-dir <dir> --exp-id <id> \\
        [--repetitions N]
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from air.experiments import evalkit, registry
from air.experiments.conditions import (
    AGENT_BUDGET,
    CONDITIONS,
    COST_BUDGET_USD,
    EXPERIMENT_GRANTS,
    TOKEN_BUDGET,
    TOOL_CALL_BUDGET,
    WALL_TIME_BUDGET_S,
)

TASKS_PATH = Path(__file__).parent / "tasks" / "v1" / "tasks.json"
SHA_PATH = Path(__file__).parent / "tasks" / "v1" / "SHA256SUM"

# The metrics the report must contain (protocol section 4 + the
# director's required list). Fixed here so the report cannot
# silently choose metrics after seeing results.
METRICS = [
    "verified_success_rate",
    "invalid_inconclusive_rate",
    "cost_per_verified_success",
    "latency_per_verified_success",
    "total_tokens",
    "mean_agents",
    "total_agents",
    "spawn_efficiency",
    "spawn_requested",
    "spawn_approved",
    "spawn_denied",
    "unnecessary_spawning",
    "verification_failures",
    "evaluator_failures",
    "unexpected_failures",
    "tool_denied",
    "policy_blocked",
    "capability_reuse",
    "policy_changes",
    "rollbacks",
    "verdict_buckets",
    "assurance_system_buckets",
]

RESEARCH_QUESTION = (
    "Under equal externally imposed resources and identical "
    "evaluation/assurance conditions, does dynamically allocating "
    "cognitive organization produce a statistically and practically "
    "meaningful improvement in independently verified task success "
    "over the predefined baselines?"
)


def task_set_hash() -> str:
    """Canonical sha256 of the frozen task set; cross-checked against
    the shipped SHA256SUM file."""
    data = json.loads(TASKS_PATH.read_text())
    canon = json.dumps(data, sort_keys=True,
                       separators=(",", ":")).encode()
    digest = hashlib.sha256(canon).hexdigest()
    shipped = SHA_PATH.read_text().split()[0]
    if digest != shipped:
        raise ValueError(
            f"task set hash {digest} != shipped {shipped}: the task set "
            f"was modified after freezing")
    return digest


def build_prereg(exp_id: str, repetitions: int) -> dict:
    tasks = json.loads(TASKS_PATH.read_text())
    return {
        "exp_id": exp_id,
        "research_question": RESEARCH_QUESTION,
        "task_set": {
            "version": tasks["version"],
            "n_tasks": len(tasks["tasks"]),
            "task_ids": [t["id"] for t in tasks["tasks"]],
            "sha256": task_set_hash(),
        },
        "conditions": {
            key: {
                "name": cfg.name,
                "force_strategy": (cfg.force_strategy.value
                                   if cfg.force_strategy else None),
                "description": cfg.description,
            }
            for key, cfg in CONDITIONS.items()
        },
        "held_constant": {
            "tool_call_budget_per_run": TOOL_CALL_BUDGET,
            "wall_time_budget_s_per_run": WALL_TIME_BUDGET_S,
            "agent_budget_per_run": AGENT_BUDGET,
            "token_budget_per_run": TOKEN_BUDGET,
            "cost_budget_usd_per_run": COST_BUDGET_USD,
            "capability_grants": EXPERIMENT_GRANTS,
            "tool_registry": "default builtin registry "
                             "(fs.read, fs.write, shell.exec, ...)",
            "evaluation_suite": {
                "id": evalkit.SUITE_ID,
                "version": evalkit.SUITE_VERSION,
            },
            "assurance": "AssuranceEngine probes (fixed set), post-hoc",
            "learning": "OFF (never invoked; per-condition ledgers)",
            "policy": "pinned; asserted per run",
            "task_order": "fixed task-file order, identical across "
                          "conditions and repetitions",
            "model_provider": "none (scripted behaviors; competence "
                              "identical across conditions by construction)",
        },
        "metrics": METRICS,
        "runs_per_condition": repetitions * len(tasks["tasks"]),
        "repetitions": repetitions,
        "seed_scheme": "derive_seed(exp_id, condition, task_id, repetition) "
                       "= int(sha256(...).hexdigest()[:15], 16) (60 bits, "
                       "fits SQLite INTEGER); recorded per run",
        "stopping_rule": (
            "Fixed N: all conditions x repetitions x tasks run to "
            "completion, timeout (harness wall-time cap), or terminal "
            "failure. No early stopping on results. Every run is "
            "recorded and reported, including timeouts and verification "
            "failures. A condition that is fast but wrong loses to one "
            "that is slow but verified."
        ),
        "known_limitations": [
            "No model provider: scripted behaviors; only organization varies.",
            "Mid-run spawning not exercised: C's dynamism is the "
            "allocator's per-task strategy choice.",
            "Wall-time cap enforced by the harness (asyncio.wait_for); "
            "the runtime stores but does not enforce time_limit_s.",
            "Token/cost budgets moot with scripted behaviors (recorded as 0).",
            "Structural grounding only: content-level truth of artifacts "
            "is beyond the evidence model by design.",
        ],
    }


def write_prereg(exp_dir: str, exp_id: str, repetitions: int) -> dict:
    """Append the pre-registration record. Refuses if runs exist."""
    if registry.first_seq_of(exp_dir, "run") is not None:
        raise RuntimeError("run records already exist: pre-registration "
                           "must precede all runs")
    if registry.first_seq_of(exp_dir, "pre_registration") is not None:
        raise RuntimeError("pre-registration already written")
    payload = build_prereg(exp_id, repetitions)
    return registry.append(exp_dir, "pre_registration", payload)


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Write pre-registration")
    ap.add_argument("--exp-dir", required=True)
    ap.add_argument("--exp-id", required=True)
    ap.add_argument("--repetitions", type=int, default=5)
    args = ap.parse_args()
    rec = write_prereg(args.exp_dir, args.exp_id, args.repetitions)
    p = rec["payload"]
    print(f"pre-registration written (seq {rec['seq']})")
    print(f"  task set: {p['task_set']['version']} "
          f"{p['task_set']['n_tasks']} tasks "
          f"sha256={p['task_set']['sha256'][:16]}...")
    print(f"  runs per condition: {p['runs_per_condition']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
