"""Cross-run credential isolation.

Threat model (mapped from the actual codebase, not invented):

Credentials in AIR live ONLY in process environment variables:
- provider API keys: src/air/providers/openai_compat.py:48-50 reads
  os.environ[api_key_env] at construction, held in memory, sent as a
  Bearer header. Never persisted.
- MCP connector credentials: src/air/mcp/connectors.py:104-118 reads
  os.environ[env_name] per request ("env values never stored").
- MCP server configs: src/air/mcp/client.py:88-96 redacts env values
  before persisting.

There is NO central credential store and NO per-run credential
scoping: src/air/mcp/* and src/air/providers/* contain zero run_id
references. MCP servers/connectors are process-global shared
infrastructure.

Isolation therefore means:
- a credential VALUE never appears in any persisted surface (DB
  tables, event ledger) nor in any API response, for any run;
- no agent can obtain a credential value through any tool,
  including shell.exec subprocess environment inheritance;
- secrets that flow through the system (args, results, messages,
  denial reasons) are redacted at every persistence boundary.

Each test names its vector and ends with its verdict.
"""

import asyncio
import json
import os
import sqlite3

import pytest

from air.agents.runtime import AgentRuntime
from air.config import AirConfig
from air.mcp.connectors import ConnectorConfig, ConnectorManager
from air.memory.store import MemoryStore, MemoryType
from air.persistence.db import Database, find_migrations_dir
from air.security.approvals import ApprovalStore
from air.security.policy import CapabilityClass, PolicyDenied
from air.tools import ToolCallRequest, ToolCallState, ToolDefinition

# Distinctive values matching the redaction patterns (sk- prefix with 16+
# alphanumerics, and key= assignment). Realistic secret shapes only: the
# pattern-based redaction boundary is heuristic by design.
SECRET = "sk-abcdefghij1234567890"
SECRET_KV = f"api_key={SECRET}"
ENV_A = "AIR_TEST_VAULT_A"
ENV_CONN = "AIR_TEST_CONN_SECRET"


def _env(tmp_path):
    config = AirConfig(data_dir=tmp_path)
    db = Database(tmp_path / "air.db")
    db.migrate(find_migrations_dir())
    rt = AgentRuntime(config, db)
    return rt, db


async def _agent(rt, run_id, granted, role="worker"):
    return await rt.create_agent(run_id, role, "do work", granted=granted)


def _req(agent, tool, args):
    return ToolCallRequest(tool_name=tool, args=args, agent_id=agent.id,
                           run_id=agent.root_run_id)


