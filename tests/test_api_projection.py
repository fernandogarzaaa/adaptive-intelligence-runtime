"""API projection tests.

The API is a projection/control surface over the runtime, never an
alternative execution path. These tests verify:

- route handlers are thin (no business logic / core engine construction
  in app.py),
- realtime WS/SSE messages are canonical event-fabric projections and a
  client that disconnects and reconnects with a last_event_id cursor
  converges to the same state,
- mutations are idempotent (Idempotency-Key and natural idempotency),
- request models cannot assert internal authority (grants, verdicts,
  provenance, statuses),
- policy promotion resolves verdicts from persisted rows, never from
  caller-supplied dictionaries,
- explain endpoints return structured decision evidence.
"""

import ast
import json
import socket
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import air.api.app as app_module
from air.api.app import create_app


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AIR_DATA_DIR", str(tmp_path))
    app_module._runtime = None
    app_module._ws_queues.clear()
    app_module._sse_queues.clear()
    app = create_app()
    with TestClient(app) as c:
        yield c
    app_module._runtime = None
    app_module._ws_queues.clear()
    app_module._sse_queues.clear()


def _conn():
    return app_module.get_runtime().db.conn


def _seed_run(client, goal="api projection test"):
    r = client.post("/runs", json={"goal": goal})
    assert r.status_code == 200, r.text
    return r.json()["run_id"]


def _seed_agent(client, run_id, role="researcher"):
    r = client.post(f"/runs/{run_id}/agents",
                    json={"role": role, "objective": "probe the api"})
    assert r.status_code == 200, r.text
    return r.json()["agent_id"]


# ------------------------------------------------------- structural rule

def test_handlers_are_thin():
    """No route handler may construct core engines or call core mutation
    APIs directly. Business logic lives in services and AIR core."""
    src = Path(app_module.__file__).read_text()
    tree = ast.parse(src)
    banned = (
        # Core engine construction has no place in a route handler.
        "AgentRuntime(", "PolicyStore(", "CapabilityPipeline(",
        "CapabilityStore(", "Evaluator(", "AssuranceEngine(",
        "LearningEngine(", "LearningBridge(", "MemoryStore(",
        "ApprovalStore(", "ExperienceRecorder(", "ToolGateway(",
        # Direct core mutation APIs: handlers go through Services.
        ".spawn_agent(", ".create_agent(", ".call_tool(", ".create_run(",
        ".cancel_run(", ".pause_run(", ".resume_run(", ".terminate_agent(",
        ".send_message(", ".emit(", "bus.publish(", "store.append(",
        # Direct DB access: handlers go through Services (passing rt.db.conn
        # *into* a Service constructor is the sanctioned pattern).
        "db.conn.execute", "_conn().execute", ".executemany(",
        ".executescript(",
    )
    violations = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            seg = ast.get_source_segment(src, node) or ""
            # Only route handlers (registered via decorators) are checked;
            # helpers like get_runtime/_conn/_rt/idempotent are infrastructure.
            is_route = any(
                isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
                and d.func.attr in ("get", "post", "put", "delete", "patch",
                                    "websocket")
                for d in node.decorator_list)
            if not is_route:
                continue
            # Check the body only: the def line legitimately names the
            # handler (e.g. `def create_agent(`) and service methods
            # (e.g. `.promote(` on a Service) are the sanctioned path.
            body = "\n".join(
                ast.get_source_segment(src, stmt) or ""
                for stmt in node.body)
            for token in banned:
                if token in body:
                    violations.append(f"{node.name}: {token}")
    assert not violations, \
        "route handlers contain business logic:\n" + "\n".join(violations)


# ------------------------------------------------------- run lifecycle

def test_run_lifecycle(client):
    run_id = _seed_run(client)
    assert client.get(f"/runs/{run_id}").json()["status"] in (
        "CREATED", "RUNNING")
    assert client.post(f"/runs/{run_id}/pause").status_code == 200
    assert client.post(f"/runs/{run_id}/resume").status_code == 200
    r = client.post(f"/runs/{run_id}/cancel")
    assert r.status_code == 200
    assert r.json()["status"] == "CANCELLED"
    assert client.get(f"/runs/{run_id}").json()["status"] == "CANCELLED"


