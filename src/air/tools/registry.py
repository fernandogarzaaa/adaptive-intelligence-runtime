"""Tool registry: named tools with JSON schemas and capability classes.

Tools are the only way agents touch the outside world. Every tool declares
the capability class it needs; the execution gateway enforces it.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from pydantic import BaseModel

from air.security.policy import CapabilityClass


class ToolContext(BaseModel):
    """What a tool handler may know about its invocation."""
    run_id: str
    agent_id: str
    workspace_root: str
    server_id: str | None = None


class ToolDefinition(BaseModel):
    name: str
    description: str
    input_schema: dict
    capability: CapabilityClass
    version: str = "1.0.0"
    timeout_s: float = 30.0
    # DESTRUCTIVE/PRIVILEGED tools always require approval; this flag adds
    # approval for lower classes too.
    requires_approval: bool = False

    model_config = {"arbitrary_types_allowed": True}


Handler = Callable[[dict, ToolContext], Awaitable[dict]]


@dataclass
class _Entry:
    definition: ToolDefinition
    handler: Handler


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, _Entry] = {}

    def register(self, definition: ToolDefinition, handler: Handler) -> None:
        if definition.name in self._tools:
            raise ValueError(f"tool already registered: {definition.name}")
        self._tools[definition.name] = _Entry(definition, handler)

    def get(self, name: str) -> _Entry | None:
        return self._tools.get(name)

    def list(self) -> list[ToolDefinition]:
        return [e.definition for e in self._tools.values()]

    def validate_args(self, name: str, args: dict) -> None:
        import jsonschema
        entry = self.get(name)
        if entry is None:
            raise KeyError(f"unknown tool: {name}")
        jsonschema.validate(args or {}, entry.definition.input_schema)
