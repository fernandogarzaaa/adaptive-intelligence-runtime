"""Runtime configuration.

Configuration is explicit and discoverable. Secrets are never stored: provider
credentials live in environment variables and only the *names* of those
variables are referenced in configuration and the database.
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, Field


def default_data_dir() -> Path:
    return Path(os.environ.get("AIR_DATA_DIR", str(Path.home() / ".air")))


class ProviderConfig(BaseModel):
    name: str
    kind: str  # openai | openai_compat | anthropic | gemini | ollama
    base_url: str | None = None
    model: str | None = None
    api_key_env: str | None = None
    enabled: bool = True


class AirConfig(BaseModel):
    data_dir: Path = Field(default_factory=default_data_dir)
    host: str = "127.0.0.1"
    port: int = 8765
    database_url: str | None = None
    providers: list[ProviderConfig] = Field(default_factory=list)
    log_level: str = "INFO"
    max_concurrent_agents: int = 16
    default_token_budget: int = 64_000
    default_cost_budget_usd: float = 5.0
    default_time_budget_s: int = 1800
    default_agent_budget: int = 8

    @property
    def db_path(self) -> Path:
        if self.database_url and self.database_url.startswith("sqlite:///"):
            return Path(self.database_url.removeprefix("sqlite:///"))
        return self.data_dir / "air.db"

    @classmethod
    def from_env(cls) -> "AirConfig":
        providers: list[ProviderConfig] = []
        if os.environ.get("OPENAI_API_KEY"):
            providers.append(
                ProviderConfig(
                    name="openai",
                    kind="openai",
                    base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
                    model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
                    api_key_env="OPENAI_API_KEY",
                )
            )
        if os.environ.get("ANTHROPIC_API_KEY"):
            providers.append(
                ProviderConfig(
                    name="anthropic",
                    kind="anthropic",
                    base_url="https://api.anthropic.com",
                    model=os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5"),
                    api_key_env="ANTHROPIC_API_KEY",
                )
            )
        if os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
            providers.append(
                ProviderConfig(
                    name="gemini",
                    kind="gemini",
                    base_url="https://generativelanguage.googleapis.com",
                    model=os.environ.get("GEMINI_MODEL", "gemini-2.0-flash"),
                    api_key_env="GEMINI_API_KEY",
                )
            )
        ollama_url = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
        providers.append(
            ProviderConfig(
                name="ollama",
                kind="ollama",
                base_url=ollama_url,
                model=os.environ.get("OLLAMA_MODEL", "llama3.1"),
                api_key_env=None,
            )
        )
        return cls(
            data_dir=default_data_dir(),
            host=os.environ.get("AIR_HOST", "127.0.0.1"),
            port=int(os.environ.get("AIR_PORT", "8765")),
            database_url=os.environ.get("AIR_DATABASE_URL"),
            providers=providers,
            log_level=os.environ.get("AIR_LOG_LEVEL", "INFO"),
        )

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "logs").mkdir(parents=True, exist_ok=True)
        (self.data_dir / "artifacts").mkdir(parents=True, exist_ok=True)