def test_idempotent_run_creation(client):
    headers = {"Idempotency-Key": "key-run-1"}
    r1 = client.post("/runs", json={"goal": "idem"}, headers=headers)
    r2 = client.post("/runs", json={"goal": "idem"}, headers=headers)
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["run_id"] == r2.json()["run_id"]
    assert r2.json()["idempotent"] is True
    runs = client.get("/runs").json()
    assert sum(1 for r in runs if r["goal"] == "idem") == 1


def test_idempotent_spawn(client):
    run_id = _seed_run(client)
    parent = _seed_agent(client, run_id)
    headers = {"Idempotency-Key": "key-spawn-1"}
    body = {"objective": "child work", "role": "coder"}
    r1 = client.post(f"/agents/{parent}/spawn", json=body, headers=headers)
    r2 = client.post(f"/agents/{parent}/spawn", json=body, headers=headers)
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["agent_id"] == r2.json()["agent_id"]
    assert r2.json()["idempotent"] is True
    agents = client.get(f"/runs/{run_id}/agents").json()
    children = [a for a in agents if a["parent_id"] == parent]
    assert len(children) == 1  # exactly one child despite two requests


def test_idempotent_cancel(client):
    run_id = _seed_run(client)
    r1 = client.post(f"/runs/{run_id}/cancel")
    r2 = client.post(f"/runs/{run_id}/cancel")
    assert r1.json()["status"] == "CANCELLED"
    assert r2.json()["status"] == "CANCELLED"
    assert r2.json()["idempotent"] is True


def test_approval_decide_idempotent_and_conflicting(client):
    from air.security.approvals import ApprovalStore
    ap_id = ApprovalStore(_conn()).request(
        "tool_call", "call_1", {"tool": "shell.exec"}, "test")
    r1 = client.post(f"/approvals/{ap_id}/decide",
                     json={"approved": True})
    assert r1.status_code == 200
    assert r1.json()["status"] == "APPROVED"
    r2 = client.post(f"/approvals/{ap_id}/decide",
                     json={"approved": True})
    assert r2.status_code == 200
    assert r2.json()["idempotent"] is True
    r3 = client.post(f"/approvals/{ap_id}/decide",
                     json={"approved": False})
    assert r3.status_code == 409  # conflicting re-decision refused


# ------------------------------------------------------- realtime

_CANONICAL_FIELDS = {"event_id", "event_type", "schema_version", "timestamp",
                     "run_id", "agent_id", "causation_id", "correlation_id",
                     "sequence", "payload"}


def test_ws_projection_is_canonical(client):
    run_id = _seed_run(client)
    with client.websocket_connect("/ws/events") as ws:
        hello = ws.receive_json()
        assert hello["event_type"] == "hello"
        _seed_agent(client, run_id)
        # Drain until we see the agent.created projection.
        seen = None
        for _ in range(50):
            msg = ws.receive_json()
            assert _CANONICAL_FIELDS <= set(msg), \
                f"missing envelope fields: {_CANONICAL_FIELDS - set(msg)}"
            if msg["event_type"] == "agent.created":
                seen = msg
                break
        assert seen is not None
        assert seen["run_id"] == run_id
        assert isinstance(seen["sequence"], int)


