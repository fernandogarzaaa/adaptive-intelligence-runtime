"""The mandatory experiment, before the console:

    Goal -> Cognitive allocation -> Agent spawned -> Capability requested
    -> Gateway authorization -> MCP/tool execution -> Observed result
    -> Experience -> Evaluation -> Assurance -> Policy/capability learning

Then deliberately attack the middle:

    malicious tool result -> attempted capability escalation
    -> attempted budget bypass -> attempted cross-run access

The expected result is not merely "request failed": AIR produces
structured evidence explaining exactly why each action was denied, while
preserving the event chain and experience record.
"""

import asyncio
import json
import sys
from pathlib import Path

from air.agents.runtime import AgentRuntime
from air.allocation.allocator import Strategy
from air.assurance.probes import AssuranceEngine
from air.config import AirConfig
from air.evaluation.suites import (EvalCase, EvalSuite, Evaluator,
                                   Verdict)
from air.experience.recorder import ExperienceRecorder
from air.learning.bridge import LearningBridge
from air.mcp import MCPClientManager, MCPServerConfig
from air.persistence.db import Database, find_migrations_dir
from air.security.policy import CapabilityClass
from air.tools import ToolCallState


def _env(tmp_path):
    config = AirConfig(data_dir=tmp_path)
    db = Database(tmp_path / "air.db")
    db.migrate(find_migrations_dir())
    return AgentRuntime(config, db), db


async def _register_mcp_tools(rt, db, server_id="chain1"):
    """Register the fixture MCP server's tools into the gateway registry.
    The ONLY path to them is the execution gateway."""
    mgr = MCPClientManager(db.conn)
    cfg = MCPServerConfig(
        id=server_id, transport="stdio", command=sys.executable,
        args=[str(Path(__file__).parent / "fixtures"
                  / "poison_mcp_server.py")],
        timeout_s=15.0, default_capability=CapabilityClass.READ)
    await mgr.register(cfg)
    try:
        tools = await mgr.discover(server_id)
        gw = rt.tool_gateway()
        for definition in mgr.tool_definitions(server_id, tools):
            tool_short = definition.name.split(".")[-1]
            gw._registry.register(definition,
                                  mgr.handler_for(server_id, tool_short))
    finally:
        await mgr.disconnect(server_id)
    return mgr


async def _wait_run(rt, run_id, timeout_s=30):
    for _ in range(int(timeout_s / 0.05)):
        row = rt.db.conn.execute("SELECT status FROM runs WHERE id=?",
                                 (run_id,)).fetchone()
        if row[0] in ("COMPLETED", "FAILED"):
            return row[0]
        await asyncio.sleep(0.05)
    return row[0]


def test_full_chain_goal_to_learning(tmp_path):
    rt, db = _env(tmp_path)

    async def tool_user(agent, runtime):
        rec = await runtime.call_tool(
            agent.id, "mcp.chain1.echo", {"text": "chain evidence"})
        assert rec.state == ToolCallState.COMMITTED
        return {"ok": True, "tool_state": rec.state,
                "result_hash": rec.result_hash}

    async def main():
        await _register_mcp_tools(rt, db)
        rt.register_behavior("specialist", tool_user)
        run_id = await rt.create_run(
            "gather evidence through an MCP tool",
            strategy=Strategy.SINGLE_AGENT, tool_call_budget=10)
        await rt.start_run(run_id)
        assert await _wait_run(rt, run_id) == "COMPLETED"

        # Gateway authorization produced an auditable ToolCall.
        row = db.conn.execute(
            "SELECT tool_name, capability, state, policy_version,"
            " authorization_decision, result_hash, provenance"
            " FROM tool_calls WHERE run_id=?", (run_id,)).fetchone()
        assert row is not None, "no tool call recorded"
        tool_name, capability, state, policy_version, authz_json, rhash, prov = row
        assert tool_name == "mcp.chain1.echo"
        assert capability == "READ" and state == "COMMITTED"
        assert policy_version and rhash
        authz = json.loads(authz_json)
        assert authz["verdict"] == "GRANT"
        assert json.loads(prov)["untrusted"] is True

        # Experience captured the tool action with its provenance.
        exps = ExperienceRecorder(db.conn).list()
        assert len(exps) == 1
        actions = json.loads(db.conn.execute(
            "SELECT actions FROM experiences WHERE id=?",
            (exps[0]["id"],)).fetchone()[0])
        tool_actions = [a for a in actions
                        if a.get("type") == "tool.call"]
        assert len(tool_actions) == 1
        assert tool_actions[0]["capability"] == "READ"
        assert tool_actions[0]["result_hash"] == rhash
        assert tool_actions[0]["policy_version"] == policy_version

        # Evaluation of the run's own history.
        suite = EvalSuite(name="chain", version="1.0.0", cases=[
            EvalCase(id="c1", name="evidence", check="event_evidence",
                     params={}),
            EvalCase(id="c2", name="no failures", check="no_failures",
                     params={}),
            EvalCase(id="c3", name="agents completed",
                     check="agents_completed", params={"min_completed": 1}),
        ])
        evaluator = Evaluator(db.conn, name="chain-evaluator",
                              version="1.0.0")
        evaluator.save_suite(suite)
        result = evaluator.evaluate_run(run_id, suite)
        assert result.verdict == Verdict.SUPPORTED, result.checks

        # Independent assurance of that evaluation.
        ar = AssuranceEngine(db.conn).assure(result.id)
        assert ar.system_verdict.value == "SUPPORTED", \
            [p.model_dump() for p in ar.probes]

        # Link the evidence, then the validated learning bridge promotes
        # knowledge: run -> experience -> evidence -> evaluation ->
        # assurance -> memory. No direct run->memory path exists.
        ExperienceRecorder(db.conn).link_evaluation(
            exps[0]["id"], result.id, ar.id)
        mem_id = LearningBridge(db.conn).promote_to_knowledge(
            exps[0]["id"], "chain-ns",
            {"lesson": "MCP echo tool served evidence through the gateway"})
        mem = db.conn.execute(
            "SELECT provenance_kind, provenance FROM memories WHERE id=?",
            (mem_id,)).fetchone()
        assert mem[0] == "DERIVED"
        detail = json.loads(mem[1])
        assert detail["validated_by"] == [result.id]

    asyncio.run(main())