def _db_text(db) -> str:
    """Every text cell in the database, for leak scanning."""
    out = []
    tables = [r[0] for r in db.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    for t in tables:
        try:
            for row in db.conn.execute(f"SELECT * FROM {t}").fetchall():
                out.append(" ".join(str(c) for c in row if c is not None))
        except sqlite3.Error:
            pass
    return "\n".join(out)


def _events_text(db) -> str:
    return "\n".join(
        r[0] for r in db.conn.execute(
            "SELECT type || ' ' || payload FROM events").fetchall())


# ------------------------------------------------- 1. no credential-ID surface
def test_no_credential_id_indirection_exists(tmp_path):
    """VERDICT: not-applicable-with-reason. There is no credential-ID
    indirection to forge: connectors reference env var names directly,
    providers read env at construction. Assert the absence explicitly."""
    rt, db = _env(tmp_path)
    schema = "\n".join(
        r[0] for r in db.conn.execute(
            "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL").fetchall())
    assert "credential_id" not in schema.lower()
    assert "credential" not in schema.lower(), \
        "unexpected credential column in schema"
    # And no run scoping exists on the credential paths either.
    for rel in ("src/air/mcp/connectors.py", "src/air/mcp/client.py",
                "src/air/providers/openai_compat.py",
                "src/air/providers/registry.py"):
        src = (rt.config.data_dir and __import__("pathlib").Path(
            __file__).resolve().parents[1] / rel).read_text()
        assert "run_id" not in src, f"{rel} grew run scoping?"


def test_unknown_connector_auth_type_is_denied(tmp_path):
    """The nearest real vector to 'forged credential reference': an
    unknown auth type must be refused, never passed through."""
    rt, db = _env(tmp_path)
    mgr = ConnectorManager(db.conn)
    cfg = ConnectorConfig(id="c1", base_url="http://example.com",
                          auth={"type": "magic_vault", "env": ENV_A})
    with pytest.raises(PolicyDenied):
        mgr._auth_headers(cfg)


# ------------------------------------------- 2/3. shell.exec environment
def test_shell_exec_subprocess_gets_no_secrets(tmp_path, monkeypatch):
    """VERDICT: fixed. shell.exec used to inherit the full process env,
    so any EXECUTE-granted agent could printenv every provider key and
    connector credential. The subprocess now gets PATH only."""
    monkeypatch.setenv(ENV_A, SECRET)

    async def main():
        rt, db = _env(tmp_path)
        run_id = await rt.create_run("t")
        agent = await _agent(rt, run_id, ["EXECUTE"])
        rec = await rt.tool_gateway().execute(
            _req(agent, "shell.exec", {"argv": ["printenv"]}))
        assert rec.state == ToolCallState.COMMITTED, rec.state
        row = db.conn.execute(
            "SELECT result_redacted FROM tool_calls WHERE id=?",
            (rec.id,)).fetchone()
        assert SECRET not in (row[0] or ""), "secret in persisted result"
        assert ENV_A not in (row[0] or "") or SECRET not in (row[0] or "")
        assert SECRET not in _events_text(db), "secret in event ledger"
        assert SECRET not in _db_text(db), "secret anywhere in DB"

    asyncio.run(main())


def test_shell_exec_still_functions(tmp_path):
    """No regression: scrubbed env still runs commands."""
    async def main():
        rt, db = _env(tmp_path)
        run_id = await rt.create_run("t")
        agent = await _agent(rt, run_id, ["EXECUTE"])
        rec = await rt.tool_gateway().execute(
            _req(agent, "shell.exec", {"argv": ["echo", "hi"]}))
        assert rec.state == ToolCallState.COMMITTED
        row = db.conn.execute(
            "SELECT result_redacted FROM tool_calls WHERE id=?",
            (rec.id,)).fetchone()
        assert "hi" in (row[0] or "")

    asyncio.run(main())


# ------------------------------------------- 4/5. event payload redaction
def test_denied_event_payload_has_no_secret(tmp_path):
    """VERDICT: fixed. tool.denied events carried the raw denial reason
    (which can echo secret arg values via jsonschema errors) while the
    DB row was redacted. Both are redacted now."""
    async def main():
        rt, db = _env(tmp_path)
        run_id = await rt.create_run("t")
        agent = await _agent(rt, run_id, [])  # no grants; validation runs first
        rec = await rt.tool_gateway().execute(
            _req(agent, "fs.read", {"path": {"api_key": SECRET}}))
        assert rec.state == ToolCallState.DENIED, rec.state
        payloads = _events_text(db)
        assert "tool.denied" in payloads
        assert SECRET not in payloads, "secret in denied event payload"
        row = db.conn.execute(
            "SELECT error FROM tool_calls WHERE id=?", (rec.id,)).fetchone()
        assert SECRET not in (row[0] or "")

    asyncio.run(main())


def test_failed_event_payload_has_no_secret(tmp_path):
    """VERDICT: fixed. tool.failed events carried the raw error."""
    async def main():
        rt, db = _env(tmp_path)

        async def _boom(args, ctx):
            raise RuntimeError(f"backend exploded: {SECRET_KV}")

        rt.tool_gateway()._registry.register(
            ToolDefinition(name="test.boom", description="t",
                           input_schema={"type": "object", "properties": {}},
                           capability=CapabilityClass.READ), _boom)
        run_id = await rt.create_run("t")
        agent = await _agent(rt, run_id, ["READ"])
        rec = await rt.tool_gateway().execute(_req(agent, "test.boom", {}))
        assert rec.state == ToolCallState.FAILED, rec.state
        assert SECRET not in _events_text(db), "secret in failed event"
        row = db.conn.execute(
            "SELECT error FROM tool_calls WHERE id=?", (rec.id,)).fetchone()
        assert SECRET not in (row[0] or "")

    asyncio.run(main())


# ------------------------------------------------- 6. message redaction
def test_message_payload_redacted_at_rest(tmp_path):
    """VERDICT: fixed. agent_messages.payload was persisted unredacted;
    a secret forwarded via message lived in the DB in plaintext."""
    async def main():
        rt, db = _env(tmp_path)
        run_id = await rt.create_run("t")
        a = await _agent(rt, run_id, [], role="a")
        b = await _agent(rt, run_id, [], role="b")
        await rt.send_message(run_id, a.id, b.id, "sibling", "note",
                              {"text": f"here: {SECRET_KV}"})
        row = db.conn.execute(
            "SELECT payload FROM agent_messages").fetchone()
        assert SECRET not in (row[0] or ""), "secret in message row"
        assert "***REDACTED***" in (row[0] or "")

    asyncio.run(main())


# ------------------------------------------------- 7/8. tool I/O redaction
def test_tool_args_redacted_at_rest(tmp_path):
    """VERDICT: holds. Args are redacted at INSERT (REQUESTED)."""
    async def main():
        rt, db = _env(tmp_path)
        run_id = await rt.create_run("t")
        agent = await _agent(rt, run_id, [])
        rec = await rt.tool_gateway().execute(
            _req(agent, "fs.write",
                 {"path": "x.txt", "content": f"key {SECRET_KV}"}))
        assert rec.state == ToolCallState.DENIED  # no WRITE grant
        row = db.conn.execute(
            "SELECT args_redacted FROM tool_calls WHERE id=?",
            (rec.id,)).fetchone()
        assert SECRET not in (row[0] or "")
        assert SECRET not in _db_text(db)

    asyncio.run(main())


def test_tool_result_secret_redacted_at_rest_but_live_to_caller(tmp_path):
    """VERDICT: holds. The persisted result is redacted; the live result
    dict handed to the calling agent is raw BY DESIGN (the agent needs
    its data). The boundary is persistence/display, not the agent's
    working memory."""
    async def main():
        rt, db = _env(tmp_path)

        async def _leaky(args, ctx):
            return {"ok": True, "data": f"vault says {SECRET_KV}"}

        rt.tool_gateway()._registry.register(
            ToolDefinition(name="test.leaky", description="t",
                           input_schema={"type": "object", "properties": {}},
                           capability=CapabilityClass.READ), _leaky)
        run_id = await rt.create_run("t")
        agent = await _agent(rt, run_id, ["READ"])
        rec = await rt.tool_gateway().execute(_req(agent, "test.leaky", {}))
        assert rec.state == ToolCallState.COMMITTED
        row = db.conn.execute(
            "SELECT result_redacted FROM tool_calls WHERE id=?",
            (rec.id,)).fetchone()
        assert SECRET not in (row[0] or ""), "secret in persisted result"
        assert SECRET not in _events_text(db)
        assert SECRET not in _db_text(db)

    asyncio.run(main())


# ------------------------------------------------- 9/10. connector credentials
def test_connector_credential_value_never_persisted(tmp_path, monkeypatch):
    """VERDICT: holds. The env VALUE is read per request and never
    written; even the env var NAME is redacted at registration."""
    monkeypatch.setenv(ENV_CONN, SECRET)
    rt, db = _env(tmp_path)
    mgr = ConnectorManager(db.conn)
    cfg = ConnectorConfig(id="c9", base_url="http://example.com",
                          auth={"type": "bearer_env", "env": ENV_CONN})
    mgr.register(cfg)
    headers = mgr._auth_headers(mgr._config("c9"))
    assert headers == {"Authorization": f"Bearer {SECRET}"}
    assert SECRET not in _db_text(db), "credential value persisted"
    # The env var NAME is a reference, not a secret: it must be
    # persisted so the connector resolves after a restart.
    row = db.conn.execute(
        "SELECT config FROM connectors WHERE id='c9'").fetchone()
    assert ENV_CONN in (row[0] or ""), "env name should persist as a ref"


def test_connector_credential_survives_restart_without_persisting(
        tmp_path, monkeypatch):
    """VERDICT: holds. Restart = new manager over the same DB file.
    The credential still resolves from the process env; the DB still
    holds no value."""
    monkeypatch.setenv(ENV_CONN, SECRET)
    rt, db = _env(tmp_path)
    mgr = ConnectorManager(db.conn)
    mgr.register(ConnectorConfig(
        id="c10", base_url="http://example.com",
        auth={"type": "header", "header": "X-Key", "env": ENV_CONN}))
    db.conn.close()  # process death
    db2 = Database(tmp_path / "air.db")
    mgr2 = ConnectorManager(db2.conn)  # fresh process state, same file
    headers = mgr2._auth_headers(mgr2._config("c10"))
    assert headers == {"X-Key": SECRET}
    assert SECRET not in _db_text(db2)


# ------------------------------------------------- 11. indirect leakage chain
def test_indirect_leakage_chain_is_broken_at_persistence(tmp_path):
    """VERDICT: holds after fixes. The adversarial chain: tool A returns
    a secret in its result -> agent forwards it in a message -> another
    agent reads it. Every persistence hop redacts, so the stored and
    API-served forms never carry the value."""
    async def main():
        rt, db = _env(tmp_path)

        async def _leaky(args, ctx):
            return {"ok": True, "data": f"vault says {SECRET_KV}"}

        rt.tool_gateway()._registry.register(
            ToolDefinition(name="test.leaky2", description="t",
                           input_schema={"type": "object", "properties": {}},
                           capability=CapabilityClass.READ), _leaky)
        run_id = await rt.create_run("t")
        a = await _agent(rt, run_id, ["READ"], role="a")
        b = await _agent(rt, run_id, [], role="b")
        rec = await rt.tool_gateway().execute(_req(a, "test.leaky2", {}))
        assert rec.state == ToolCallState.COMMITTED
        # The agent forwards the (raw, by-design) result content onward.
        await rt.send_message(run_id, a.id, b.id, "sibling", "note",
                              {"text": f"tool said: vault says {SECRET_KV}"})
        # Agent B reads via the messages surface.
        rows = db.conn.execute(
            "SELECT payload FROM agent_messages WHERE run_id=?",
            (run_id,)).fetchall()
        assert rows, "message not stored"
        for (payload,) in rows:
            assert SECRET not in (payload or ""), \
                "secret survived the message hop"
        assert SECRET not in _db_text(db)

    asyncio.run(main())


def test_cross_run_agent_cannot_reach_other_runs_tool_results(tmp_path):
    """VERDICT: holds. Tool results are namespaced by run_id; the
    messages API is run-scoped and cross-run messaging is denied."""
    async def main():
        rt, db = _env(tmp_path)
        run_a = await rt.create_run("a")
        run_b = await rt.create_run("b")
        a = await _agent(rt, run_a, [], role="a")
        b = await _agent(rt, run_b, [], role="b")
        await rt.send_message(run_a, a.id, None, "evidence", "note",
                              {"text": f"secret {SECRET_KV}"})
        # Run B sees only its own messages.
        rows_b = db.conn.execute(
            "SELECT payload FROM agent_messages WHERE run_id=?",
            (run_b,)).fetchall()
        assert rows_b == []
        # And cannot message into run A.
        try:
            await rt.send_message(run_b, b.id, a.id, "sibling", "note",
                                  {"text": "x"})
            denied = False
        except PermissionError:
            denied = True
        assert denied
        assert SECRET not in _db_text(db)

    asyncio.run(main())


# ------------------------------------------------- 12. forged reference shape
def test_connector_auth_names_are_not_secret_bearers(tmp_path, monkeypatch):
    """Companion to the not-applicable verdict: env NAME squatting is
    process-global by design (like PATH). The value is what is
    protected; assert a missing env fails closed, never empty."""
    rt, db = _env(tmp_path)
    mgr = ConnectorManager(db.conn)
    cfg = ConnectorConfig(id="c11", base_url="http://example.com",
                          auth={"type": "bearer_env", "env": "AIR_TEST_UNSET_X"})
    assert os.environ.get("AIR_TEST_UNSET_X") is None
    with pytest.raises(PolicyDenied):
        mgr._auth_headers(cfg)


# ------------------------------------------------- 13/14. memory + approvals
def test_memory_write_redacted(tmp_path):
    """VERDICT: holds. MemoryStore redacts at write."""
    rt, db = _env(tmp_path)
    store = MemoryStore(db.conn)
    mem = store.store("ns1", MemoryType.EPISODIC,
                      {"note": f"found {SECRET_KV}", "run_id": "r1"})
    got = store.get(mem.id)
    assert SECRET not in json.dumps(got.content), "secret in memory row"
    assert SECRET not in _db_text(db)


def test_approval_payload_redacted(tmp_path):
    """VERDICT: holds. Approval payloads are redacted at request."""
    rt, db = _env(tmp_path)
    store = ApprovalStore(db.conn)
    ap_id = store.request("tool_call", "x",
                          {"tool": "t", "args": {"k": SECRET_KV}}, "agent1")
    db.conn.commit()
    row = db.conn.execute(
        "SELECT payload FROM approvals WHERE id=?", (ap_id,)).fetchone()
    assert SECRET not in (row[0] or ""), "secret in approval row"
    assert SECRET not in _db_text(db)


# ------------------------------------------------- 15/16. API surfaces
def _api_client(tmp_path, monkeypatch):
    monkeypatch.setenv("AIR_DATA_DIR", str(tmp_path))
    import air.api.app as app_module
    app_module._runtime = None
    app_module._ws_queues.clear()
    app_module._sse_queues.clear()
    from fastapi.testclient import TestClient
    from air.api.app import create_app
    return TestClient(create_app())


def test_api_denied_tool_call_carries_no_secret(tmp_path, monkeypatch):
    """VERDICT: holds. A denial whose reason echoes secret args must
    not leak the value through the API response."""
    with _api_client(tmp_path, monkeypatch) as client:
        run_id = client.post("/runs", json={"goal": "t"}).json()["run_id"]
        agent_id = client.post(
            f"/runs/{run_id}/agents",
            json={"role": "w", "objective": "t"}).json()["agent_id"]
        r = client.post(f"/agents/{agent_id}/tools/call",
                        json={"tool_name": "fs.read",
                              "args": {"path": {"api_key": SECRET}}})
        assert r.status_code == 200
        assert SECRET not in r.text, "secret in API denial response"


def test_api_models_health_carry_no_secrets(tmp_path, monkeypatch):
    """VERDICT: holds. Provider/model surfaces never expose key material."""
    monkeypatch.setenv("AIR_TEST_PROBE", SECRET)
    with _api_client(tmp_path, monkeypatch) as client:
        for path in ("/models", "/health", "/ready"):
            r = client.get(path)
            assert SECRET not in r.text, f"secret leaked via {path}"
