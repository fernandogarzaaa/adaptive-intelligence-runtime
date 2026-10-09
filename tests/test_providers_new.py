"""Unit tests for Anthropic, Gemini, and OpenAI providers.

The HTTP layer is mocked with httpx.MockTransport: no real API keys,
no network egress. Tests cover request shape, response parsing,
error propagation, cost math, capabilities, and from_config wiring.
"""

from __future__ import annotations

import json

import httpx
import pytest

from air.providers.anthropic import AnthropicProvider
from air.providers.gemini import GeminiProvider
from air.providers.openai import OpenAIProvider


def _mocked(provider, handler):
    """Patch a provider's _client to use httpx.MockTransport."""
    transport = httpx.MockTransport(handler)
    provider._client = lambda timeout=120: httpx.AsyncClient(
        transport=transport, timeout=timeout, trust_env=False)
    return provider


# ---------------------------------------------------------------- anthropic

def _anthropic_ok(request: httpx.Request) -> httpx.Response:
    assert request.url.path == "/v1/messages"
    assert request.headers["x-api-key"] == "test-key"
    assert request.headers["anthropic-version"] == "2023-06-01"
    body = json.loads(request.content)
    assert body["model"] == "claude-haiku-4-5"
    assert body["messages"] == [{"role": "user", "content": "hello"}]
    assert body["system"] == "be brief"
    assert body["max_tokens"] == 42
    return httpx.Response(200, json={
        "id": "msg_1", "type": "message", "model": "claude-haiku-4-5",
        "content": [{"type": "text", "text": "hi there"}],
        "usage": {"input_tokens": 10, "output_tokens": 5},
    })


@pytest.mark.asyncio
async def test_anthropic_generate_parses_response():
    p = _mocked(AnthropicProvider(model="claude-haiku-4-5", api_key="test-key"),
                _anthropic_ok)
    r = await p.generate("hello", system="be brief", max_tokens=42)
    assert r.text == "hi there"
    assert r.input_tokens == 10
    assert r.output_tokens == 5
    assert r.model == "claude-haiku-4-5"
    assert r.provider == "anthropic"


@pytest.mark.asyncio
async def test_anthropic_generate_no_system():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={
            "content": [{"type": "text", "text": "ok"}],
            "usage": {"input_tokens": 1, "output_tokens": 1}})
    p = _mocked(AnthropicProvider(model="m", api_key="k"), handler)
    r = await p.generate("hi")
    assert r.text == "ok"
    assert "system" not in seen


@pytest.mark.asyncio
async def test_anthropic_http_error_propagates_not_swallowed():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "bad key"}})
    p = _mocked(AnthropicProvider(model="m", api_key="bad"), handler)
    with pytest.raises(httpx.HTTPStatusError):
        await p.generate("hi")


def test_anthropic_estimate_cost():
    p = AnthropicProvider(model="m", cost_per_1k_in=0.003,
                          cost_per_1k_out=0.015)
    assert p.estimate_cost(1000, 1000) == pytest.approx(0.018)


def test_anthropic_capabilities():
    caps = AnthropicProvider(model="m").capabilities()
    assert caps.tools is True
    assert caps.local is False


def test_anthropic_from_config_reads_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "env-key-123")
    p = AnthropicProvider.from_config(name="anthropic", base_url=None,
                                      model="claude-haiku-4-5",
                                      api_key_env="ANTHROPIC_API_KEY")
    assert p._key == "env-key-123"
    assert p.name == "anthropic"
    assert p.kind == "anthropic"


# ------------------------------------------------------------------- gemini

def _gemini_ok(request: httpx.Request) -> httpx.Response:
    assert request.url.path == "/v1beta/models/gemini-2.0-flash:generateContent"
    assert request.headers["x-goog-api-key"] == "test-key"
    body = json.loads(request.content)
    assert body["contents"] == [{"role": "user",
                                 "parts": [{"text": "hello"}]}]
    assert body["system_instruction"] == {"parts": [{"text": "be brief"}]}
    assert body["generationConfig"]["maxOutputTokens"] == 42
    return httpx.Response(200, json={
        "candidates": [{"content": {"role": "model",
                                    "parts": [{"text": "hi there"}]}}],
        "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5},
    })


