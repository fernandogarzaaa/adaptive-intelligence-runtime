"""Pre-registration for the D-series learning experiment (d-series-v1).

A SEPARATE preregistered experiment from baseline-v1, written BEFORE D1
runs. The D-series tests whether AIR learns an allocation distinction
from experience:

    D1 (reproduce the baseline failure)
      -> Learning 1 (experience -> candidate -> validation ->
                     evaluation -> assurance -> promotion decision)
      -> D2 (same tasks, learned policy)
      -> Learning 2 (refine or reinforce?)
      -> D3 (unseen tasks: generalization vs overfitting)

Constraints sealed here (from the research director):
1. D1 starts from the same initial policy state as baseline C:
   cognitive-allocation@v1, no hand-editing, no encoded answers.
2. D1 failures become experience through the normal pipeline only.
3. Every policy change carries the 9-field attribution record.
4. The learning boundary is explicit; D2 starts only after it.
5. D2 receives no privileged knowledge of the failure cause.
6. D3 tasks are frozen NOW (hash below), before D1 runs, and are
   structurally different from t05/t07.

Usage:
    python -m air.experiments.dseries_prereg --exp-dir <dir>
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from air.experiments import registry
from air.experiments.conditions import (
    AGENT_BUDGET,
    COST_BUDGET_USD,
    EXPERIMENT_GRANTS,
    TOKEN_BUDGET,
    TOOL_CALL_BUDGET,
    WALL_TIME_BUDGET_S,
)

V1_TASKS_PATH = Path(__file__).parent / "tasks" / "v1" / "tasks.json"
V1_SHA_PATH = Path(__file__).parent / "tasks" / "v1" / "SHA256SUM"
D3_TASKS_PATH = Path(__file__).parent / "tasks" / "d3" / "tasks.json"
D3_SHA_PATH = Path(__file__).parent / "tasks" / "d3" / "SHA256SUM"

EXP_ID = "d-series-v1"

# D-series research question (distinct from the baseline question).
RESEARCH_QUESTION = (
    "After a repeatable allocation failure (parallel_agents chosen for "
    "production tasks, yielding no producer), can AIR's "
    "experience -> evaluation -> assurance -> policy pipeline generate, "
    "validate, and promote a policy change that (a) reduces the known "
    "failure and (b) generalizes the structural distinction to unseen "
    "task shapes, without merely learning 'parallel_agents is bad'?"
)

# The five questions the report must answer.
FIVE_QUESTIONS = [
    "D1: does AIR reproduce the baseline failure?",
    "Learning 1: can AIR generate and validate a useful allocation hypothesis?",
    "D2: does the promoted policy eliminate/reduce the known failure?",
    "Learning 2: does AIR refine rather than blindly reinforce the first lesson?",
    "D3: does the learned allocation principle generalize to unseen tasks?",
]

# The 9-field attribution schema. Every policy change (or explicit
# non-change) records all nine; nothing is optional.
ATTRIBUTION_FIELDS = [
    "policy_version",
    "parent_policy_version",
    "source_experiences",
    "hypothesis",
    "proposed_change",
    "expected_effect",
    "evaluation_id",
    "assurance_id",
    "promotion_decision",
]


def _task_set_hash(tasks_path: Path, sha_path: Path) -> str:
    data = json.loads(tasks_path.read_text())
    canon = json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    digest = hashlib.sha256(canon).hexdigest()
    shipped = sha_path.read_text().split()[0]
    if digest != shipped:
        raise ValueError(
            f"task set hash {digest} != shipped {shipped}: modified after freezing")
    return digest


def build_prereg() -> dict:
    v1_tasks = json.loads(V1_TASKS_PATH.read_text())
    d3_tasks = json.loads(D3_TASKS_PATH.read_text())
    return {
        "exp_id": EXP_ID,
        "kind": "learning_series",
        "research_question": RESEARCH_QUESTION,
        "five_questions": FIVE_QUESTIONS,
        "d1": {
            "description": "Reproduce the baseline-C failure under v1.",
            "task_set": {"version": v1_tasks["version"],
                         "hash": _task_set_hash(V1_TASKS_PATH, V1_SHA_PATH),
                         "n_tasks": len(v1_tasks["tasks"])},
            "policy": "cognitive-allocation@v1 (initial state, unmodified)",
            "strategy": "dynamic (no pin; identical to baseline C)",
            "repetitions": 5,
            "expected": "reproduces ~65/80 with t05/t07 failing via parallel_agents",
        },
        "learning_boundary": {
            "description": "D1 experiences -> LearningEngine.propose_policy_update "
                           "-> trial validation under candidate -> "
                           "evaluate_policy_candidate -> assurance -> "
                           "promotion gate. Uses ONLY the product machinery "
                           "plus task-agnostic plumbing.",
            "validation_tasks": ["t05", "t07", "t01", "t16"],
            "validation_reps": 2,
            "attribution_fields": ATTRIBUTION_FIELDS,
            "no_privileged_knowledge": True,
        },
        "d2": {
            "description": "Same 16 v1 tasks, 5 reps, under the promoted policy "
                           "(or v1 if no promotion).",
            "task_set": {"version": v1_tasks["version"],
                         "hash": _task_set_hash(V1_TASKS_PATH, V1_SHA_PATH)},
            "repetitions": 5,
        },
        "d3": {
            "description": "Unseen tasks under the latest policy. Tests "
                           "generalization vs overfitting.",
            "task_set": {"version": d3_tasks["version"],
                         "hash": _task_set_hash(D3_TASKS_PATH, D3_SHA_PATH),
                         "n_tasks": len(d3_tasks["tasks"]),
                         "task_ids": [t["id"] for t in d3_tasks["tasks"]],
                         "frozen_before_d1": True},
            "repetitions": 5,
        },
        "held_constant": {
            "budgets": {"tool_call": TOOL_CALL_BUDGET,
                        "wall_time_s": WALL_TIME_BUDGET_S,
                        "agents": AGENT_BUDGET, "tokens": TOKEN_BUDGET,
                        "cost_usd": COST_BUDGET_USD},
            "grants": list(EXPERIMENT_GRANTS),
            "behaviors": "identical scripted behavior, all phases",
            "evaluation_suite": "suite_exp_v1@1.0.0 (with outcome_grounding)",
            "learning_engine_during_phases": "never invoked inside D1/D2/D3",
        },
        "metrics": [
            "verified_success_rate", "invalid_inconclusive_rate",
            "per_task_kind_success", "strategy_selection_counts",
            "policy_changes", "attribution_records",
            "counterfactual_overfitting_vs_structural",
        ],
        "stopping_rule": ("Fixed repetitions (5 per task per phase). No early "
                          "stopping, no interim analysis influencing later phases."),
        "known_limitations": [
            "scripted behaviors of identical competence; no model provider",
            "the product learning engine learns from run completion, not "
            "verification verdicts (audited; see report)",
            "policy format supports only global strategy boosts + spawn threshold",
        ],
    }


def main() -> None:
    exp_dir = None
    args = sys.argv[1:]
    for i, a in enumerate(args):
        if a == "--exp-dir" and i + 1 < len(args):
            exp_dir = args[i + 1]
    if not exp_dir:
        print("usage: python -m air.experiments.dseries_prereg --exp-dir <dir>",
              file=sys.stderr)
        sys.exit(2)
    Path(exp_dir).mkdir(parents=True, exist_ok=True)
    prereg = build_prereg()
    rec = registry.append(exp_dir, "pre_registration", prereg)
    print(f"pre-registration written: seq={rec['seq']} hash={rec['hash'][:12]}")
    print(f"d3 task hash: {prereg['d3']['task_set']['hash'][:16]}")


if __name__ == "__main__":
    main()
