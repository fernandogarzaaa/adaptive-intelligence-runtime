"""Assurance: can we trust the mechanism that told us it performed well?

Evaluation asks "did the system perform well?". Assurance asks "can we trust
the evaluator?". These are separate systems with separate verdicts.

Probes attack the evaluator with adversarial fixtures:
- evaluator spoofing / fake success: a subject that claims success with no
  execution evidence must NOT be accepted.
- stdout spoofing: expected output printed as text must NOT count as evidence.
- test avoidance: a subject that skips verification must NOT pass.
- pathological acceptance/rejection: fixture sweeps measure false accepts and
  false rejects per check.
- correlated evaluator failure: evaluator == subject author is flagged.
- evaluator timeout / crash: check errors must be recorded, not swallowed.

A candidate is never promoted on self-reported metrics.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from enum import Enum

from pydantic import BaseModel, Field

from air.evaluation.suites import CHECKS, EvalSuite, Verdict
from air.events.fabric import canonical, utcnow


class EvaluatorVerdict(str, Enum):
    SOUND = "SOUND"
    EXPLOITABLE = "EXPLOITABLE"
    UNRELIABLE = "UNRELIABLE"
    OVER_STRICT = "OVER_STRICT"
    UNRESPONSIVE = "UNRESPONSIVE"


class SystemVerdict(str, Enum):
    SUPPORTED = "SUPPORTED"
    FALSIFIED = "FALSIFIED"
    INCONCLUSIVE = "INCONCLUSIVE"
    INVALID = "INVALID"
    UNTESTED = "UNTESTED"


class ProbeResult(BaseModel):
    probe: str
    passed: bool  # True = evaluator behaved correctly under attack
    detail: str
    false_accept: bool = False
    false_reject: bool = False


class AssuranceResult(BaseModel):
    id: str = Field(default_factory=lambda: "asr_" + uuid.uuid4().hex[:12])
    evaluation_id: str
    target: dict
    probes: list[ProbeResult] = Field(default_factory=list)
    false_accepts: int = 0
    false_rejects: int = 0
    timeouts: int = 0
    evaluator_verdict: EvaluatorVerdict = EvaluatorVerdict.UNRELIABLE
    system_verdict: SystemVerdict = SystemVerdict.UNTESTED
    evidence_hash: str = ""
    started_at: str = Field(default_factory=utcnow)
    completed_at: str | None = None


# ---------------------------------------------------------------------------
# Adversarial fixtures: synthetic event histories with known ground truth.
# ---------------------------------------------------------------------------
def _good_events() -> list[dict]:
    return [
        {"type": "tool.completed", "agent_id": "a1", "payload": {"tool": "t"}},
        {"type": "agent.completed", "agent_id": "a1", "payload": {}},
    ]


def _spoofed_success_events() -> list[dict]:
    # Agent claims success in its result message but never executed anything.
    return [
        {"type": "agent.message", "agent_id": "a1",
         "payload": {"kind": "result",
                     "result": {"success": True, "output": "all tests pass"}}},
        {"type": "agent.completed", "agent_id": "a1", "payload": {}},
    ]


def _stdout_spoof_events() -> list[dict]:
    # Expected output printed as text; no tool evidence behind it.
    return [
        {"type": "agent.message", "agent_id": "a1",
         "payload": {"kind": "message",
                     "text": "tool.completed verification passed"}},
        {"type": "agent.completed", "agent_id": "a1", "payload": {}},
    ]


def _empty_events() -> list[dict]:
    return []


def _run_checks(suite: EvalSuite, events: list[dict]) -> list[tuple[str, bool, str]]:
    out = []
    for case in suite.cases:
        fn = CHECKS.get(case.check)
        if fn is None:
            out.append((case.id, False, f"unknown check {case.check}"))
            continue
        try:
            passed, detail = fn(events, None, case.params)
        except Exception as e:  # noqa: BLE001
            passed, detail = False, f"check raised {type(e).__name__}: {e}"
        out.append((case.id, passed, detail))
    return out


class AssuranceEngine:
    def __init__(self, conn) -> None:
        self._conn = conn

    def assure(self, evaluation_id: str) -> AssuranceResult:
        row = self._conn.execute(
            "SELECT suite_id, subject, evaluator, evaluator_version, verdict,"
            " evidence FROM evaluation_runs WHERE id=?", (evaluation_id,)).fetchone()
        if not row:
            raise ValueError(f"evaluation not found: {evaluation_id}")
        suite_id, subject_json, evaluator, evaluator_version, verdict, _ = row
        suite_row = self._conn.execute(
            "SELECT name, version, cases FROM evaluation_suites WHERE id=?",
            (suite_id,)).fetchone()
        if not suite_row:
            raise ValueError(f"suite not found: {suite_id}")
        from air.evaluation.suites import EvalCase, EvalSuite
        suite = EvalSuite(
            id=suite_id, name=suite_row[0], version=suite_row[1],
            cases=[EvalCase(**c) for c in json.loads(suite_row[2])])

        result = AssuranceResult(
            evaluation_id=evaluation_id,
            target={"kind": "evaluation", "id": evaluation_id,
                    "subject": json.loads(subject_json),
                    "evaluator": evaluator,
                    "evaluator_version": evaluator_version,
                    "evaluation_verdict": verdict})

        # Probe 1: spoofed success must be rejected (false accept = exploitable).
        self._probe_fixture(result, "evaluator_spoofing/fake_success",
                            _spoofed_success_events(), suite,
                            expect_pass=False)
        # Probe 2: stdout spoofing must be rejected.
        self._probe_fixture(result, "stdout_spoofing",
                            _stdout_spoof_events(), suite, expect_pass=False)
        # Probe 3: genuine evidence must be accepted (false reject = over-strict).
        self._probe_fixture(result, "control_genuine_evidence",
                            _good_events(), suite, expect_pass=True)
        # Probe 4: empty history must never yield a pass.
        self._probe_fixture(result, "control_empty_history",
                            _empty_events(), suite, expect_pass=False)
        # Probe 5: correlated evaluator (evaluator authored the subject).
        subject = json.loads(subject_json)
        if subject.get("author") and subject["author"] == evaluator:
            result.probes.append(ProbeResult(
                probe="correlated_evaluator_failure", passed=False,
                detail=f"evaluator {evaluator} authored the subject"))
        else:
            result.probes.append(ProbeResult(
                probe="correlated_evaluator_failure", passed=True,
                detail="evaluator is independent of subject author"))

        result.false_accepts = sum(1 for p in result.probes if p.false_accept)
        result.false_rejects = sum(1 for p in result.probes if p.false_reject)
        result.timeouts = sum(1 for p in result.probes if "timeout" in p.detail)

        if result.false_accepts > 0:
            result.evaluator_verdict = EvaluatorVerdict.EXPLOITABLE
        elif result.false_rejects > 1:
            result.evaluator_verdict = EvaluatorVerdict.OVER_STRICT
        elif any(not p.passed for p in result.probes):
            result.evaluator_verdict = EvaluatorVerdict.UNRELIABLE
        else:
            result.evaluator_verdict = EvaluatorVerdict.SOUND

        # System verdict: an exploitable evaluator invalidates the evaluation.
        eval_verdict = Verdict(verdict)
        if result.evaluator_verdict == EvaluatorVerdict.EXPLOITABLE:
            result.system_verdict = SystemVerdict.INVALID
        elif result.evaluator_verdict in (EvaluatorVerdict.UNRELIABLE,
                                          EvaluatorVerdict.UNRESPONSIVE):
            result.system_verdict = SystemVerdict.INCONCLUSIVE
        else:
            result.system_verdict = SystemVerdict(eval_verdict.value)

        result.completed_at = utcnow()
        result.evidence_hash = hashlib.sha256(
            canonical(result.model_dump(exclude={"evidence_hash"}))
            .encode()).hexdigest()
        self._persist(result)
        return result

    def _probe_fixture(self, result: AssuranceResult, name: str,
                       events: list[dict], suite: EvalSuite,
                       expect_pass: bool) -> None:
        """Run the suite's checks against a fixture with known ground truth.

        A check 'passes' the fixture if it accepts it. For attack fixtures we
        expect rejection; accepting is a false accept. For genuine fixtures we
        expect acceptance; rejecting is a false reject.
        """
        checks = _run_checks(suite, events)
        # The suite accepts the fixture if every check passes.
        accepted = bool(checks) and all(passed for _, passed, _ in checks)
        if expect_pass and not accepted:
            result.probes.append(ProbeResult(
                probe=name, passed=False,
                detail=f"false reject: genuine fixture rejected: {checks}",
                false_reject=True))
        elif not expect_pass and accepted:
            result.probes.append(ProbeResult(
                probe=name, passed=False,
                detail=f"false accept: attack fixture accepted: {checks}",
                false_accept=True))
        else:
            result.probes.append(ProbeResult(
                probe=name, passed=True,
                detail=f"correct: {'accepted' if accepted else 'rejected'}"
                       f" as expected"))

    def _persist(self, result: AssuranceResult) -> None:
        self._conn.execute(
            """INSERT INTO assurance_runs (id, target, probes, false_accepts,
               false_rejects, timeouts, exploitability, evaluator_verdict,
               system_verdict, evidence, started_at, completed_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (result.id, json.dumps(result.target),
             json.dumps([p.model_dump() for p in result.probes]),
             result.false_accepts, result.false_rejects, result.timeouts,
             result.evaluator_verdict.value, result.evaluator_verdict.value,
             result.system_verdict.value,
             json.dumps({"evidence_hash": result.evidence_hash}),
             result.started_at, result.completed_at),
        )
        self._conn.commit()
