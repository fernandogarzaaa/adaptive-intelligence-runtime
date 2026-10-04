"""Tests for the D-series learning experiment plumbing.

These test the harness mechanics, not the learning outcome: task-set
integrity, pre-registration contents, the attribution schema, and the
counterfactual analysis on synthetic data.
"""

from __future__ import annotations

import json
from pathlib import Path

from air.experiments import dseries_prereg, registry
from air.experiments.dseries_report import counterfactual_analysis


def test_d3_taskset_frozen_and_valid():
    tasks_path = Path(__file__).parent.parent / "src" / "air" / "experiments" \
        / "tasks" / "d3" / "tasks.json"
    # Path when running from repo root via pytest:
    if not tasks_path.exists():
        tasks_path = Path("src/air/experiments/tasks/d3/tasks.json")
    data = json.loads(tasks_path.read_text())
    assert data["version"] == "d3-v1"
    tasks = data["tasks"]
    assert len(tasks) == 9
    kinds = {t["kind"] for t in tasks}
    prod = {k for k in kinds if not k.startswith("research")}
    research = {k for k in kinds if k.startswith("research")}
    assert len(prod) == 5 and len(research) == 4
    for t in tasks:
        assert t["id"] and t["goal"] and t["claims"] and t["operations"]
        for c in t["claims"]:
            assert c["id"] and c["phase"] in ("produce", "verify")
    # Hash matches the shipped SHA256SUM (frozen before D1).
    sha_path = tasks_path.parent / "SHA256SUM"
    shipped = sha_path.read_text().split()[0]
    canon = json.dumps(data, sort_keys=True,
                       separators=(",", ":")).encode()
    import hashlib
    assert hashlib.sha256(canon).hexdigest() == shipped


def test_dseries_prereg_seals_d3_before_d1():
    prereg = dseries_prereg.build_prereg()
    assert prereg["exp_id"] == "d-series-v1"
    d3 = prereg["d3"]
    assert d3["task_set"]["frozen_before_d1"] is True
    assert len(d3["task_set"]["task_ids"]) == 9
    assert len(d3["task_set"]["hash"]) == 64
    assert prereg["d1"]["policy"].startswith("cognitive-allocation@v1")
    assert prereg["learning_boundary"]["no_privileged_knowledge"] is True
    assert len(prereg["five_questions"]) == 5
    assert len(dseries_prereg.ATTRIBUTION_FIELDS) == 9


def test_counterfactual_verdict_structural(tmp_path):
    exp_dir = str(tmp_path)
    # Synthetic D1: one failure on write_multi via parallel_agents,
    # one success on write_multi via single_agent.
    for run_id, kind, strat, verdict in [
        ("r1", "write_multi", "parallel_agents", "INCONCLUSIVE"),
        ("r2", "write_multi", "single_agent", "SUPPORTED"),
    ]:
        registry.append(exp_dir, "run", {
            "run_id": run_id, "phase": "D1", "task_id": "t",
            "task_kind": kind, "strategy": strat,
            "metrics": {"verdict": verdict}})
    # Synthetic D3: production -> adaptive_spawn, research -> parallel_agents.
    for run_id, kind, strat in [
        ("r3", "exec_write", "adaptive_spawn"),
        ("r4", "research_synth", "parallel_agents"),
    ]:
        registry.append(exp_dir, "run", {
            "run_id": run_id, "phase": "D3", "task_id": "t",
            "task_kind": kind, "strategy": strat,
            "metrics": {"verdict": "SUPPORTED"}})
    cf = counterfactual_analysis(exp_dir)
    assert cf["d1_failures"][0]["chosen_strategy"] == "parallel_agents"
    assert cf["d1_failures"][0]["successful_alternatives"] == ["single_agent"]
    assert cf["verdict"].startswith("STRUCTURAL")


def test_counterfactual_verdict_overfitting(tmp_path):
    exp_dir = str(tmp_path)
    registry.append(exp_dir, "run", {
        "run_id": "r1", "phase": "D1", "task_id": "t",
        "task_kind": "write_multi", "strategy": "parallel_agents",
        "metrics": {"verdict": "INCONCLUSIVE"}})
    # D3: policy avoids parallel_agents even on research tasks.
    registry.append(exp_dir, "run", {
        "run_id": "r2", "phase": "D3", "task_id": "t",
        "task_kind": "research_synth", "strategy": "adaptive_spawn",
        "metrics": {"verdict": "SUPPORTED"}})
    cf = counterfactual_analysis(exp_dir)
    assert cf["verdict"].startswith("OVERFITTING")
