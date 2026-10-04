"""Generic connectors: deliberately narrower than MCP.

A connector is one HTTP service with:
  - auth as REFERENCES (env names), never values
  - an allowlist of operations (method + path templates)
  - JSON schemas per operation
  - a token-bucket rate limit

Connectors expose their operations as gateway tools, exactly like MCP
servers do. The agent cannot tell (or care) which transport served it.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
import uuid

import httpx
from pydantic import BaseModel, Field

from air.events.fabric import utcnow
from air.security.policy import (CapabilityClass, PolicyDenied, check_url,
                                 redact_secrets)


class ConnectorOperation(BaseModel):
    name: str
    method: str = "GET"
    path: str  # may contain {param} templates
    input_schema: dict = Field(default_factory=dict)  # validated against payload
    capability: CapabilityClass = CapabilityClass.NETWORK

    model_config = {"use_enum_values": True}


class ConnectorConfig(BaseModel):
    id: str = Field(default_factory=lambda: "conn_" + uuid.uuid4().hex[:8])
    base_url: str
    auth: dict = Field(default_factory=dict)
    # {"type": "bearer_env", "env": "NAME"} |
    # {"type": "header", "header": "X-Key", "env": "NAME"} |
    # {"type": "none"}
    operations: list[ConnectorOperation] = Field(default_factory=list)
    rate_limit_per_min: int = 60
    timeout_s: float = 20.0


class RateLimited(Exception):
    pass


class ConnectorManager:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._configs: dict[str, ConnectorConfig] = {}
        self._buckets: dict[str, list[float]] = {}

    def register(self, config: ConnectorConfig) -> str:
        check_url(config.base_url, None)
        redacted = config.model_dump()
        redacted["auth"] = {k: ("***" if k == "env" else v)
                            for k, v in config.auth.items()}
        self._conn.execute(
            "INSERT INTO connectors (id, base_url, config, created_at)"
            " VALUES (?,?,?,?)"
            " ON CONFLICT(id) DO UPDATE SET base_url=excluded.base_url,"
            " config=excluded.config",
            (config.id, config.base_url,
             redact_secrets(json.dumps(redacted)), utcnow()))
        self._conn.commit()
        self._configs[config.id] = config
        return config.id

    def _config(self, connector_id: str) -> ConnectorConfig:
        if connector_id in self._configs:
            return self._configs[connector_id]
        row = self._conn.execute(
            "SELECT base_url, config FROM connectors WHERE id=?",
            (connector_id,)).fetchone()
        if not row:
            raise KeyError(f"unknown connector: {connector_id}")
        data = json.loads(row[1])
        data["base_url"] = row[0]
        cfg = ConnectorConfig.model_validate(data)
        self._configs[connector_id] = cfg
        return cfg

    def _rate_check(self, connector_id: str, per_min: int) -> None:
        now = time.monotonic()
        window = self._buckets.setdefault(connector_id, [])
        cutoff = now - 60.0
        while window and window[0] < cutoff:
            window.pop(0)
        if len(window) >= per_min:
            raise RateLimited(
                f"connector {connector_id}: {per_min}/min exceeded")
        window.append(now)

    def _auth_headers(self, config: ConnectorConfig) -> dict:
        auth = config.auth or {"type": "none"}
        atype = auth.get("type", "none")
        if atype == "none":
            return {}
        env_name = auth.get("env")
        value = os.environ.get(env_name, "") if env_name else ""
        if not value:
            raise PolicyDenied(
                f"connector {config.id}: credential env {env_name!r}"
                " is not set")
        if atype == "bearer_env":
            return {"Authorization": f"Bearer {value}"}
        if atype == "header":
            return {auth.get("header", "X-API-Key"): value}
        raise PolicyDenied(f"unknown auth type: {atype}")

    async def request(self, connector_id: str, operation: str,
                      params: dict) -> dict:
        """Execute one connector operation. Only the gateway calls this."""
        config = self._config(connector_id)
        op = next((o for o in config.operations if o.name == operation),
                  None)
        if op is None:
            raise PolicyDenied(
                f"operation {operation!r} not allowlisted on"
                f" connector {connector_id}")
        import jsonschema
        jsonschema.validate(params or {}, op.input_schema)
        self._rate_check(connector_id, config.rate_limit_per_min)
        path = op.path
        for key, val in (params or {}).items():
            path = path.replace("{" + key + "}",
                                re.sub(r"[^A-Za-z0-9_.-]", "", str(val)))
        if "{" in path:
            raise PolicyDenied(f"unfilled path template: {path}")
        url = config.base_url.rstrip("/") + path
        check_url(url, None)
        headers = {"User-Agent": "AIR/0.1.0"}
        headers.update(self._auth_headers(config))
        started = time.monotonic()
        async with httpx.AsyncClient(trust_env=False,
                                     timeout=config.timeout_s) as client:
            resp = await client.request(
                op.method, url, headers=headers,
                json=params.get("_body") if op.method != "GET" else None)
        body = resp.text[:20000]
        return {
            "ok": resp.status_code < 400, "status": resp.status_code,
            "body": redact_secrets(body),
            "provenance": {
                "source": "connector", "connector_id": connector_id,
                "operation": operation,
                "latency_ms": int((time.monotonic() - started) * 1000),
                "untrusted": True, "at": utcnow(),
            },
        }

    def tool_definitions(self, connector_id: str):
        """Registry definitions, one per allowlisted operation."""
        from air.tools.registry import ToolDefinition
        config = self._config(connector_id)
        defs = []
        for op in config.operations:
            defs.append(ToolDefinition(
                name=f"conn.{connector_id}.{op.name}",
                description=f"[connector:{connector_id}] {op.method}"
                            f" {op.path}",
                input_schema=op.input_schema, capability=op.capability,
                timeout_s=config.timeout_s))
        return defs

    def handler_for(self, connector_id: str, operation: str):
        async def _handler(args: dict, ctx) -> dict:
            return await self.request(connector_id, operation, args)
        return _handler