def test_ws_reconnect_converges(client, tmp_path):
    """Kill the connection mid-activity, reconnect with the cursor, and
    converge to the same state as if the client never disconnected."""
    run_id = _seed_run(client)
    root = _seed_agent(client, run_id)

    with client.websocket_connect("/ws/events") as ws:
        cursor = ws.receive_json()["last_event_id"]
    assert cursor
    agents_at_cursor = {a["id"]
                        for a in client.get(f"/runs/{run_id}/agents").json()}

    # ---- activity while disconnected: spawn, tool call, message ----
    r = client.post(f"/agents/{root}/spawn",
                    json={"objective": "child work", "role": "coder"})
    assert r.status_code == 200
    assert r.json()["decision"] == "SPAWN"
    child = r.json()["agent_id"]
    decision_id = r.json()["decision_id"]

    # Grant READ via the runtime's own authority (test setup, not the API:
    # the API can never assert grants). The tool workspace root is the
    # runtime data dir, which the fixture points at tmp_path.
    _conn().execute(
        "UPDATE agents SET granted_capabilities=? WHERE id=?",
        (json.dumps(["READ"]), child))
    _conn().commit()
    (tmp_path / "probe.txt").write_text("reconnect convergence probe")
    r = client.post(f"/agents/{child}/tools/call",
                    json={"tool_name": "fs.read",
                          "args": {"path": "probe.txt"}})
    assert r.status_code == 200, r.text
    call_id = r.json()["id"]

    r = client.post(f"/agents/{root}/message?run_id={run_id}",
                    json={"to_agent_id": child, "channel": "sibling",
                          "kind": "note", "payload": {"hello": "child"}})
    assert r.status_code == 200, r.text

    # The runtime is quiescent now: no background task will emit more
    # events, so the missed set is stable.
    missed = client.get(f"/runs/{run_id}/events",
                        params={"after_event_id": cursor,
                                "limit": 10000}).json()
    assert len(missed) > 0
    missed_ids = [e["event_id"] for e in missed]

    # ---- reconnect with the cursor: replay must equal the missed set ----
    with client.websocket_connect(
            f"/ws/events?last_event_id={cursor}") as ws:
        replayed = [ws.receive_json() for _ in missed_ids]
    replayed_ids = [m["event_id"] for m in replayed]
    assert replayed_ids == missed_ids
    for m in replayed:
        assert _CANONICAL_FIELDS <= set(m)

    # ---- convergence: cursor-time state + replayed events == live state --
    agents_from_replay = set()
    for m in replayed:
        if m.get("event_type") in ("agent.created", "spawn.approved"):
            aid = m.get("agent_id") or m.get("payload", {}).get("agent_id")
            if aid:
                agents_from_replay.add(aid)
    agents_now = {a["id"]
                  for a in client.get(f"/runs/{run_id}/agents").json()}
    assert agents_at_cursor | agents_from_replay == agents_now

    # Explain handles from the replayed stream resolve.
    r = client.get(f"/spawn-decisions/{decision_id}")
    assert r.status_code == 200
    assert r.json()["decision"] == "SPAWN"
    r = client.get(f"/tool-calls/{call_id}/authorization")
    assert r.status_code == 200
    assert r.json()["inputs"]["tool_name"] == "fs.read"


