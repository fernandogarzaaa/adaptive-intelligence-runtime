"""Condition definitions and the per-run driver.

Three conditions; organization is the independent variable:

- A (single agent): allocator pinned to SINGLE_AGENT. One agent, the
  full run budget, no spawning, no delegation.
- B (static multi-agent): allocator pinned to HIERARCHICAL_AGENTS,
  the product's own static hierarchical workflow
  (planner -> researcher -> coder -> synthesizer). The topology is
  fixed by the forced strategy: no re-decision, no mid-run spawns.
- C (AIR dynamic): no pinned strategy. The real allocator scores
  strategies per goal and builds the plan; the real spawn policy
  governs any spawn attempt.

Held constant across all three (pinned in pre-registration):
- identical frozen task set (same goal strings, same operations,
  same seed files)
- identical hard budgets per run: tool calls, wall time, agents,
  tokens, cost
- identical tool registry and identical capability grants
  (READ+WRITE+EXECUTE for every task agent)
- identical evaluation suite and assurance probes
- learning engine never invoked; policy pinned (asserted per run)
- same machine

Capability grants note: the product's allocator practices least
privilege (most plan specs grant nothing), which would deny task
agents the tools the tasks require. The experiment's grants policy
— READ+WRITE+EXECUTE for every task agent in every condition — is
set on the plan specs between create_run and start_run. This is
experiment configuration (disclosed here and in the verification
report), not a product change: no product file is modified, and
every tool call still passes through the real gateway with real
authorization, budgets, and redaction. The alternative — a product
grant knob for experiment/operator use — is recorded as a
recommended product change, not implemented here (frozen).

Mid-run spawns: scripted behaviors never call spawn_agent in this
shakedown, in any condition. C's dynamism is therefore the
allocator's per-task strategy choice, not mid-run reorganization.
This is an explicit, documented limitation; the D1->D2->D3
adaptation sequence is the follow-up that exercises spawning.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

from air.allocation.allocator import Strategy
from air.experiments.behaviors import TASK_BY_RUN

# Held-constant budgets (per run).
TOOL_CALL_BUDGET = 24
WALL_TIME_BUDGET_S = 120
AGENT_BUDGET = 8
TOKEN_BUDGET = 64_000
COST_BUDGET_USD = 5.0

# Held-constant capability grants for every task agent.
EXPERIMENT_GRANTS = ["READ", "WRITE", "EXECUTE"]


@dataclass(frozen=True)
class ConditionConfig:
    """Identifies a condition for harness bookkeeping and reports.

    ``key`` ("A"/"B"/"C") is used ONLY by the harness and the report.
    It is never passed to the runtime, the allocator, or any agent:
    condition blindness is asserted structurally by
    test_experiments.py.
    """
    key: str
    name: str
    force_strategy: Strategy | None
    description: str
    notes: str = ""


CONDITIONS: dict[str, ConditionConfig] = {
    "A": ConditionConfig(
        key="A",
        name="single_agent",
        force_strategy=Strategy.SINGLE_AGENT,
        description="One agent, the full run budget, no spawning or delegation.",
    ),
    "B": ConditionConfig(
        key="B",
        name="static_hierarchical",
        force_strategy=Strategy.HIERARCHICAL_AGENTS,
        description="Fixed planner -> researcher -> coder -> synthesizer "
                    "topology from the product's own plan. No re-decision, "
                    "no mid-run spawns.",
    ),
    "C": ConditionConfig(
        key="C",
        name="dynamic_allocation",
        force_strategy=None,
        description="The real allocator scores strategies per goal and "
                    "builds the plan; the real spawn policy governs any "
                    "spawn attempt. No pinned strategy.",
    ),
}


def canonical_task_input(task: dict) -> bytes:
    """The exact task information the runtime receives, canonicalized.

    Covers the goal string, the operations (tool + args templates),
    and the seed files (names + contents). The run namespace is NOT
    included: namespacing is a harness isolation detail, identical in
    form across conditions, not task information.
    """
    payload = {
        "goal": task["goal"],
        "operations": task.get("operations", []),
        "seed_files": task.get("seed_files", {}),
    }
    return json.dumps(payload, sort_keys=True,
                      separators=(",", ":")).encode()


def task_input_hash(task: dict) -> str:
    return hashlib.sha256(canonical_task_input(task)).hexdigest()


def apply_experiment_grants(rt, run_id: str) -> None:
    """Set the held-constant grants policy on the run's plan specs.

    Called between create_run and start_run. See module docstring for
    why this configuration step exists and why it is not a product
    change.
    """
    cfg = rt._run_configs.get(run_id)
    if cfg is None:
        raise ValueError(f"unknown run: {run_id}")
    for spec in cfg["plan"]["agent_specs"]:
        spec["grants_applied_by_experiment"] = True  # audit marker
        spec["granted"] = list(EXPERIMENT_GRANTS)


def seed_workspace(data_dir, run_id: str, task: dict) -> None:
    """Write the task's seed files into the run's namespace."""
    import os
    ns = os.path.join(str(data_dir), run_id)
    os.makedirs(ns, exist_ok=True)
    for name, content in (task.get("seed_files") or {}).items():
        target = os.path.join(ns, name)
        assert ".." not in name and not name.startswith("/"), \
            f"unsafe seed file name: {name!r}"
        with open(target, "w", encoding="utf-8") as f:
            f.write(content)


def register_task_for_run(run_id: str, task: dict) -> None:
    """Make the task visible to behaviors via run_id lookup.

    The registered payload is the task dict only: no condition key,
    no condition label, no harness metadata.
    """
    TASK_BY_RUN[run_id] = {"task": task}
