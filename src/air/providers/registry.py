"""Provider registry: builds providers from AirConfig, routes by requirements."""

from __future__ import annotations

from air.config import AirConfig, ProviderConfig
from air.providers.base import (
    ModelCapabilities,
    Provider,
    RoutedModel,
    TaskRequirements,
    route,
)
from air.providers.openai_compat import OpenAICompatProvider

# Default per-1k pricing used for routing when the operator has not set better
# numbers. Documented as estimates, not quotes.
_DEFAULT_PRICING: dict[str, tuple[float, float]] = {
    "openai": (0.002, 0.008),
    "anthropic": (0.003, 0.015),
    "gemini": (0.001, 0.004),
    "ollama": (0.0, 0.0),
}


class ProviderRegistry:
    def __init__(self, config: AirConfig) -> None:
        self._config = config
        self._providers: dict[str, Provider] = {}
        self.unavailable: dict[str, str] = {}
        for pc in config.providers:
            if not pc.enabled:
                continue
            try:
                self._providers[pc.name] = self._build(pc)
            except ValueError as e:
                # Honest boundary: configured but not yet implemented providers
                # are reported as unavailable, never faked.
                self.unavailable[pc.name] = str(e)

    def _build(self, pc: ProviderConfig) -> Provider:
        price_in, price_out = _DEFAULT_PRICING.get(pc.name, (0.002, 0.008))
        caps = ModelCapabilities(local=(pc.kind == "ollama"))
        if pc.kind in ("openai_compat", "ollama"):
            return OpenAICompatProvider.from_config(
                name=pc.name, base_url=pc.base_url or "http://127.0.0.1:11434/v1",
                model=pc.model or "llama3.1", api_key_env=pc.api_key_env,
                cost_per_1k_in=price_in, cost_per_1k_out=price_out, caps=caps,
            )
        # Anthropic/Gemini providers land in the next build slice; the registry
        # reports them as configured-but-unavailable rather than faking them.
        raise ValueError(f"provider kind not yet implemented: {pc.kind} ({pc.name})")

    def get(self, name: str) -> Provider | None:
        return self._providers.get(name)

    def names(self) -> list[str]:
        return list(self._providers)

    def route(self, requirements: TaskRequirements) -> RoutedModel | None:
        candidates: list[tuple[Provider, str, float, ModelCapabilities]] = []
        for pc in self._config.providers:
            if not pc.enabled:
                continue
            provider = self._providers.get(pc.name)
            if provider is None:
                continue
            price_in, _ = _DEFAULT_PRICING.get(pc.name, (0.002, 0.008))
            candidates.append((provider, pc.model or "default", price_in,
                               provider.capabilities()))
        return route(requirements, candidates)
