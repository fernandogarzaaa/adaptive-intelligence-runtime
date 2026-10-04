"""Adversarial evaluator tests: the grounding boundary under attack.

Beyond the regression tests, these pin the fail-closed behavior of
the evidence validity + relevance layers:

- cross-run evidence citation is refused;
- a tampered result_hash is detected by recomputation;
- a simulator's successful tool call is not real-world evidence
  (Invariant 12);
- one evidence double-cited for two unrelated claims grounds only
  the claim its scope covers;
- citing a denied tool's event is refused;
- citing a nonexistent event is refused.
"""

from __future__ import annotations

import asyncio
import json

from air.agents.models import AgentStatus
from air.agents.runtime import AgentRuntime
from air.allocation.allocator import Strategy
from air.config import AirConfig
from air.evaluation.suites import (
    EvalCase,
    EvalSuite,
    Evaluator,
    Verdict,
)
from air.evidence.grounding import ground_claims
from air.evidence.validity import build_evidence
from air.persistence.db import Database, find_migrations_dir


def _env(tmp_path):
    config = AirConfig(data_dir=tmp_path)
    db = Database(tmp_path / "air.db")
    db.migrate(find_migrations_dir())
    return AgentRuntime(config, db), db


def _suite() -> EvalSuite:
    return EvalSuite(name="adversarial", version="1.0.0", cases=[
        EvalCase(id="g1", name="evidence validity",
                 check="event_evidence", params={}),
        EvalCase(id="g2", name="outcome grounding",
                 check="outcome_grounding", params={}),
    ])


def _completed_event_id(db, run_id: str, event_type="tool.completed") -> str:
    row = db.conn.execute(
        "SELECT event_id FROM events WHERE run_id=? AND type=?"
        " ORDER BY rowid DESC LIMIT 1", (run_id, event_type)).fetchone()
    assert row, f"no {event_type} event recorded"
    return row[0]


def _evaluate(db, run_id: str):
    evaluator = Evaluator(db.conn)
    suite = _suite()
    evaluator.save_suite(suite)
    return evaluator.evaluate_run(run_id, suite)


def _check(result, case_id: str):
    for c in result.checks:
        if c.case_id == case_id:
            return c
    raise AssertionError(f"check {case_id} missing")


def _complete_with_outcomes(rt, run_id, agent_id, outcomes):
    rt._set_status(rt.get_agent(agent_id), AgentStatus.COMPLETED)
    return rt.emit(
        "agent.completed", run_id=run_id, agent_id=agent_id,
        payload={"result": {"ok": True, "outcomes": outcomes}})


def test_cross_run_evidence_citation_is_refused(tmp_path):
    """Run B cites run A's genuine write: cross-run evidence cannot
    ground run B's claim."""
    async def main():
        rt, db = _env(tmp_path)
        run_a = await rt.create_run("write report_a.md",
                                    strategy=Strategy.SINGLE_AGENT)
        agent_a = await rt.create_agent(run_a, "worker", "write report_a.md",
                                        granted=["WRITE"])
        await rt.call_tool(agent_a.id, "fs.write",
                           {"path": "report_a.md", "content": "# A"})
        event_a = _completed_event_id(db, run_a)

        run_b = await rt.create_run("write report_b.md",
                                    strategy=Strategy.SINGLE_AGENT)
        agent_b = await rt.create_agent(run_b, "worker", "write report_b.md",
                                        granted=["WRITE"])
        await rt.call_tool(agent_b.id, "fs.write",
                           {"path": "report_b.md", "content": "# B"})
        await _complete_with_outcomes(rt, run_b, agent_b.id, [
            {"id": "o1", "description": "report_b written",
             "artifacts": ["report_b.md"], "effects": ["create"],
             # Cites run A's evidence, not run B's own write.
             "evidence_refs": [f"ev_{event_a}"]}])
        await rt.emit("run.completed", run_id=run_b, payload={})
        ev, reasons = build_evidence(db.conn, run_b, event_a)
        assert ev is None, "cross-run evidence must not project"
        assert any("not the" in r and "evaluated run" in r for r in reasons)
        return _evaluate(db, run_b)
    result = asyncio.run(main())
    assert not _check(result, "g2").passed
    assert result.verdict != Verdict.SUPPORTED


def test_tampered_result_hash_is_detected(tmp_path):
    """The payload's result_hash is recomputed, never trusted: tampering
    with the recorded hash invalidates the evidence."""
    async def main():
        rt, db = _env(tmp_path)
        run_id = await rt.create_run("write quarterly_report.md",
                                     strategy=Strategy.SINGLE_AGENT)
        agent = await rt.create_agent(run_id, "worker",
                                      "write quarterly_report.md",
                                      granted=["WRITE"])
        await rt.call_tool(agent.id, "fs.write",
                           {"path": "quarterly_report.md",
                            "content": "# Q3"})
        event_id = _completed_event_id(db, run_id)
        # Tamper: rewrite the event payload's claimed hash.
        (payload,) = db.conn.execute(
            "SELECT payload FROM events WHERE event_id=?",
            (event_id,)).fetchone()
        doc = json.loads(payload)
        doc["result_hash"] = "0" * 64
        db.conn.execute("UPDATE events SET payload=? WHERE event_id=?",
                        (json.dumps(doc), event_id))
        db.conn.commit()
        ev, reasons = build_evidence(db.conn, run_id, event_id)
        assert ev is None, "tampered hash must not project to evidence"
        assert any("mismatch" in r for r in reasons), reasons
        return event_id
    event_id = asyncio.run(main())
    assert event_id  # (validity proven above; the run is not evaluated)


