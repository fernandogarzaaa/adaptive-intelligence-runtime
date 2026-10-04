"""Model provider abstraction.

The intelligence runtime is model-agnostic: no component may hard-code a
specific model as the intelligence. Providers implement this interface;
routing selects among them by task requirements.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from pydantic import BaseModel


class ModelCapabilities(BaseModel):
    vision: bool = False
    reasoning: bool = False
    tools: bool = False
    local: bool = False
    max_tokens: int = 8192


class ProviderHealth(BaseModel):
    ok: bool
    reason: str | None = None
    latency_ms: float | None = None


class TaskRequirements(BaseModel):
    need_reasoning: bool = False
    need_vision: bool = False
    need_tools: bool = False
    privacy_sensitive: bool = False
    cheap_ok: bool = True
    max_cost_per_1k: float | None = None


@dataclass
class GenerationResult:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""
    provider: str = ""


class Provider(ABC):
    name: str = "base"
    kind: str = "base"

    @abstractmethod
    async def generate(self, prompt: str, system: str | None = None,
                       max_tokens: int = 1024, temperature: float = 0.2) -> GenerationResult:
        ...

    async def stream(self, prompt: str, system: str | None = None,
                     max_tokens: int = 1024, temperature: float = 0.2):
        result = await self.generate(prompt, system, max_tokens, temperature)
        yield result.text

    async def structured_output(self, prompt: str, schema: type[BaseModel],
                                system: str | None = None) -> BaseModel:
        import json
        instruction = (f"{prompt}\n\nRespond with a single JSON object matching this schema, "
                       f"no other text. Schema: {schema.model_json_schema()}")
        result = await self.generate(instruction, system)
        return schema.model_validate(json.loads(result.text))

    @abstractmethod
    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        ...

    @abstractmethod
    async def health(self) -> ProviderHealth:
        ...

    @abstractmethod
    def capabilities(self) -> ModelCapabilities:
        ...


@dataclass
class RoutedModel:
    provider: Provider
    model: str
    reason: str


def route(requirements: TaskRequirements, candidates: list[tuple[Provider, str, float, ModelCapabilities]]) -> RoutedModel | None:
    """Score candidate (provider, model, cost_per_1k, caps) and pick the best.

    Honest routing: privacy-sensitive work prefers local; reasoning tasks
    prefer reasoning-capable models; otherwise cheapest capable model wins.
    Returns None when nothing is capable (caller reports MODEL_PROVIDER_UNAVAILABLE).
    """
    scored: list[tuple[float, str, Provider, str]] = []
    for provider, model, cost, caps in candidates:
        if requirements.need_reasoning and not caps.reasoning:
            continue
        if requirements.need_vision and not caps.vision:
            continue
        if requirements.need_tools and not caps.tools:
            continue
        if requirements.privacy_sensitive and not caps.local:
            continue
        if requirements.max_cost_per_1k is not None and cost > requirements.max_cost_per_1k:
            continue
        score = 1.0 / (1.0 + cost)
        if requirements.privacy_sensitive and caps.local:
            score += 2.0
        if requirements.need_reasoning and caps.reasoning:
            score += 1.0
        if requirements.cheap_ok:
            score += 0.5 / (1.0 + cost)
        scored.append((score, model, provider, f"score={score:.3f} cost/1k={cost}"))
    if not scored:
        return None
    scored.sort(key=lambda t: t[0], reverse=True)
    _, model, provider, reason = scored[0]
    return RoutedModel(provider=provider, model=model, reason=reason)
