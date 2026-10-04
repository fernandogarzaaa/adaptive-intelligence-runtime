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
    """Genuine evidence under the current grounding semantics.

    A real (fixture) tool.completed with ok=true, a persisted result
    whose recomputed hash matches the payload claim, a non-simulated
    producer, and a claim that cites it with covering scope. Anything
    less is not "genuine" anymore: presence of the event type alone
    is not evidence (see air.evidence).
    """
    preimage = json.dumps({"ok": True, "output": {"bytes": 42},
                           "framing": "untrusted"}, sort_keys=True)
    digest = hashlib.sha256(preimage.encode()).hexdigest()
    return [
        {"type": "tool.completed", "agent_id": "a1",
         "payload": {"tool": "fs.read", "call_id": "tc_good1",
                     "ok": True, "result_hash": digest,
                     "result_preimage": preimage,
                     "args": {"path": "evidence.txt"}}},
        {"type": "agent.completed", "agent_id": "a1",
         "payload": {"result": {"ok": True, "outcomes": [
             {"id": "o1", "description": "evidence read",
              "artifacts": ["evidence.txt"], "effects": ["observe"],
              "evidence_refs": ["ev_fixture_evt_0"]}]}}},
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


def _fixture_ledger(events: list[dict]):
    """Materialize fixture events into an in-memory ledger.

    The evidence checks (event_evidence, outcome_grounding) verify
    against ledger rows, never bare event dicts, so the fixture
    runner gives them a real (throwaway) ledger. Fixture
    tool.completed events carry ``result_preimage`` (test-only) so
    the backing tool_calls row has a result whose recomputed hash
    matches; attack fixtures simply omit tool execution, which is
    precisely what makes them invalid. Returns (conn, run_id);
    the caller closes the connection.
    """
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE events (event_id TEXT PRIMARY KEY, timestamp TEXT,"
        " run_id TEXT, agent_id TEXT, type TEXT, payload TEXT,"
        " causation_id TEXT, correlation_id TEXT)")
    conn.execute(
        "CREATE TABLE tool_calls (id TEXT PRIMARY KEY, run_id TEXT,"
        " agent_id TEXT, tool_name TEXT, state TEXT, args_redacted TEXT,"
        " result_redacted TEXT, result_hash TEXT)")
    conn.execute(
        "CREATE TABLE agents (id TEXT PRIMARY KEY, epistemic_kind TEXT)")
    run_id = "fixture_run"
    for i, e in enumerate(events):
        event_id = f"fixture_evt_{i}"
        payload = dict(e.get("payload") or {})
        agent_id = e.get("agent_id") or "a1"
        conn.execute(
            "INSERT INTO events (event_id, timestamp, run_id, agent_id,"
            " type, payload) VALUES (?,?,?,?,?,?)",
            (event_id, utcnow(), run_id, agent_id, e["type"],
             json.dumps(payload)))
        conn.execute(
            "INSERT OR IGNORE INTO agents (id, epistemic_kind)"
            " VALUES (?, 'OBSERVED')", (agent_id,))
        if e["type"] == "tool.completed" and payload.get("call_id"):
            conn.execute(
                "INSERT INTO tool_calls (id, run_id, agent_id, tool_name,"
                " state, args_redacted, result_redacted, result_hash)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (payload["call_id"], run_id, agent_id,
                 payload.get("tool", "unknown"), "COMMITTED",
                 json.dumps(payload.get("args", {})),
                 payload.get("result_preimage"),
                 payload.get("result_hash")))
    conn.commit()
    return conn, run_id


def _run_checks(suite: EvalSuite, events: list[dict],
                experience: dict | None = None,
                ledger_conn=None,
                ledger_run_id: str = "fixture_run",
                ) -> list[tuple[str, bool, str]]:
    out = []
    for case in suite.cases:
        if case.check in ("event_evidence", "outcome_grounding"):
            # Ledger-bound checks: run the Evaluator's real validity /
            # relevance logic against the fixture ledger. Without a
            # ledger there is nothing to verify against: fail-closed.
            if ledger_conn is None:
                out.append((case.id, False,
                            f"{case.check} requires a ledger connection"))
                continue
            from air.evaluation.suites import Evaluator
            evaluator = Evaluator(ledger_conn)
            try:
                if case.check == "event_evidence":
                    passed, detail = evaluator._check_event_evidence(
                        ledger_run_id, case.params)
                else:
                    passed, detail = evaluator._check_outcome_grounding(
                        ledger_run_id, case.params)
            except Exception as e:  # noqa: BLE001
                passed, detail = False, f"check raised {type(e).__name__}: {e}"
            out.append((case.id, passed, detail))
            continue
        fn = CHECKS.get(case.check)
        if fn is None:
            out.append((case.id, False, f"unknown check {case.check}"))
            continue
        try:
            passed, detail = fn(events, experience, case.params)
        except Exception as e:  # noqa: BLE001
            passed, detail = False, f"check raised {type(e).__name__}: {e}"
        out.append((case.id, passed, detail))
    return out