def test_simulator_evidence_is_refused(tmp_path):
    """A simulator's successful tool call is not real-world evidence
    (Invariant 12), even when cited by an OBSERVED agent's claim."""
    async def main():
        rt, db = _env(tmp_path)
        run_id = await rt.create_run("write quarterly_report.md",
                                     strategy=Strategy.SINGLE_AGENT)
        sim = await rt.create_agent(run_id, "sim", "write quarterly_report.md",
                                    granted=["WRITE"],
                                    epistemic_kind="SIMULATED")
        await rt.call_tool(sim.id, "fs.write",
                           {"path": "quarterly_report.md",
                            "content": "# simulated"})
        event_id = _completed_event_id(db, run_id)
        ev, reasons = build_evidence(db.conn, run_id, event_id)
        assert ev is None, "simulator output must not be evidence"
        assert any("SIMULATED" in r for r in reasons), reasons
        # An OBSERVED agent cites the simulator's write.
        agent = await rt.create_agent(run_id, "worker",
                                      "write quarterly_report.md",
                                      granted=["READ"])
        await _complete_with_outcomes(rt, run_id, agent.id, [
            {"id": "o1", "description": "quarterly report written",
             "artifacts": ["quarterly_report.md"], "effects": ["create"],
             "evidence_refs": [f"ev_{event_id}"]}])
        await rt.emit("run.completed", run_id=run_id, payload={})
        return _evaluate(db, run_id)
    result = asyncio.run(main())
    assert not _check(result, "g2").passed
    assert result.verdict != Verdict.SUPPORTED


def test_double_cited_evidence_grounds_only_the_covered_claim(tmp_path):
    """One genuine write cited by two claims: the claim whose
    artifacts the scope covers is grounded; the unrelated one is not.
    Overall the run is not SUPPORTED."""
    async def main():
        rt, db = _env(tmp_path)
        run_id = await rt.create_run("write reports",
                                     strategy=Strategy.SINGLE_AGENT)
        agent = await rt.create_agent(run_id, "worker", "write reports",
                                      granted=["WRITE"])
        await rt.call_tool(agent.id, "fs.write",
                           {"path": "quarterly_report.md",
                            "content": "# Q3"})
        event_id = _completed_event_id(db, run_id)
        ref = f"ev_{event_id}"
        await _complete_with_outcomes(rt, run_id, agent.id, [
            {"id": "o1", "description": "quarterly report written",
             "artifacts": ["quarterly_report.md"], "effects": ["create"],
             "evidence_refs": [ref]},
            {"id": "o2", "description": "annual report written",
             "artifacts": ["annual_report.md"], "effects": ["create"],
             "evidence_refs": [ref]}])
        await rt.emit("run.completed", run_id=run_id, payload={})
        claims = {c.claim_id: c
                  for c in ground_claims(db.conn, run_id)}
        assert claims["o1"].grounded, claims["o1"].reasons
        assert not claims["o2"].grounded, \
            "unrelated claim must not ride on another claim's evidence"
        return _evaluate(db, run_id)
    result = asyncio.run(main())
    assert not _check(result, "g2").passed
    assert result.verdict != Verdict.SUPPORTED


def test_citing_denied_tool_event_is_refused(tmp_path):
    """A tool.denied event is not a tool.completed event: citing it is
    refused at the validity layer."""
    async def main():
        rt, db = _env(tmp_path)
        run_id = await rt.create_run("read secrets",
                                     strategy=Strategy.SINGLE_AGENT)
        # No grants: the read is denied.
        agent = await rt.create_agent(run_id, "worker", "read secrets",
                                      granted=[])
        await rt.call_tool(agent.id, "fs.read", {"path": "secrets.txt"})
        denied_id = _completed_event_id(db, run_id, "tool.denied")
        ev, reasons = build_evidence(db.conn, run_id, denied_id)
        assert ev is None, "a denied tool call is not evidence"
        assert any("not tool.completed" in r for r in reasons), reasons
        await _complete_with_outcomes(rt, run_id, agent.id, [
            {"id": "o1", "description": "secrets read",
             "artifacts": ["secrets.txt"], "effects": ["observe"],
             "evidence_refs": [f"ev_{denied_id}"]}])
        await rt.emit("run.completed", run_id=run_id, payload={})
        return _evaluate(db, run_id)
    result = asyncio.run(main())
    assert not _check(result, "g1").passed
    assert not _check(result, "g2").passed
    assert result.verdict != Verdict.SUPPORTED


def test_citing_nonexistent_event_is_refused(tmp_path):
    """An evidence_ref that resolves to nothing fails closed."""
    async def main():
        rt, db = _env(tmp_path)
        run_id = await rt.create_run("write quarterly report",
                                     strategy=Strategy.SINGLE_AGENT)
        agent = await rt.create_agent(run_id, "worker",
                                      "write quarterly report",
                                      granted=["READ"])
        ev, reasons = build_evidence(db.conn, run_id, "ev_nope_missing")
        assert ev is None
        assert any("no ledger event" in r for r in reasons), reasons
        await _complete_with_outcomes(rt, run_id, agent.id, [
            {"id": "o1", "description": "quarterly report written",
             "artifacts": ["quarterly_report.md"], "effects": ["create"],
             "evidence_refs": ["ev_nope_missing"]}])
        await rt.emit("run.completed", run_id=run_id, payload={})
        return _evaluate(db, run_id)
    result = asyncio.run(main())
    assert not _check(result, "g2").passed
    assert result.verdict != Verdict.SUPPORTED
