"""Tests for the experiment harness itself (not the experiment).

Fast unit tests: task-set hash stability, registry chain semantics,
seed determinism, aggregator bucketing, Fisher's exact sanity. The
12 contamination checks are exercised by
``python -m air.experiments.verify_protocol`` (pre/post phases),
which the run and report gates require.
"""

import hashlib
import json
from pathlib import Path

from air.experiments import metrics, registry
from air.experiments.conditions import task_input_hash
from air.experiments.harness import derive_seed

TASKS = Path(__file__).parent.parent / "src" / "air" / "experiments" \
    / "tasks" / "v1" / "tasks.json"
SHA = TASKS.parent / "SHA256SUM"


def test_task_set_hash_stable():
    data = json.loads(TASKS.read_text())
    canon = json.dumps(data, sort_keys=True,
                       separators=(",", ":")).encode()
    digest = hashlib.sha256(canon).hexdigest()
    shipped = SHA.read_text().split()[0]
    assert digest == shipped, "task set changed after freezing"
    assert len(data["tasks"]) == 16


def test_task_input_hash_deterministic():
    data = json.loads(TASKS.read_text())
    t = data["tasks"][0]
    assert task_input_hash(t) == task_input_hash(t)
    assert task_input_hash(t) != task_input_hash(data["tasks"][1])


def test_derive_seed_deterministic_and_unique():
    assert derive_seed("e", "A", "t01", 0) == derive_seed("e", "A", "t01", 0)
    assert derive_seed("e", "A", "t01", 0) != derive_seed("e", "B", "t01", 0)
    assert derive_seed("e", "A", "t01", 0) != derive_seed("e", "A", "t01", 1)


def test_registry_chain_and_tamper(tmp_path):
    d = str(tmp_path)
    registry.append(d, "a", {"x": 1})
    registry.append(d, "b", {"x": 2})
    ok, detail = registry.verify_chain(d)
    assert ok, detail
    # Tamper with the first record.
    p = tmp_path / "registry.jsonl"
    lines = p.read_text().splitlines()
    rec = json.loads(lines[0])
    rec["payload"]["x"] = 999
    lines[0] = json.dumps(rec, sort_keys=True)
    p.write_text("\n".join(lines) + "\n")
    ok, detail = registry.verify_chain(d)
    assert not ok and "tampered" in detail


def test_registry_ordering_helpers(tmp_path):
    d = str(tmp_path)
    assert registry.first_seq_of(d, "run") is None
    registry.append(d, "pre_registration", {})
    registry.append(d, "run", {})
    assert registry.first_seq_of(d, "pre_registration") == 0
    assert registry.first_seq_of(d, "run") == 1


def _synthetic(v):
    return {
        "verdict": v, "cost_usd": 0.0, "tokens": 0, "latency_s": 1.0,
        "n_agents": 1, "n_spawn_requested": 0, "n_spawn_approved": 0,
        "n_spawn_denied": 0, "n_unexpected_failures": 0,
        "n_tool_denied": 0, "n_policy_blocked": 0, "n_rollbacks": 0,
        "verified_success": v == "SUPPORTED",
        "assurance_system_verdict": None,
    }


def test_aggregate_buckets_every_verdict():
    per = [_synthetic(v) for v in
           ["SUPPORTED", "FALSIFIED", "INCONCLUSIVE", "INVALID",
            "UNTESTED", "WEIRD"]]
    agg = metrics.aggregate(per)
    assert agg["verdict_buckets"] == {
        "SUPPORTED": 1, "FALSIFIED": 1, "INCONCLUSIVE": 1, "INVALID": 1,
        "UNTESTED": 1, "UNKNOWN": 1}
    assert agg["verified_success_rate"] == 1 / 6
    assert agg["verification_failures"] == 2  # FALSIFIED + INVALID
    assert agg["evaluator_failures"] == 1  # UNTESTED


def test_fisher_exact_sanity():
    # Identical tables -> p = 1.
    assert metrics.fisher_exact(5, 5, 5, 5) == 1.0
    # Extreme separation -> small p.
    p = metrics.fisher_exact(10, 0, 0, 10)
    assert p < 0.001, p
    # Degenerate tables -> 1.0, never crash.
    assert metrics.fisher_exact(0, 0, 0, 0) == 1.0
