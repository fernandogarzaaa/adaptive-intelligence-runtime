"""MCP client: transport and protocol integration for external servers.

INVARIANT: agents never invoke MCP servers directly. Discovered MCP tools
are registered into AIR's ToolRegistry and executed through the Execution
Gateway like any other tool, with capability classes assigned by the
operator. MCP is a transport; the gateway remains the authority.

Supports the official MCP SDK transports: stdio (subprocess) and
streamable HTTP. Every call records provenance (server id, tool, schema
version, timestamps) and frames results as untrusted.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime
import json
import os
import sqlite3
import time
import uuid

from pydantic import BaseModel, Field

from air.events.fabric import utcnow
from air.security.policy import CapabilityClass, check_url, redact_secrets
from air.tools.registry import ToolDefinition


class MCPServerConfig(BaseModel):
    id: str = Field(default_factory=lambda: "mcp_" + uuid.uuid4().hex[:8])
    transport: str = "stdio"  # stdio | http
    # stdio:
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    cwd: str | None = None
    # http:
    url: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    timeout_s: float = 30.0
    # Operator-assigned capability class for every tool from this server
    # (refined per-tool via capability_overrides).
    default_capability: CapabilityClass = CapabilityClass.READ
    capability_overrides: dict[str, CapabilityClass] = Field(
        default_factory=dict)
    # If set, only these tools are exposed.
    allowed_tools: list[str] | None = None

    model_config = {"use_enum_values": True}


class MCPToolInfo(BaseModel):
    name: str
    description: str | None = None
    input_schema: dict = Field(default_factory=dict)
    server_id: str


class MCPError(Exception):
    pass


class MCPClientManager:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        # server_id -> {"close_event": Event, "closed": Event,
        #               "session": ClientSession}
        # A dedicated supervisor task enters AND exits every SDK context
        # (transport + session). AnyIO requires cancel scopes and task
        # groups to be exited in the same task that entered them, so the
        # whole connection lifecycle is bound to that one task; callers
        # may use the session and request disconnect from any task.
        self._sessions: dict[str, dict] = {}

    # ------------------------------------------------------------ lifecycle
    async def register(self, config: MCPServerConfig) -> str:
        """Persist the server registration. Secrets are never stored: env
        values in config are treated as overrides resolved from the process
        environment at connect time; only names are persisted."""
        if config.transport == "http":
            if not config.url:
                raise MCPError("http transport requires url")
            check_url(config.url, None)
        if config.transport == "stdio" and not config.command:
            raise MCPError("stdio transport requires command")
        redacted = config.model_dump()
        redacted["env"] = {k: "***" for k in config.env}
        redacted["headers"] = {k: "***" for k in config.headers}
        self._conn.execute(
            """INSERT INTO mcp_servers (id, transport, config, status,
               created_at) VALUES (?,?,?,'REGISTERED',?)
               ON CONFLICT(id) DO UPDATE SET transport=excluded.transport,
               config=excluded.config""",
            (config.id, config.transport, redact_secrets(json.dumps(redacted)),
             utcnow()))
        self._conn.commit()
        self._configs: dict[str, MCPServerConfig] = getattr(
            self, "_configs", {})
        self._configs[config.id] = config
        return config.id

    async def connect(self, server_id: str):
        """Open (or reuse) a session to the server.

        The session is owned by a dedicated supervisor task: all MCP SDK
        context managers are entered and exited inside that task, so
        disconnect() is safe to call from any task.
        """
        if server_id in self._sessions:
            return self._sessions[server_id]["session"]
        config = self._config(server_id)
        loop = asyncio.get_running_loop()
        ready: asyncio.Future = loop.create_future()
        close_event = asyncio.Event()
        closed = asyncio.Event()
        loop.create_task(
            self._supervise(server_id, config, ready, close_event, closed),
            name=f"mcp-supervisor-{server_id}")
        try:
            session = await ready
        except BaseException:
            # Setup failed, or the caller went away (cancelled): make
            # sure the supervisor tears down before propagating.
            close_event.set()
            await closed.wait()
            raise
        self._sessions[server_id] = {
            "close_event": close_event, "closed": closed,
            "session": session}
        self._conn.execute(
            "UPDATE mcp_servers SET status='CONNECTED', last_error=NULL"
            " WHERE id=?", (server_id,))
        self._conn.commit()
        return session

    async def _supervise(self, server_id: str, config: MCPServerConfig,
                         ready: asyncio.Future,
                         close_event: asyncio.Event,
                         closed: asyncio.Event) -> None:
        """Own the connection lifecycle in ONE task: enter the transport
        and session contexts here, hold them open until disconnect() is
        requested, then exit them here. Exiting in a different task
        raises RuntimeError from AnyIO's cancel scopes."""
        from mcp import ClientSession
        stack = contextlib.AsyncExitStack()
        try:
            if config.transport == "stdio":
                from mcp.client.stdio import (StdioServerParameters,
                                              stdio_client)
                # Env: config names resolve from the live process environment.
                env = {k: os.environ.get(k, v)
                       for k, v in config.env.items()}
                params = StdioServerParameters(
                    command=config.command, args=config.args, env=env or None,
                    cwd=config.cwd)
                read, write = await stack.enter_async_context(
                    stdio_client(params))
            else:
                from mcp.client.streamable_http import streamablehttp_client
                read, write, _ = await stack.enter_async_context(
                    streamablehttp_client(
                        config.url, headers=config.headers or None,
                        timeout=datetime.timedelta(
                            seconds=config.timeout_s)))
            session = await stack.enter_async_context(
                ClientSession(read, write))
            await asyncio.wait_for(session.initialize(),
                                   timeout=config.timeout_s)
        except Exception as e:  # noqa: BLE001 - setup can fail many ways
            if not ready.done():
                ready.set_exception(e)
            with contextlib.suppress(Exception):
                await stack.aclose()
            closed.set()
            return
        if not ready.done():
            ready.set_result(session)
        try:
            await close_event.wait()
        finally:
            # Same task that entered the contexts exits them.
            with contextlib.suppress(Exception):
                await stack.aclose()
            closed.set()

    async def disconnect(self, server_id: str) -> None:
        """Signal the supervisor task to tear the connection down and
        wait for it. Safe from any task, including after a server crash:
        teardown never touches the (possibly dead) server process
        directly beyond what the SDK's own context exit does."""
        handle = self._sessions.pop(server_id, None)
        if handle:
            handle["close_event"].set()
            await handle["closed"].wait()
        self._conn.execute(
            "UPDATE mcp_servers SET status='REGISTERED' WHERE id=?",
            (server_id,))
        self._conn.commit()

    # ------------------------------------------------------------ discovery
    async def discover(self, server_id: str) -> list[MCPToolInfo]:
        session = await self.connect(server_id)
        config = self._config(server_id)
        try:
            tools = await asyncio.wait_for(session.list_tools(),
                                           timeout=config.timeout_s)
        except asyncio.TimeoutError:
            raise MCPError(f"discovery timed out for {server_id}")
        out = []
        for t in tools.tools:
            if config.allowed_tools and t.name not in config.allowed_tools:
                continue
            out.append(MCPToolInfo(
                name=t.name, description=t.description,
                input_schema=dict(t.inputSchema or {"type": "object"}),
                server_id=server_id))
        return out

    # ----------------------------------------------------------------- call
    async def call(self, server_id: str, tool_name: str, args: dict,
                   timeout_s: float | None = None) -> dict:
        """Direct protocol call. Only the execution gateway should invoke
        this (via a registered tool handler); agents never call it."""
        session = await self.connect(server_id)
        config = self._config(server_id)
        timeout = timeout_s or config.timeout_s
        started = time.monotonic()
        try:
            result = await asyncio.wait_for(
                session.call_tool(
                    tool_name, args,
                    read_timeout_seconds=datetime.timedelta(seconds=timeout)),
                timeout=timeout + 5)
        except asyncio.TimeoutError:
            raise MCPError(f"MCP tool {tool_name} timed out after"
                           f" {timeout}s")
        except Exception as e:  # noqa: BLE001 - SDK errors vary
            raise MCPError(f"MCP tool {tool_name} failed:"
                           f" {type(e).__name__}: {e}")
        latency_ms = int((time.monotonic() - started) * 1000)
        content = []
        for block in result.content or []:
            b = block.model_dump() if hasattr(block, "model_dump") else block
            content.append(b)
        return {
            "ok": not result.isError,
            "content": content,
            "isError": bool(result.isError),
            "provenance": {
                "source": "mcp", "server_id": server_id,
                "tool": tool_name, "latency_ms": latency_ms,
                "untrusted": True, "at": utcnow(),
            },
        }

    # ------------------------------------------------- registry integration
    def tool_definitions(self, server_id: str,
                         tools: list[MCPToolInfo]) -> list[ToolDefinition]:
        """Build registry definitions for discovered tools. The operator's
        capability map decides the class; default is the server default."""
        config = self._config(server_id)
        defs = []
        for t in tools:
            cap = config.capability_overrides.get(t.name,
                                                  config.default_capability)
            # Poisoned metadata guard: descriptions are data, never
            # instructions; overlong/conflicting schemas are rejected.
            description = (t.description or "")[:2000]
            defs.append(ToolDefinition(
                name=f"mcp.{server_id}.{t.name}",
                description=f"[MCP:{server_id}] {description}",
                input_schema=t.input_schema,
                capability=cap,
                version="1.0.0",
                timeout_s=config.timeout_s,
                requires_approval=cap in (CapabilityClass.DESTRUCTIVE,
                                          CapabilityClass.PRIVILEGED)))
        return defs

    def handler_for(self, server_id: str, tool_name: str):
        async def _handler(args: dict, ctx) -> dict:
            # The gateway enforces the timeout and frames this result as
            # untrusted; the MCP provenance block is preserved inside.
            return await self.call(server_id, tool_name, args)
        return _handler

    # -------------------------------------------------------------- internal
    def _config(self, server_id: str) -> MCPServerConfig:
        configs = getattr(self, "_configs", {})
        if server_id in configs:
            return configs[server_id]
        row = self._conn.execute(
            "SELECT config FROM mcp_servers WHERE id=?",
            (server_id,)).fetchone()
        if not row:
            raise MCPError(f"unknown MCP server: {server_id}")
        data = json.loads(row[0])
        # Env/header VALUES were never stored; keep the names so connect()
        # can resolve them from the live process environment.
        data["env"] = {k: "" for k in (data.get("env") or {})}
        data["headers"] = {k: "" for k in (data.get("headers") or {})}
        cfg = MCPServerConfig.model_validate(data)
        configs[server_id] = cfg
        self._configs = configs
        return cfg