@pytest.mark.asyncio
async def test_gemini_generate_parses_response():
    p = _mocked(GeminiProvider(model="gemini-2.0-flash", api_key="test-key"),
                _gemini_ok)
    r = await p.generate("hello", system="be brief", max_tokens=42)
    assert r.text == "hi there"
    assert r.input_tokens == 10
    assert r.output_tokens == 5
    assert r.model == "gemini-2.0-flash"
    assert r.provider == "gemini"


@pytest.mark.asyncio
async def test_gemini_generate_no_system():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={
            "candidates": [{"content": {"parts": [{"text": "ok"}]}}],
            "usageMetadata": {}})
    p = _mocked(GeminiProvider(model="m", api_key="k"), handler)
    r = await p.generate("hi")
    assert r.text == "ok"
    assert "system_instruction" not in seen


@pytest.mark.asyncio
async def test_gemini_http_error_propagates_not_swallowed():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"message": "bad request"}})
    p = _mocked(GeminiProvider(model="m", api_key="bad"), handler)
    with pytest.raises(httpx.HTTPStatusError):
        await p.generate("hi")


def test_gemini_estimate_cost():
    p = GeminiProvider(model="m", cost_per_1k_in=0.001,
                       cost_per_1k_out=0.004)
    assert p.estimate_cost(1000, 1000) == pytest.approx(0.005)


def test_gemini_capabilities():
    caps = GeminiProvider(model="m").capabilities()
    assert caps.tools is True
    assert caps.local is False


def test_gemini_from_config_reads_env(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "env-key-123")
    p = GeminiProvider.from_config(name="gemini", base_url=None,
                                    model="gemini-2.0-flash",
                                    api_key_env="GEMINI_API_KEY")
    assert p._key == "env-key-123"
    assert p.name == "gemini"
    assert p.kind == "gemini"


# ------------------------------------------------------------------- openai

def _openai_ok(request: httpx.Request) -> httpx.Response:
    assert request.url.path == "/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer test-key"
    body = json.loads(request.content)
    assert body["model"] == "gpt-4o-mini"
    assert {"role": "system", "content": "be brief"} in body["messages"]
    assert {"role": "user", "content": "hello"} in body["messages"]
    return httpx.Response(200, json={
        "choices": [{"message": {"role": "assistant",
                                 "content": "hi there"}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    })


@pytest.mark.asyncio
async def test_openai_generate_parses_response():
    p = _mocked(OpenAIProvider(model="gpt-4o-mini", api_key="test-key"),
                _openai_ok)
    r = await p.generate("hello", system="be brief")
    assert r.text == "hi there"
    assert r.input_tokens == 10
    assert r.output_tokens == 5
    assert r.provider == "openai"


@pytest.mark.asyncio
async def test_openai_http_error_propagates_not_swallowed():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "bad key"}})
    p = _mocked(OpenAIProvider(model="m", api_key="bad"), handler)
    with pytest.raises(httpx.HTTPStatusError):
        await p.generate("hi")


def test_openai_defaults():
    p = OpenAIProvider()
    assert p._base == "https://api.openai.com/v1"
    assert p._model == "gpt-4o-mini"
    assert p.kind == "openai"
    assert p.name == "openai"


def test_openai_from_config_reads_env(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "env-key-123")
    p = OpenAIProvider.from_config(name="openai",
                                   base_url="https://api.openai.com/v1",
                                   model="gpt-4o-mini",
                                   api_key_env="OPENAI_API_KEY")
    assert p._key == "env-key-123"
    assert isinstance(p, OpenAIProvider)


def test_openai_estimate_cost():
    p = OpenAIProvider(model="m", cost_per_1k_in=0.002,
                       cost_per_1k_out=0.008)
    assert p.estimate_cost(1000, 1000) == pytest.approx(0.010)
