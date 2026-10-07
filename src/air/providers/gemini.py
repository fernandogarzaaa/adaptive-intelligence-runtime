"""Google Gemini provider via the Generative Language API (generateContent)."""

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


class GeminiProvider(Provider):
    name = "gemini"
    kind = "gemini"

    def __init__(self, model: str, api_key: str | None = None,
                 base_url: str = "https://generativelanguage.googleapis.com",
                 name: str = "gemini",
                 cost_per_1k_in: float = 0.001, cost_per_1k_out: float = 0.004,
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
            h["x-goog-api-key"] = self._key
        return h

    def _client(self, timeout: float = 120) -> httpx.AsyncClient:
        # trust_env=False: provider egress goes direct under AIR's own
        # SSRF/allowlist policy (security/policy.py), never via an ambient
        # proxy the operator did not configure for this purpose.
        return httpx.AsyncClient(timeout=timeout, trust_env=False)

    @classmethod
    def from_config(cls, name: str, base_url: str | None, model: str,
                    api_key_env: str | None, **kw) -> "GeminiProvider":
        key = os.environ.get(api_key_env) if api_key_env else None
        return cls(base_url=base_url or "https://generativelanguage.googleapis.com",
                   model=model, api_key=key, name=name, **kw)

    async def generate(self, prompt: str, system: str | None = None,
                       max_tokens: int = 1024,
                       temperature: float = 0.2) -> GenerationResult:
        body: dict = {
            "contents": [{"role": "user",
                          "parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": max_tokens,
                                 "temperature": temperature},
        }
        if system:
            body["system_instruction"] = {"parts": [{"text": system}]}
        url = f"{self._base}/v1beta/models/{self._model}:generateContent"
        async with self._client() as client:
            resp = await client.post(url, headers=self._headers(), json=body)
            resp.raise_for_status()
            data = resp.json()
        text = ""
        candidates = data.get("candidates", [])
        if candidates:
            parts = candidates[0].get("content", {}).get("parts", [])
            text = "".join(p.get("text", "") for p in parts)
        usage = data.get("usageMetadata", {})
        return GenerationResult(
            text=text,
            input_tokens=usage.get("promptTokenCount", 0),
            output_tokens=usage.get("candidatesTokenCount", 0),
            model=self._model,
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
                resp = await client.get(f"{self._base}/v1beta/models",
                                        headers=self._headers())
                ok = resp.status_code < 500
                reason = None if ok else f"HTTP {resp.status_code}"
        except Exception as e:  # noqa: BLE001 - health must report, not raise
            return ProviderHealth(ok=False, reason=f"{type(e).__name__}: {e}")
        return ProviderHealth(ok=ok, reason=reason,
                              latency_ms=(time.monotonic() - start) * 1000)

    def capabilities(self) -> ModelCapabilities:
        return self._caps
