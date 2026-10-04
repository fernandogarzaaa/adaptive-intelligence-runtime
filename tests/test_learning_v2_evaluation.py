"""Phase 5a: discriminating evaluation. Intervention, not retrospection."""

import pytest

from air.learning_v2 import evaluation
from air.learning_v2.contracts import OrganizationProperties
from air.learning_v2.evaluation import (
    EvalTask,
    decision_delta,
    evaluate_paired,
    mcnemar_exact_onesided,
    wilson_lower,
)

PAR = OrganizationProperties(strategy="parallel_agents",
                             roles=frozenset({"researcher"}),
                             capabilities=frozenset({"READ"}),
                             topology="flat")
DIR = OrganizationProperties(strategy="direct",
                             roles=frozenset({"generalist"}),
                             capabilities=frozenset({"READ", "WRITE"}),
                             topology="single")


def _task(tid, kind):
    feats = {"requested_write_effect": kind == "production",
             "output_artifact_count": 3 if kind == "production" else 0}
    return EvalTask(id=tid, features=feats, eval_class=kind)


def _oracle(task, org):
    prod = task.features["output_artifact_count"] > 1
    ok = (not prod) or ("WRITE" in org.capabilities)
    return {"evaluation_verdict": "SUPPORTED" if ok else "FALSIFIED",
            "assurance_verdict": "SOUND",
            "resources": {"cost": 1.0, "latency_ms": 100},
            "safety_violations": []}


def _parent(task):
    return PAR


def _candidate(task):
    return DIR if task.features["output_artifact_count"] > 1 else PAR


def _tasks():
    return ([_task(f"prod_{i}", "production") for i in range(8)]
            + [_task(f"res_{i}", "research") for i in range(8)])


def test_decision_delta():
    tasks = _tasks()
    delta = decision_delta(_parent, _candidate, tasks)
    assert sorted(delta) == sorted(f"prod_{i}" for i in range(8))


def test_vacuous_candidate_refused():
    tasks = _tasks()
    ev = evaluate_paired(id="e1", candidate_id="c1", parent_version="v1",
                         sealed_pool_id="pool1", tasks=tasks,
                         parent_choose=_parent, candidate_choose=_parent,
                         oracle=_oracle)
    assert ev.verdict == "VACUOUS"
    assert ev.decision_delta_task_ids == ()


def test_pass_on_improvement():
    # 40/class: the Wilson lower bound needs enough samples to clear a
    # preregistered non-inferiority margin even at identical rates.
    tasks = ([_task(f"prod_{i}", "production") for i in range(40)]
             + [_task(f"res_{i}", "research") for i in range(40)])
    ev = evaluate_paired(id="e1", candidate_id="c1", parent_version="v1",
                         sealed_pool_id="pool1", tasks=tasks,
                         parent_choose=_parent, candidate_choose=_candidate,
                         oracle=_oracle, non_inferiority_eps=0.15)
    assert ev.verdict == "PASS", ev.statistics
    assert "production" in ev.statistics["improved_classes"]
    assert len(ev.decision_delta_task_ids) == 40


def test_fail_on_regression():
    # Candidate breaks research: non-inferiority violated there.
    def bad_candidate(task):
        return PAR if task.features["output_artifact_count"] > 1 else DIR

    def bad_oracle(task, org):
        # direct is bad at research in this world
        if task.features["output_artifact_count"] <= 1 \
                and org.strategy == "direct":
            return {"evaluation_verdict": "FALSIFIED",
                    "assurance_verdict": "SOUND",
                    "resources": {"cost": 1.0, "latency_ms": 100},
                    "safety_violations": []}
        return _oracle(task, org)

    tasks = _tasks()
    ev = evaluate_paired(id="e1", candidate_id="c1", parent_version="v1",
                         sealed_pool_id="pool1", tasks=tasks,
                         parent_choose=_parent, candidate_choose=bad_candidate,
                         oracle=bad_oracle)
    assert ev.verdict == "FAIL"
    assert any("research" in n for n in
               ev.statistics["verdict_reason"].split(";")) or \
        "non-inferior" in ev.statistics["verdict_reason"]


def test_fail_on_safety_violation():
    def unsafe_oracle(task, org):
        res = _oracle(task, org)
        if org.strategy == "direct":
            res = dict(res, safety_violations=["writes outside sandbox"])
        return res

    tasks = [_task("s1", "production")]
    t = EvalTask(id="s1", features=tasks[0].features, eval_class="prod",
                 safety=True)
    ev = evaluate_paired(id="e1", candidate_id="c1", parent_version="v1",
                         sealed_pool_id="pool1", tasks=[t],
                         parent_choose=_parent, candidate_choose=_candidate,
                         oracle=unsafe_oracle)
    assert ev.verdict == "FAIL"
    assert ev.statistics["safety_violations"] == ["s1"]


def test_fail_on_cost_blowout():
    def pricey_oracle(task, org):
        res = _oracle(task, org)
        cost = 10.0 if org.strategy == "direct" else 1.0
        return dict(res, resources={"cost": cost, "latency_ms": 100})

    tasks = _tasks()
    ev = evaluate_paired(id="e1", candidate_id="c1", parent_version="v1",
                         sealed_pool_id="pool1", tasks=tasks,
                         parent_choose=_parent, candidate_choose=_candidate,
                         oracle=pricey_oracle, max_cost_increase=0.25)
    assert ev.verdict == "FAIL"
    assert "cost" in ev.statistics["verdict_reason"]


def test_mcnemar_exact_known_values():
    # b=0, c=5: p = (1/2)^5
    assert mcnemar_exact_onesided(0, 5) == pytest.approx(0.03125)
    assert mcnemar_exact_onesided(5, 0) == pytest.approx(1.0)
    assert mcnemar_exact_onesided(0, 0) == 1.0


def test_wilson_lower_sanity():
    assert wilson_lower(10, 10) > 0.7
    assert wilson_lower(0, 10) == 0.0
    assert wilson_lower(5, 10) < 0.5
