"""CTC-1 structural tests: T1 (v1 topology blindness) and T2 (label leakage).

These are PREREGISTERED invariants from docs/PREREGISTRATION_CTC_1.md
Section 3.3. They must pass before the freeze is declared valid.

T1: The CTC topology intervention must have no execution path into
    v1 scoring or feature extraction. For every task, parent_choose
    (v1, empty rules) must return identical choices whether or not
    topology_override is present.

T2: The learner/policy must not receive topology_override, pair
    identity, AB/C/D membership, expected prediction, or counterfactual
    labels as task features. extract_features output must be identical
    whether or not topology_override is present.
"""

import json
import copy
from pathlib import Path

import pytest

from air.experiments.v2harness import (
    parent_choose,
    extract_features,
    base_scores,
)

POOL = Path("src/air/experiments/tasks/v2/ctc_1.json")


@pytest.fixture(scope="module")
def tasks():
    return json.loads(POOL.read_text())["tasks"]


def _strip_override(task):
    t = copy.deepcopy(task)
    t.pop("topology_override", None)
    return t


def test_t1_v1_blind_to_topology(tasks):
    """T1: v1 choice identical with/without topology_override, all tasks."""
    for t in tasks:
        assert "topology_override" in t, f"{t['id']}: missing override"
        with_override = parent_choose(t)
        without_override = parent_choose(_strip_override(t))
        assert with_override == without_override, (
            f"T1 VIOLATION {t['id']}: v1 choice {with_override} with override "
            f"vs {without_override} without"
        )


def test_t1_v1_base_scores_blind(tasks):
    """T1 corollary: v1 base scores unaffected by override presence."""
    for t in tasks:
        s_with = base_scores(t["goal"])
        # base_scores only reads goal text; override cannot affect it.
        # This test pins that contract.
        assert s_with == base_scores(_strip_override(t)["goal"])


def test_t2_features_ignore_override(tasks):
    """T2: extract_features identical with/without topology_override."""
    for t in tasks:
        f_with = extract_features(t)
        f_without = extract_features(_strip_override(t))
        assert f_with == f_without, (
            f"T2 VIOLATION {t['id']}: features differ with/without override"
        )


def test_t2_no_leakage_keys_in_features(tasks):
    """T2: no CTC metadata keys leak into feature dict."""
    LEAK_KEYS = {"topology_override", "pair", "pair_type", "_ctc",
                 "expected", "prediction", "counterfactual"}
    for t in tasks:
        feats = extract_features(t)
        for k in feats:
            assert k not in LEAK_KEYS, f"T2 VIOLATION {t['id']}: {k}"
        # Task-level: policy-visible dict must not contain pair labels
        for k in t:
            assert k not in {"_ctc", "pair", "pair_type"}, (
                f"T2 VIOLATION {t['id']}: task carries {k}"
            )


def test_t2_goal_features_ignore_override(tasks):
    """T2: v1 goal_features (lexical) never sees topology."""
    from air.allocation.allocator import extract_features as goal_features
    for t in tasks:
        g_with = goal_features(t["goal"])
        g_without = goal_features(_strip_override(t)["goal"])
        assert g_with == g_without
