"""Anthropic (Claude) provider via the Messages API."""

from __future__ import annotations

import os
import time

import httpx

from air.providers.base import (
    GenerationResult,
    ModelCapabilities,
    Provider,
    ProviderHealth,
)

_ANTHROPIC_VERSION = "2023-06-01"


class AnthropicProvider(Provider):
    name = "anthropic"
    kind = "anthropic"

    def __init__(self, model: str, api_key: str | None = None,
                 base_url: str = "https://api.anthropic.com",
                 name: str = "anthropic",
                 cost_per_1k_in: float = 0.003, cost_per_1k_out: float = 0.015,
                 caps: ModelCapabilities | None = None) -> None:
        self._base = base_url.rstrip("/")
        self._model = model
        self._key = api_key
        self.name = name
        self._cost_in = cost_per_1k_in
        self._cost_out = cost_per_1k_out
        self._caps = caps or ModelCapabilities(tools=True, reasoning=True)

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json",
             "anthropic-version": _ANTHROPIC_VERSION}
        if self._key:
            h["x-api-key"] = self._key
        return h

    def _client(self, timeout: float = 120) -> httpx.AsyncClient:
        # trust_env=False: provider egress goes direct under AIR's own
        # SSRF/allowlist policy (security/policy.py), never via an ambient
        # proxy the operator did not configure for this purpose.
        return httpx.AsyncClient(timeout=timeout, trust_env=False)

    @classmethod
    def from_config(cls, name: str, base_url: str | None, model: str,
                    api_key_env: str | None, **kw) -> "AnthropicProvider":
        key = os.environ.get(api_key_env) if api_key_env else None
        return cls(base_url=base_url or "https://api.anthropic.com",
                   model=model, api_key=key, name=name, **kw)

    async def generate(self, prompt: str, system: str | None = None,
                       max_tokens: int = 1024,
                       temperature: float = 0.2) -> GenerationResult:
        body: dict = {
            "model": self._model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            body["system"] = system
        async with self._client() as client:
            resp = await client.post(f"{self._base}/v1/messages",
                                     headers=self._headers(), json=body)
            resp.raise_for_status()
            data = resp.json()
        text = "".join(
            block.get("text", "")
            for block in data.get("content", [])
            if block.get("type") == "text"
        )
        usage = data.get("usage", {})
        return GenerationResult(
            text=text,
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            model=data.get("model", self._model),
            provider=self.name,
        )

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        return (input_tokens / 1000 * self._cost_in
                + output_tokens / 1000 * self._cost_out)

    async def health(self) -> ProviderHealth:
        if self._key is None:
            return ProviderHealth(ok=False, reason="no API key configured")
        start = time.monotonic()
        try:
            async with self._client(timeout=10) as client:
                resp = await client.get(f"{self._base}/v1/models",
                                        headers=self._headers())
                ok = resp.status_code < 500
                reason = None if ok else f"HTTP {resp.status_code}"
        except Exception as e:  # noqa: BLE001 - health must report, not raise
            return ProviderHealth(ok=False, reason=f"{type(e).__name__}: {e}")
        return ProviderHealth(ok=ok, reason=reason,
                              latency_ms=(time.monotonic() - start) * 1000)

    def capabilities(self) -> ModelCapabilities:
        return self._caps
