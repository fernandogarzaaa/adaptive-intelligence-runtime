"""Evaluation: did the system perform well?

Evaluation is separate from assurance. An evaluator measures a subject
(a run, a capability, a policy) against a suite of checks. Checks run against
the persisted event log and evidence ledger — never against the subject's
own claims about itself. Every evaluation records its provenance: suite,
evaluator name + version, evidence hashes.

SUPPORTED means: the available evidence establishes the claimed
proposition within the declared verification scope. It never means
"something happened that looks vaguely related", and it never means
"the agent said it succeeded".

TOOL_SUCCESS and CLAIM_SUPPORTED are distinct and must never be
conflated: a successful tool execution is only an *eligible evidence
source*, never by itself evidence of task success.

Three layers (see air.evidence), in order: (1) Evidence Validity —
did this actually happen? (2) Evidence Relevance — does it establish
the claim? (3) Outcome Evaluation — did the objective actually
succeed? A research-grade SUPPORTED verdict requires every claimed
outcome grounded through layers 1 and 2 (the ``outcome_grounding``
check). The ``event_evidence`` check enforces layer 1 only: necessary,
explicitly not sufficient.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from enum import Enum

from pydantic import BaseModel, Field

from air.events.fabric import canonical, utcnow
from air.experience.provenance import NON_EVIDENTIARY, Provenance


def epistemic_refusal(conn, run_id: str) -> str | None:
    """Epistemic separation gate (Invariant #12).

    Returns a refusal reason if the run's evidence cannot ground a
    verification verdict, else None. Simulation, forecast, hypothesis,
    and counterfactual work may inform allocation and generate
    hypotheses, but can NEVER become the basis of a SUPPORTED verdict:
    simulation can generate evidence FOR a hypothesis, but cannot
    itself become evidence that the hypothesis is true in reality.
    """
    kinds = {r[0] for r in conn.execute(
        "SELECT epistemic_kind FROM agents WHERE root_run_id=?",
        (run_id,)).fetchall()}
    if kinds and all(Provenance(k) in NON_EVIDENTIARY for k in kinds):
        return ("all evidence-producing agents are non-evidentiary"
                f" ({sorted(kinds)}): simulation cannot verify reality")
    exp = conn.execute(
        "SELECT outcomes FROM experiences WHERE run_id=?"
        " ORDER BY created_at DESC LIMIT 1", (run_id,)).fetchone()
    if exp:
        prov = (json.loads(exp[0] or "{}")).get("provenance")
        if prov and Provenance(prov) in NON_EVIDENTIARY:
            return (f"experience provenance is {prov}: not real-world"
                    " evidence")
    return None


class Verdict(str, Enum):
    SUPPORTED = "SUPPORTED"
    FALSIFIED = "FALSIFIED"
    INCONCLUSIVE = "INCONCLUSIVE"
    INVALID = "INVALID"
    UNTESTED = "UNTESTED"


class EvalCase(BaseModel):
    id: str
    name: str
    # Check name from the registry + params, e.g. {"check": "event_evidence",
    # "params": {"required": ["tool.completed"]}}
    check: str
    params: dict = Field(default_factory=dict)
    weight: float = 1.0


class EvalSuite(BaseModel):
    id: str = Field(default_factory=lambda: "suite_" + uuid.uuid4().hex[:12])
    name: str
    version: str = "1.0.0"
    cases: list[EvalCase] = Field(default_factory=list)
    created_at: str = Field(default_factory=utcnow)


class CheckResult(BaseModel):
    case_id: str
    passed: bool
    detail: str
    evidence: dict = Field(default_factory=dict)


class EvaluationResult(BaseModel):
    id: str = Field(default_factory=lambda: "eval_" + uuid.uuid4().hex[:12])
    suite_id: str
    suite_version: str
    subject: dict
    evaluator: str
    evaluator_version: str
    checks: list[CheckResult] = Field(default_factory=list)
    metrics: dict = Field(default_factory=dict)
    verdict: Verdict = Verdict.UNTESTED
    evidence_hash: str = ""
    started_at: str = Field(default_factory=utcnow)
    completed_at: str | None = None


# ---------------------------------------------------------------------------
# Check registry. Checks are named, deterministic, auditable functions over
# (events, experience, params). Registration is explicit; no arbitrary code.
#
# The two evidence checks (event_evidence, outcome_grounding) are NOT in
# this registry: they need the ledger connection (tool_calls, agents),
# not just the event list, so the Evaluator binds them explicitly.
# ---------------------------------------------------------------------------
def _check_no_failures(events: list[dict], experience: dict | None,
                       params: dict) -> tuple[bool, str]:
    bad = [e for e in events if e["type"] in
           ("agent.failed", "budget.exhausted", "policy.blocked", "tool.failed")]
    allowed = set(params.get("allow", []))
    bad = [e for e in bad if e["type"] not in allowed]
    if bad:
        return False, f"{len(bad)} failure events: {[e['type'] for e in bad][:5]}"
    return True, "no failure events"


def _check_agents_completed(events: list[dict], experience: dict | None,
                            params: dict) -> tuple[bool, str]:
    min_completed = int(params.get("min_completed", 1))
    n = sum(1 for e in events if e["type"] == "agent.completed")
    if n < min_completed:
        return False, f"only {n} agents completed, need {min_completed}"
    return True, f"{n} agents completed"


def _check_cost_below(events: list[dict], experience: dict | None,
                      params: dict) -> tuple[bool, str]:
    limit = float(params.get("limit_usd", 10.0))
    cost = (experience or {}).get("cost", 0.0)
    if cost > limit:
        return False, f"cost {cost:.4f} exceeds {limit}"
    return True, f"cost {cost:.4f} within {limit}"


def _check_spawn_discipline(events: list[dict], experience: dict | None,
                            params: dict) -> tuple[bool, str]:
    """The runtime should deny wasteful spawns: denials are healthy."""
    max_denied_ratio = float(params.get("max_denied_ratio", 1.0))
    requested = sum(1 for e in events if e["type"] == "spawn.requested")
    denied = sum(1 for e in events if e["type"] == "spawn.denied")
    if requested == 0:
        return True, "no spawns requested"
    ratio = denied / requested
    if ratio > max_denied_ratio:
        return False, f"denied ratio {ratio:.2f} exceeds {max_denied_ratio}"
    return True, f"denied ratio {ratio:.2f} acceptable"


CHECKS: dict[str, callable] = {
    "no_failures": _check_no_failures,
    "agents_completed": _check_agents_completed,
    "cost_below": _check_cost_below,
    "spawn_discipline": _check_spawn_discipline,
}


def register_check(name: str, fn) -> None:
    if name in CHECKS:
        raise ValueError(f"check already registered: {name}")
    CHECKS[name] = fn


class Evaluator:
    """Runs a suite against a subject's persisted history.

    The evidence checks (``event_evidence``, ``outcome_grounding``)
    are bound here rather than in the CHECKS registry because they
    need the ledger connection: validity is verified against the
    ``tool_calls`` and ``agents`` tables, never trusted from the
    event payload alone.
    """

    def __init__(self, conn, name: str = "air-default-evaluator",
                 version: str = "1.0.0") -> None:
        self._conn = conn
        self.name = name
        self.version = version

    def _check_event_evidence(self, run_id: str,
                              params: dict) -> tuple[bool, str]:
        """Layer 1 (validity) gate: the run contains at least one VALID
        evidence source.

        Valid means: a tool.completed event with ok=true, a persisted
        result whose recomputed hash matches, and a non-simulated
        producer. Necessary but explicitly not sufficient for
        SUPPORTED: validity says the execution genuinely happened,
        nothing about whether it establishes any claim.
        """
        from air.evidence.validity import valid_evidence_for_run
        valid = valid_evidence_for_run(self._conn, run_id)
        if not valid:
            return False, (
                "no valid evidence: no tool.completed event with ok=true,"
                " a persisted result, a matching result_hash, and a"
                " non-simulated producer")
        return True, (
            f"{len(valid)} valid evidence source(s):"
            f" {[e.evidence_id for e in valid]}")

    def _check_outcome_grounding(self, run_id: str,
                                 params: dict) -> tuple[bool, str]:
        """Layers 1+2+3: every claimed outcome grounded.

        SUPPORTED requires each claimed outcome to cite valid evidence
        whose verification scope covers the claim's artifacts and
        effects. Fail-closed: no claims, no citations, invalid
        evidence, or scope mismatch all refuse grounding.
        """
        from air.evidence.grounding import ground_claims
        claims = ground_claims(self._conn, run_id)
        if not claims:
            return False, (
                "no claimed outcomes declared in the run: there is no"
                " proposition for evidence to establish")
        ungrounded = [c for c in claims if not c.grounded]
        if ungrounded:
            detail = "; ".join(
                f"{c.claim_id}:"
                f" {c.reasons[0] if c.reasons else 'not grounded'}"
                for c in ungrounded)
            return False, (
                f"{len(ungrounded)}/{len(claims)} claimed outcomes"
                f" ungrounded: {detail}")
        return True, (
            f"all {len(claims)} claimed outcomes grounded:"
            f" {[(c.claim_id, c.evidence_ids) for c in claims]}")

    def evaluate_run(self, run_id: str, suite: EvalSuite) -> EvaluationResult:
        events = self._conn.execute(
            "SELECT type, agent_id, payload FROM events WHERE run_id=?"
            " ORDER BY rowid", (run_id,)).fetchall()
        event_dicts = [{"type": t, "agent_id": a, "payload": json.loads(p)}
                       for t, a, p in events]
        exp_row = self._conn.execute(
            "SELECT outcomes, cost FROM experiences WHERE run_id=?"
            " ORDER BY created_at DESC LIMIT 1", (run_id,)).fetchone()
        experience = None
        if exp_row:
            experience = {"outcomes": json.loads(exp_row[0] or "{}"),
                          "cost": exp_row[1]}
        # Epistemic separation (Invariant #12): simulated / forecast /
        # hypothetical / counterfactual evidence can never yield SUPPORTED.
        # The refusal is itself recorded as a check, so the boundary is
        # auditable rather than silent.
        refusal = epistemic_refusal(self._conn, run_id)
        if refusal:
            result = EvaluationResult(
                suite_id=suite.id, suite_version=suite.version,
                subject={"kind": "run", "run_id": run_id},
                evaluator=self.name, evaluator_version=self.version)
            result.checks.append(CheckResult(
                case_id="epistemic_separation", passed=False,
                detail=f"refused: {refusal}",
                evidence={"boundary": "evaluation_entry"}))
            result.verdict = Verdict.INVALID
            result.metrics = {"score": 0.0, "total": 1.0, "pass_rate": 0.0}
            result.completed_at = utcnow()
            result.evidence_hash = hashlib.sha256(
                canonical(result.model_dump(exclude={"evidence_hash"}))
                .encode()).hexdigest()
            self._persist(result)
            return result
        return self._run_suite(suite, {"kind": "run", "run_id": run_id},
                               event_dicts, experience)

    def _run_suite(self, suite: EvalSuite, subject: dict,
                   events: list[dict], experience: dict | None) -> EvaluationResult:
        result = EvaluationResult(
            suite_id=suite.id, suite_version=suite.version, subject=subject,
            evaluator=self.name, evaluator_version=self.version)
        if not events:
            result.verdict = Verdict.INVALID
            result.completed_at = utcnow()
            self._persist(result)
            return result
        score, total = 0.0, 0.0
        for case in suite.cases:
            if case.check in ("event_evidence", "outcome_grounding"):
                # Evidence checks need the ledger connection and the run
                # id; they are bound on the Evaluator, not in CHECKS.
                if subject.get("kind") != "run" or not subject.get("run_id"):
                    passed = False
                    detail = (f"{case.check} requires a run subject")
                    extra_evidence = {"check": case.check,
                                      "params": case.params}
                else:
                    run_id = subject["run_id"]
                    if case.check == "event_evidence":
                        passed, detail = self._check_event_evidence(
                            run_id, case.params)
                    else:
                        passed, detail = self._check_outcome_grounding(
                            run_id, case.params)
                    extra_evidence = {"check": case.check,
                                      "params": case.params}
                result.checks.append(CheckResult(
                    case_id=case.id, passed=passed, detail=detail,
                    evidence=extra_evidence))
                total += case.weight
                if passed:
                    score += case.weight
                continue
            fn = CHECKS.get(case.check)
            if fn is None:
                result.checks.append(CheckResult(
                    case_id=case.id, passed=False,
                    detail=f"unknown check: {case.check}"))
                total += case.weight
                continue
            try:
                passed, detail = fn(events, experience, case.params)
            except Exception as e:  # noqa: BLE001 - check errors are evidence
                passed, detail = False, f"check raised {type(e).__name__}: {e}"
            result.checks.append(CheckResult(
                case_id=case.id, passed=passed, detail=detail,
                evidence={"check": case.check, "params": case.params}))
            total += case.weight
            if passed:
                score += case.weight
        result.metrics = {"score": round(score, 3), "total": round(total, 3),
                          "pass_rate": round(score / total, 3) if total else 0.0}
        if total == 0:
            result.verdict = Verdict.INCONCLUSIVE
        elif score == total:
            result.verdict = Verdict.SUPPORTED
        elif score == 0:
            result.verdict = Verdict.FALSIFIED
        else:
            result.verdict = Verdict.INCONCLUSIVE
        result.completed_at = utcnow()
        result.evidence_hash = hashlib.sha256(
            canonical(result.model_dump(exclude={"evidence_hash"}))
            .encode()).hexdigest()
        self._persist(result)
        return result

    def _persist(self, result: EvaluationResult) -> None:
        self._conn.execute(
            """INSERT INTO evaluation_runs (id, suite_id, subject, evaluator,
               evaluator_version, metrics, verdict, evidence, started_at,
               completed_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (result.id, result.suite_id, json.dumps(result.subject),
             result.evaluator, result.evaluator_version,
             json.dumps(result.metrics), result.verdict.value,
             json.dumps({"checks": [c.model_dump() for c in result.checks],
                         "evidence_hash": result.evidence_hash}),
             result.started_at, result.completed_at),
        )
        self._conn.commit()

    def save_suite(self, suite: EvalSuite) -> EvalSuite:
        self._conn.execute(
            "INSERT OR IGNORE INTO evaluation_suites (id, name, version, cases,"
            " created_at) VALUES (?,?,?, ?,?)",
            (suite.id, suite.name, suite.version,
             json.dumps([c.model_dump() for c in suite.cases]),
             suite.created_at),
        )
        self._conn.commit()
        return suite
