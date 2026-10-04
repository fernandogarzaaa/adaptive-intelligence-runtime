"""OpenAI-compatible provider (covers OpenAI itself, Ollama /v1, vLLM, etc.)."""

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


class OpenAICompatProvider(Provider):
    name = "openai_compat"
    kind = "openai_compat"

    def __init__(self, base_url: str, model: str, api_key: str | None = None,
                 name: str = "openai_compat",
                 cost_per_1k_in: float = 0.002, cost_per_1k_out: float = 0.008,
                 caps: ModelCapabilities | None = None) -> None:
        self._base = base_url.rstrip("/")
        self._model = model
        self._key = api_key
        self.name = name
        self._cost_in = cost_per_1k_in
        self._cost_out = cost_per_1k_out
        self._caps = caps or ModelCapabilities(tools=True)

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self._key:
            h["Authorization"] = f"Bearer {self._key}"
        return h

    def _client(self, timeout: float = 120) -> httpx.AsyncClient:
        # trust_env=False: provider egress goes direct under AIR's own
        # SSRF/allowlist policy (security/policy.py), never via an ambient
        # proxy the operator did not configure for this purpose.
        return httpx.AsyncClient(timeout=timeout, trust_env=False)

    @classmethod
    def from_config(cls, name: str, base_url: str, model: str,
                    api_key_env: str | None, **kw) -> "OpenAICompatProvider":
        key = os.environ.get(api_key_env) if api_key_env else None
        return cls(base_url=base_url, model=model, api_key=key, name=name, **kw)

    async def generate(self, prompt: str, system: str | None = None,
                       max_tokens: int = 1024, temperature: float = 0.2) -> GenerationResult:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        async with self._client() as client:
            resp = await client.post(
                f"{self._base}/chat/completions",
                headers=self._headers(),
                json={"model": self._model, "messages": messages,
                      "max_tokens": max_tokens, "temperature": temperature},
            )
            resp.raise_for_status()
            data = resp.json()
        choice = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        return GenerationResult(
            text=choice,
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            model=self._model, provider=self.name,
        )

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        return input_tokens / 1000 * self._cost_in + output_tokens / 1000 * self._cost_out

    async def health(self) -> ProviderHealth:
        if self._key is None and "127.0.0.1" not in self._base and "localhost" not in self._base:
            return ProviderHealth(ok=False, reason="no API key configured")
        start = time.monotonic()
        try:
            async with self._client(timeout=10) as client:
                resp = await client.get(f"{self._base}/models",
                                        headers=self._headers())
                ok = resp.status_code < 500
                reason = None if ok else f"HTTP {resp.status_code}"
        except Exception as e:  # noqa: BLE001 - health must report, not raise
            return ProviderHealth(ok=False, reason=f"{type(e).__name__}: {e}")
        return ProviderHealth(ok=ok, reason=reason,
                              latency_ms=(time.monotonic() - start) * 1000)

    def capabilities(self) -> ModelCapabilities:
        return self._caps
