"""Python library API for AIR. Imported lazily via air.__getattr__."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_POLICY_PATH = Path(__file__).parent / "data" / "pv2_policy.json"


@lru_cache(maxsize=1)
def _load_policy() -> dict:
    """Load the frozen pv2 policy bundled with the package."""
    return json.loads(_POLICY_PATH.read_text())


@lru_cache(maxsize=1)
def _load_rules():
    """Load pv2 rules as AllocationRule objects."""
    from air.experiments.v2harness import _rebuild_rules
    policy = _load_policy()
    return _rebuild_rules(policy["rules"]), policy["version"]


# Maps user-facing capability names to task-dict structure
# that pv2's feature extraction reads.
_NEEDS_MAP = {
    "read": {"effects": [], "operations": [{"tool": "fs.read", "phase": "produce"}]},
    "write": {"effects": ["create"], "operations": []},
    "execute": {"effects": ["execute"], "operations": []},
}


def _build_task(goal: str, needs: list[str] | None = None) -> dict:
    """Build a task dict from a goal and explicit capability needs."""
    effects: list[str] = []
    operations: list[dict] = []
    for n in needs or []:
        key = n.strip().lower()
        if key not in _NEEDS_MAP:
            raise ValueError(
                f"unknown capability {n!r}; expected one of: "
                + ", ".join(sorted(_NEEDS_MAP))
            )
        mapping = _NEEDS_MAP[key]
        for e in mapping["effects"]:
            if e not in effects:
                effects.append(e)
        operations.extend(mapping["operations"])
    return {"id": "api", "goal": goal, "artifacts": [],
            "effects": effects, "operations": operations}


def allocate_strategy(goal: str, needs: list[str] | None = None) -> str:
    """Allocate using the frozen pv2 policy. Returns strategy name.

    pv2 is the 12-rule policy learned from verified experience
    (D-series) and validated through OOD-1, CTC-1, and PV-1.
    It responds to organizational capability requirements,
    not just lexical cues.

    needs: explicit capabilities the task requires, e.g.
    ["read"], ["write"], ["execute"], or ["read", "write"].
    This is how CTC-1's validated capability sensitivity
    is reached through the public API.
    """
    from air.experiments.v2harness import choose_strategy
    rules, _ = _load_rules()
    task = _build_task(goal, needs)
    return choose_strategy(task, rules)


def allocation_scores(goal: str, needs: list[str] | None = None) -> dict:
    """Return v1 baseline scores and pv2 adjustments for a goal."""
    from air.experiments.v2harness import decision_decomposition
    rules, _ = _load_rules()
    task = _build_task(goal, needs)
    decomp = decision_decomposition(task, rules)
    return {
        "v1_base_scores": decomp["v1_base_scores"],
        "pv2_adjustments": decomp["pv2_adjustments"],
        "pv2_final_scores": decomp["pv2_final_scores"],
        "v1_choice": decomp["v1_choice"],
        "pv2_choice": decomp["pv2_choice"],
        "fired_rules": decomp["fired_rules"],
    }


def allocation_explanation(goal: str, needs: list[str] | None = None) -> str:
    """Human-readable explanation of an allocation decision."""
    s = allocation_scores(goal, needs)
    lines = [f"Goal: {goal}"]
    if needs:
        lines.append(f"Needs: {', '.join(needs)}")
    lines.append("")
    lines.append("v1 baseline scores:")
    for k, v in sorted(s["v1_base_scores"].items(), key=lambda x: -x[1]):
        lines.append(f"  {k}: {v:.3f}")
    if s["pv2_adjustments"]:
        lines.append("")
        lines.append("pv2 adjustments (frozen policy):")
        for k, v in s["pv2_adjustments"].items():
            lines.append(f"  {k}: {v:+.3f}")
    lines.append("")
    lines.append(f"v1 choice:  {s['v1_choice']}")
    lines.append(f"pv2 choice: {s['pv2_choice']}")
    if s["fired_rules"]:
        lines.append(f"Rules fired: {', '.join(s['fired_rules'])}")
    return "\n".join(lines)


def frozen_policy() -> dict:
    """Return the frozen pv2 policy metadata."""
    rules, version = _load_rules()
    return {
        "version": version,
        "n_rules": len(rules),
        "rule_ids": [r.id for r in rules],
        "frozen": True,
        "note": "Learned from D-series verified experience; "
                "validated via OOD-1, CTC-1, PV-1. Not updated.",
    }
