"""Provider honesty boundaries.

Anthropic/Gemini are configured-but-unavailable: no implementation, no
keys, no egress in this environment. The system must report that fact at
every surface and never fake success, hang, or raise unhandled.

Covers: registry status, /models projection, routing exclusion, and the
no-provider agent execution path.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

import air.api.app as app_module
from air.api.app import create_app
from air.config import AirConfig
from air.providers.base import TaskRequirements
from air.providers.registry import ProviderRegistry


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


def _registry_with_keys(monkeypatch) -> ProviderRegistry:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
    return ProviderRegistry(AirConfig.from_env())


def test_anthropic_gemini_are_unavailable_never_built(monkeypatch):
    reg = _registry_with_keys(monkeypatch)
    assert "anthropic" in reg.unavailable
    assert "gemini" in reg.unavailable
    assert "anthropic" not in reg.names()
    assert "gemini" not in reg.names()
    assert reg.get("anthropic") is None
    assert reg.get("gemini") is None
    assert "not yet implemented" in reg.unavailable["anthropic"]
    assert "not yet implemented" in reg.unavailable["gemini"]


def test_router_never_selects_unavailable_providers(monkeypatch):
    reg = _registry_with_keys(monkeypatch)
    for req in (TaskRequirements(),
                TaskRequirements(needs_tools=True),
                TaskRequirements(needs_vision=True),
                TaskRequirements(cheap_ok=True)):
        routed = reg.route(req)
        assert routed is None or routed.provider.name not in (
            "anthropic", "gemini"), routed


def test_models_api_reports_unavailable_with_reason(client, monkeypatch):
    # Rebuild the runtime's registry with keys present so the projection
    # sees configured-but-unavailable providers.
    rt = app_module.get_runtime()
    rt.providers = _registry_with_keys(monkeypatch)
    body = client.get("/models").json()
    by_name = {m["provider"]: m for m in body}
    for name in ("anthropic", "gemini"):
        assert by_name[name]["model"] == "unavailable", by_name[name]
        assert "not yet implemented" in by_name[name]["reason"]
    # Configured providers never claim to be reachable without a check.
    assert by_name["ollama"]["model"] == "configured"


def test_no_provider_agent_is_blocked_not_hung_or_faked(client):
    """With zero usable providers, a launched agent is BLOCKED with
    MODEL_PROVIDER_UNAVAILABLE: it never fakes output and never hangs."""
    rt = app_module.get_runtime()
    rt.providers._providers.clear()
    rt.providers.unavailable.clear()
    run_id = client.post(
        "/runs", json={"goal": "honesty probe", "strategy": "single_agent"}
    ).json()["run_id"]
    agent_id = None
    deadline = time.time() + 20
    while time.time() < deadline and agent_id is None:
        agents = client.get(f"/runs/{run_id}/agents").json()
        if agents:
            agent_id = agents[0]["id"]
        else:
            time.sleep(0.2)
    assert agent_id, "plan produced no agent"
    status = None
    deadline = time.time() + 20
    while time.time() < deadline and status not in (
            "BLOCKED", "COMPLETED", "FAILED"):
        status = client.get(f"/agents/{agent_id}").json()["status"]
        time.sleep(0.2)
    agent = client.get(f"/agents/{agent_id}").json()
    assert agent["status"] == "BLOCKED", agent["status"]
    assert "MODEL_PROVIDER_UNAVAILABLE" in (agent.get("status_reason") or "")
    assert agent["provider"] == "scripted"
