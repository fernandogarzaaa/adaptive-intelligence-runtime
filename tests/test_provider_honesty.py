"""Provider honesty boundaries.

Anthropic, Gemini, and OpenAI providers are implemented. The honesty
contract now is:

- When API keys are present, the registry builds them and the router
  may select them. /models reports them as "configured".
- When keys are absent, they are not configured at all (never faked).
- A built provider without a key reports health ok=False, never success.
- Unknown provider kinds are reported as unavailable, never faked.
- With zero usable providers, agent execution is BLOCKED with
  MODEL_PROVIDER_UNAVAILABLE: never faked, never hung.

No real API keys or egress are used in these tests.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

import air.api.app as app_module
from air.api.app import create_app
from air.config import AirConfig, ProviderConfig
from air.providers.anthropic import AnthropicProvider
from air.providers.base import TaskRequirements
from air.providers.gemini import GeminiProvider
from air.providers.openai import OpenAIProvider
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
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-real")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
    return ProviderRegistry(AirConfig.from_env())


def test_new_providers_are_built_when_keys_present(monkeypatch):
    reg = _registry_with_keys(monkeypatch)
    for name, cls in (("openai", OpenAIProvider),
                      ("anthropic", AnthropicProvider),
                      ("gemini", GeminiProvider)):
        assert name in reg.names(), reg.names()
        assert name not in reg.unavailable, reg.unavailable
        p = reg.get(name)
        assert isinstance(p, cls)
        assert p.kind in ("openai", "anthropic", "gemini")


def test_new_providers_absent_without_keys(monkeypatch):
    for var in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY",
                "GOOGLE_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    reg = ProviderRegistry(AirConfig.from_env())
    for name in ("openai", "anthropic", "gemini"):
        assert name not in reg.names()
        assert reg.get(name) is None
    # ollama is always configured (local, no key)
    assert "ollama" in reg.names()


def test_unknown_kind_still_reported_unavailable_not_faked(monkeypatch):
    cfg = AirConfig.from_env()
    cfg.providers.append(ProviderConfig(name="mystery", kind="quantum",
                                        model="q1"))
    reg = ProviderRegistry(cfg)
    assert "mystery" in reg.unavailable
    assert "mystery" not in reg.names()
    assert "not yet implemented" in reg.unavailable["mystery"]


def test_router_may_select_configured_providers(monkeypatch):
    reg = _registry_with_keys(monkeypatch)
    routed = reg.route(TaskRequirements(need_reasoning=True))
    assert routed is not None
    # anthropic is reasoning-capable and cheapest reasoning option here
    assert routed.provider.name in ("anthropic", "openai", "gemini", "ollama")


def test_models_api_reports_configured_with_reason(client, monkeypatch):
    rt = app_module.get_runtime()
    rt.providers = _registry_with_keys(monkeypatch)
    body = client.get("/models").json()
    by_name = {m["provider"]: m for m in body}
    for name in ("openai", "anthropic", "gemini"):
        assert by_name[name]["model"] == "configured", by_name[name]
        assert "capabilities" in by_name[name]
    assert by_name["ollama"]["model"] == "configured"


@pytest.mark.asyncio
async def test_health_without_key_reports_not_ok_never_fakes():
    for p in (AnthropicProvider(model="m", api_key=None),
              GeminiProvider(model="m", api_key=None),
              OpenAIProvider(model="m", api_key=None)):
        h = await p.health()
        assert h.ok is False
        assert "no API key" in (h.reason or "")


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