def _policy_good_fixture() -> dict:
    return {"baseline": {"success_rate": 0.82, "verified_rate": 0.76,
                         "avg_cost": 0.41},
            "candidate": {"success_rate": 0.86, "verified_rate": 0.79,
                          "avg_cost": 0.38}}


def _policy_regressed_fixture() -> dict:
    # The dangerous shape: raw success improves while verified performance
    # deteriorates. A guardrailed suite must reject this.
    return {"baseline": {"success_rate": 0.82, "verified_rate": 0.76,
                         "avg_cost": 0.41},
            "candidate": {"success_rate": 0.84, "verified_rate": 0.71,
                          "avg_cost": 0.39}}


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

        subject = json.loads(subject_json)
        is_policy_suite = any(c.check.startswith("policy_")
                              for c in suite.cases)
        if is_policy_suite:
            self._assure_policy_suite(result, suite)
        else:
            self._assure_event_suite(result, suite)

        # Probe: epistemic separation (Invariant #12). If the evaluated
        # subject's evidence is simulated / forecast / hypothetical /
        # counterfactual and the evaluator still returned SUPPORTED, the
        # evaluator let simulation verify reality: false accept.
        self._probe_epistemic_separation(result, subject, verdict)

        # Probe: correlated evaluator (evaluator authored/generated the subject).
        author = subject.get("author") or subject.get("generated_by")
        if author and author == evaluator:
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

    def _assure_event_suite(self, result: AssuranceResult,
                              suite: EvalSuite) -> None:
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

    def _assure_policy_suite(self, result: AssuranceResult,
                             suite: EvalSuite) -> None:
        # Probe: a candidate that improves raw success while verified
        # performance deteriorates must be REJECTED. A suite without the
        # guardrail accepts it: false accept, evaluator EXPLOITABLE.
        self._probe_fixture(result, "policy_regression_guardrail",
                            [], suite, expect_pass=False,
                            experience=_policy_regressed_fixture())
        # Control: a genuine improvement must be accepted.
        self._probe_fixture(result, "policy_control_good",
                            [], suite, expect_pass=True,
                            experience=_policy_good_fixture())

    def _probe_epistemic_separation(self, result: AssuranceResult,
                                      subject: dict, verdict: str) -> None:
        """Invariant #12 at the assurance boundary: simulated evidence
        must never underpin a SUPPORTED verdict. If the evaluator did
        that, it is exploitable; if it correctly refused, the probe
        passes."""
        from air.evaluation.suites import Verdict as EvalVerdict
        from air.evaluation.suites import epistemic_refusal
        run_ids: list[str] = []
        if subject.get("kind") == "run" and subject.get("run_id"):
            run_ids = [subject["run_id"]]
        elif subject.get("task_runs"):
            run_ids = list(subject["task_runs"])
        refusals = {rid: epistemic_refusal(self._conn, rid)
                    for rid in run_ids}
        simulated = {rid: r for rid, r in refusals.items() if r}
        if not simulated:
            result.probes.append(ProbeResult(
                probe="epistemic_separation", passed=True,
                detail="subject evidence is real-world; nothing to refuse"))
            return
        if verdict == EvalVerdict.SUPPORTED.value:
            result.probes.append(ProbeResult(
                probe="epistemic_separation", passed=False,
                detail=f"false accept: evaluator returned SUPPORTED on"
                       f" non-evidentiary subject {simulated}",
                false_accept=True))
        else:
            result.probes.append(ProbeResult(
                probe="epistemic_separation", passed=True,
                detail=f"correct: evaluator refused simulated subject"
                       f" with {verdict}: {simulated}"))

    def _probe_fixture(self, result: AssuranceResult, name: str,
                       events: list[dict], suite: EvalSuite,
                       expect_pass: bool,
                       experience: dict | None = None) -> None:
        """Run the suite's checks against a fixture with known ground truth.

        A check 'passes' the fixture if it accepts it. For attack fixtures we
        expect rejection; accepting is a false accept. For genuine fixtures we
        expect acceptance; rejecting is a false reject.
        """
        fixture_conn, fixture_run_id = _fixture_ledger(events)
        try:
            checks = _run_checks(suite, events, experience,
                                 ledger_conn=fixture_conn,
                                 ledger_run_id=fixture_run_id)
        finally:
            fixture_conn.close()
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
