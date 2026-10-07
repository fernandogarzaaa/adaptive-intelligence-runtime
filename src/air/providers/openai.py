"""OpenAI provider (first-party API; OpenAI-compatible chat completions)."""

from __future__ import annotations

import os

from air.providers.openai_compat import OpenAICompatProvider


class OpenAIProvider(OpenAICompatProvider):
    """Dedicated OpenAI provider with first-party defaults.

    Inherits the OpenAI-compatible chat-completions implementation;
    exists as its own kind so config, registry, and /models can name
    OpenAI explicitly rather than lumping it under openai_compat.
    """

    name = "openai"
    kind = "openai"

    def __init__(self, model: str = "gpt-4o-mini",
                 api_key: str | None = None,
                 base_url: str = "https://api.openai.com/v1",
                 name: str = "openai", **kw) -> None:
        super().__init__(base_url=base_url, model=model, api_key=api_key,
                         name=name, **kw)

    @classmethod
    def from_config(cls, name: str, base_url: str | None, model: str,
                    api_key_env: str | None, **kw) -> "OpenAIProvider":
        key = os.environ.get(api_key_env) if api_key_env else None
        return cls(base_url=base_url or "https://api.openai.com/v1",
                   model=model or "gpt-4o-mini", api_key=key, name=name, **kw)