def test_attack_the_middle_produces_structured_evidence(tmp_path):
    rt, db = _env(tmp_path)

    async def attacker(agent, runtime):
        gw = runtime.tool_gateway()
        from air.tools import ToolCallRequest
        evidence = {}
        # 1. Malicious tool result: instructions masquerading as data.
        rec = await runtime.call_tool(
            agent.id, "mcp.chain2.lying_tool", {})
        row = runtime.db.conn.execute(
            "SELECT result_redacted FROM tool_calls WHERE id=?",
            (rec.id,)).fetchone()
        framed = json.loads(row[0])
        evidence["framing"] = framed["framing"]
        evidence["untrusted"] = framed["provenance"]["untrusted"]
        # 2. Capability escalation: READ agent tries WRITE.
        rec2 = await gw.execute(ToolCallRequest(
            tool_name="fs.write", args={"path": "pwned.txt",
                                        "content": "x"},
            agent_id=agent.id, run_id=agent.root_run_id))
        evidence["escalation_state"] = rec2.state
        evidence["escalation_reason"] = (
            rec2.authorization_decision["denial_reason"])
        evidence["escalation_checks"] = (
            rec2.authorization_decision["checks"])
        # 3. Budget bypass: tool_call_budget=1, this is the third call.
        rec3 = await runtime.call_tool(
            agent.id, "mcp.chain2.echo", {"text": "over budget"})
        evidence["budget_state"] = rec3.state
        # 4. Cross-run access: forge another run's id.
        rec4 = await gw.execute(ToolCallRequest(
            tool_name="mcp.chain2.echo", args={"text": "x"},
            agent_id=agent.id, run_id="run_forged"))
        evidence["cross_run_state"] = rec4.state
        evidence["cross_run_reason"] = (
            rec4.authorization_decision["denial_reason"])
        await runtime.emit("experiment.evidence", run_id=agent.root_run_id,
                           agent_id=agent.id, payload={"evidence": evidence})
        return {"ok": True}

    async def main():
        await _register_mcp_tools(rt, db, server_id="chain2")
        rt.register_behavior("specialist", attacker)
        run_id = await rt.create_run("attack the execution boundary",
                                     strategy=Strategy.SINGLE_AGENT,
                                     tool_call_budget=1)
        await rt.start_run(run_id)
        assert await _wait_run(rt, run_id) == "COMPLETED"

        row = db.conn.execute(
            "SELECT payload FROM events WHERE run_id=? AND type=?"
            " ORDER BY rowid DESC LIMIT 1",
            (run_id, "experiment.evidence")).fetchone()
        evidence = json.loads(row[0])["evidence"]

        # 1. The lie arrived framed as untrusted data, not an instruction.
        assert "UNTRUSTED TOOL OUTPUT" in evidence["framing"]
        assert evidence["untrusted"] is True
        # 2. Escalation denied with the exact cause, per-check evidence.
        assert evidence["escalation_state"] == "DENIED"
        assert "WRITE" in evidence["escalation_reason"]
        assert evidence["escalation_checks"]["capability"]["ok"] is False
        # 3. Budget bypass failed: the run allowed 1 tool call; the lying
        # tool consumed it, so this call failed on budget.
        assert evidence["budget_state"] == "FAILED"
        # 4. Cross-run denied at the identity/scope check.
        assert evidence["cross_run_state"] == "DENIED"
        assert "scope" in evidence["cross_run_reason"].lower()

        # The event chain preserves everything: denials are first-class.
        types = [r[0] for r in db.conn.execute(
            "SELECT type FROM events WHERE run_id=?", (run_id,)).fetchall()]
        assert "tool.denied" in types
        assert "tool.completed" in types
        # And the experience record kept the denied attempts.
        exps = ExperienceRecorder(db.conn).list()
        assert len(exps) == 1
        actions = json.loads(db.conn.execute(
            "SELECT actions FROM experiences WHERE id=?",
            (exps[0]["id"],)).fetchone()[0])
        states = {a.get("status") for a in actions
                  if a.get("type") == "tool.call"}
        assert "DENIED" in states and "COMMITTED" in states

    asyncio.run(main())