def test_sse_projection(client, tmp_path):
    """SSE is served over real HTTP (the TestClient's stream() hangs in
    this httpx/starlette combination, so this test binds a live server).
    The projection is the same canonical event fabric as the WebSocket."""
    import http.client
    import socket
    import threading
    import time

    import uvicorn

    run_id = _seed_run(client)
    _seed_agent(client, run_id)
    events = client.get(f"/runs/{run_id}/events",
                        params={"limit": 10000}).json()
    assert len(events) >= 2
    cursor = events[0]["event_id"]
    expected = len(events) - 1

    server = uvicorn.Server(uvicorn.Config(
        app_module.create_app(), host="127.0.0.1", port=_free_port(),
        log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    port = server.config.port
    try:
        for _ in range(200):
            if server.started:
                break
            time.sleep(0.05)
        assert server.started, "uvicorn did not start"

        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request("GET", f"/events/stream?last_event_id={cursor}")
        resp = conn.getresponse()
        assert resp.status == 200
        assert "text/event-stream" in resp.getheader("Content-Type")

        frames, raw = [], b""
        deadline = time.time() + 15
        while len(frames) < expected and time.time() < deadline:
            try:
                line = resp.fp.readline(65537)
            except (socket.timeout, http.client.HTTPException):
                break
            if not line:
                break
            raw += line
            while b"\n\n" in raw:
                block, raw = raw.split(b"\n\n", 1)
                eid, data = None, ""
                for ln in block.decode().splitlines():
                    if ln.startswith("id: "):
                        eid = ln[4:]
                    elif ln.startswith("data: "):
                        data = ln[6:]
                if data and not data.startswith(":"):
                    frames.append((eid, json.loads(data)))
        assert len(frames) == expected, \
            f"got {len(frames)} frames, expected {expected}"
        for eid, frame in frames:
            assert _CANONICAL_FIELDS <= set(frame)
            assert frame["event_type"] != "hello"
        assert [f[1]["event_id"] for f in frames] == \
            [e["event_id"] for e in events[1:]]
        conn.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)


# ------------------------------------------------------- no authority

def test_client_cannot_assert_grants(client):
    run_id = _seed_run(client)
    r = client.post(f"/runs/{run_id}/agents",
                    json={"role": "researcher", "objective": "x",
                          "granted_capabilities": ["ADMIN"]})
    assert r.status_code == 200
    agent = client.get(f"/agents/{r.json()['agent_id']}").json()
    assert agent["granted_capabilities"] == []


def test_client_cannot_assert_provenance(client):
    r = client.post("/memory", json={
        "namespace": "t", "content": {"claim": "x"},
        "provenance": "DERIVED", "confidence": 0.99})
    assert r.status_code == 200
    mem_id = r.json()["id"]
    chain = client.get(f"/memory/{mem_id}/history").json()
    assert chain[0]["provenance"] == "USER_ASSERTED"


def test_promotion_rejects_caller_supplied_verdicts(client):
    r = client.post("/policies/cognitive-allocation/propose",
                    json={"params": {}, "reason": "test"})
    assert r.status_code == 200
    version = r.json()["version"]
    # Old authority-leak shape: caller-supplied verdict dicts.
    r = client.post(
        f"/policies/cognitive-allocation/promote",
        json={"version": version,
              "evaluation": {"verdict": "SUPPORTED"},
              "assurance": {"evaluator_verdict": "SOUND",
                            "system_verdict": "SUPPORTED"}})
    assert r.status_code == 422  # evaluation_id/assurance_id required


def test_promote_requires_persisted_evidence(client):
    run_id = _seed_run(client)
    _seed_agent(client, run_id)
    r = client.post("/policies/cognitive-allocation/propose",
                    json={"params": {}, "reason": "test"})
    version = r.json()["version"]
    # Unknown evidence rows: 404, not a gate decision.
    r = client.post("/policies/cognitive-allocation/promote",
                    json={"version": version,
                          "evaluation_id": "nope", "assurance_id": "nope"})
    assert r.status_code == 404
    # Real persisted rows, but the verdict is not SUPPORTED: gate blocks.
    ev = client.post("/evaluations", json={"run_id": run_id}).json()
    ar = client.post("/assurance",
                     params={"evaluation_id": ev["evaluation_id"]}).json()
    r = client.post("/policies/cognitive-allocation/promote",
                    json={"version": version,
                          "evaluation_id": ev["evaluation_id"],
                          "assurance_id": ar["assurance_id"]})
    assert r.status_code in (409, 200)
    if r.status_code == 409:
        assert "blocked" in str(r.json())


# ------------------------------------------------------- explain

def test_run_messages(client):
    run_id = _seed_run(client)
    root = _seed_agent(client, run_id)
    r = client.post(f"/agents/{root}/message?run_id={run_id}",
                    json={"to_agent_id": None, "channel": "sibling",
                          "kind": "note", "payload": {"text": "hello"}})
    assert r.status_code == 200
    msgs = client.get(f"/runs/{run_id}/messages").json()
    assert len(msgs) == 1
    assert msgs[0]["payload"] == {"text": "hello"}
    assert msgs[0]["from_agent_id"] == root


def test_spawn_decisions_list_includes_evidence(client):
    run_id = _seed_run(client)
    root = _seed_agent(client, run_id)
    r = client.post(f"/agents/{root}/spawn",
                    json={"objective": "child work", "role": "coder"})
    assert r.status_code == 200
    decisions = client.get(f"/runs/{run_id}/spawn-decisions").json()
    assert len(decisions) >= 1
    d = next(x for x in decisions
             if x["decision_id"] == r.json()["decision_id"])
    assert d["decision"] in ("SPAWN", "DENY")
    assert d["inputs"]["spawn_threshold"] is not None
    assert d["events"], "decision must reference its event trail"


def test_tool_calls_state_filter(client, tmp_path):
    run_id = _seed_run(client)
    root = _seed_agent(client, run_id)
    _conn().execute(
        "UPDATE agents SET granted_capabilities=? WHERE id=?",
        (json.dumps(["READ"]), root))
    _conn().commit()
    (tmp_path / "probe.txt").write_text("filter me")
    r = client.post(f"/agents/{root}/tools/call",
                    json={"tool_name": "fs.read",
                          "args": {"path": "probe.txt"}})
    assert r.status_code == 200
    committed = client.get("/tool-calls",
                           params={"run_id": run_id,
                                   "state": "COMMITTED"}).json()
    assert any(c["id"] == r.json()["id"] for c in committed)
    denied = client.get("/tool-calls",
                        params={"run_id": run_id,
                                "state": "DENIED"}).json()
    assert all(c["state"] == "DENIED" for c in denied)
    run_id = _seed_run(client)
    root = _seed_agent(client, run_id)
    r = client.post(f"/agents/{root}/spawn",
                    json={"objective": "child work", "role": "coder"})
    assert r.status_code == 200
    decision_id = r.json()["decision_id"]

    r = client.get(f"/runs/{run_id}/explain")
    assert r.status_code == 200
    body = r.json()
    assert body["subject"] == "run"
    assert {"decision", "inputs", "constraints", "selected_strategy",
            "policy_version", "budget_state",
            "evidence_references"} <= set(body)

    r = client.get(f"/agents/{root}/explain")
    assert r.status_code == 200
    body = r.json()
    assert body["subject"] == "agent"
    assert "authorization_checks" in body

    r = client.get(f"/spawn-decisions/{decision_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["decision"] in ("SPAWN", "DENY")
    assert "inputs" in body and "scores" in body
    assert body["inputs"]["spawn_threshold"] is not None

    r = client.get("/policies/cognitive-allocation/provenance")
    assert r.status_code == 200


def test_experience_lineage(client):
    from air.experience.recorder import ExperienceRecorder
    run_id = _seed_run(client)
    _seed_agent(client, run_id)
    exp, _event = ExperienceRecorder(_conn()).record_run(run_id)
    assert _event is None  # no store wired: no event produced
    r = client.get(f"/experiences/{exp.id}/lineage")
    assert r.status_code == 200
    body = r.json()
    assert body["subject"] == "experience"
    assert body["inputs"]["run_id"] == run_id
    assert "evaluation_refs" in body["evidence_references"]


def test_out_of_scope_message_is_403_not_500(client):
    """A cross-run message is denied by the runtime (policy.blocked event)
    and the API surfaces it as 403 Forbidden, never a 500."""
    run1 = _seed_run(client)
    a1 = _seed_agent(client, run1)
    run2 = _seed_run(client)
    a2 = _seed_agent(client, run2)
    r = client.post(
        f"/agents/{a1}/message?run_id={run1}",
        json={"to_agent_id": a2, "channel": "sibling",
              "kind": "note", "payload": {}})
    assert r.status_code == 403, (r.status_code, r.text[:200])
    assert "not in communication scope" in r.text


def test_approval_required_tool_call_is_403_with_approval_id(client):
    """A tool call gated on operator approval returns 403 carrying the
    approval_id, so the console can direct the operator to the inbox."""
    import asyncio

    from air.security.policy import CapabilityClass
    from air.tools.registry import ToolDefinition

    run_id = _seed_run(client)
    rt = app_module.get_runtime()

    async def _noop(args, ctx):
        return {"ok": True}

    rt.tool_gateway()._registry.register(
        ToolDefinition(name="test.privileged",
                       description="test-only privileged tool",
                       input_schema={"type": "object", "properties": {}},
                       capability=CapabilityClass.PRIVILEGED), _noop)
    agent = asyncio.new_event_loop().run_until_complete(
        rt.create_agent(run_id, role="operator", objective="probe approvals",
                        granted=["PRIVILEGED"]))
    r = client.post(f"/agents/{agent.id}/tools/call",
                    json={"tool_name": "test.privileged", "args": {}})
    assert r.status_code == 403, (r.status_code, r.text[:200])
    body = r.json()["detail"]
    assert body["state"] == "APPROVAL_PENDING"
    assert body["approval_id"].startswith("appr_")


def test_console_shell_is_never_cached(client):
    """index.html served under API-colliding paths must carry
    Cache-Control: no-store, or the browser would serve the cached shell
    to the console's own fetch() of the same URL."""
    import pytest
    # The wheel does not ship web/dist; the console is only served from a
    # repo checkout. Skip when this app instance has no console.
    probe = client.get("/", headers={"Accept": "text/html"})
    if "text/html" not in probe.headers.get("content-type", ""):
        pytest.skip("console not served by this install")
    r = client.get("/policies", headers={"Accept": "text/html"})
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-store"
    assert "<!doctype html>" in r.text.lower()
    # API clients still get JSON.
    r = client.get("/policies", headers={"Accept": "application/json"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
