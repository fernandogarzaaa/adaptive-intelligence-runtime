"""Built-in tools. Each is argv/paths-first: no shell=True anywhere, no raw
sockets, and every external byte is framed as untrusted on the way out."""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

from air.security.policy import (CapabilityClass, assert_safe_path,
                                 check_url, redact_secrets, safe_argv)
from air.tools.registry import ToolContext, ToolDefinition, ToolRegistry

MAX_READ_BYTES = 1_000_000
MAX_FETCH_BYTES = 5_000_000


async def _fs_read(args: dict, ctx: ToolContext) -> dict:
    root = Path(ctx.workspace_root)
    path = assert_safe_path(args["path"], root)
    if not path.is_file():
        return {"ok": False, "error": f"not a file: {args['path']}"}
    data = path.read_bytes()[: int(args.get("max_bytes", MAX_READ_BYTES))]
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return {"ok": False, "error": "not UTF-8 text"}
    return {"ok": True, "path": str(path.relative_to(root.resolve())),
            "content": redact_secrets(text)}


async def _fs_write(args: dict, ctx: ToolContext) -> dict:
    root = Path(ctx.workspace_root)
    path = assert_safe_path(args["path"], root)
    path.parent.mkdir(parents=True, exist_ok=True)
    content = redact_secrets(args["content"])
    path.write_text(content, encoding="utf-8")
    return {"ok": True, "path": str(path.relative_to(root.resolve())),
            "bytes": len(content.encode("utf-8"))}


async def _shell_exec(args: dict, ctx: ToolContext) -> dict:
    argv = safe_argv(list(args["argv"]))
    if not argv:
        return {"ok": False, "error": "empty argv"}
    root = Path(ctx.workspace_root)
    cwd = assert_safe_path(args.get("cwd", "."), root)
    timeout = min(float(args.get("timeout_s", 30)), 300)
    proc = await asyncio.create_subprocess_exec(
        *argv, cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return {"ok": False, "error": f"timeout after {timeout}s",
                "argv": argv}
    return {"ok": proc.returncode == 0, "returncode": proc.returncode,
            "argv": argv,
            "stdout": redact_secrets(out.decode("utf-8", "replace")[-20000:]),
            "stderr": redact_secrets(err.decode("utf-8", "replace")[-20000:])}


async def _http_fetch(args: dict, ctx: ToolContext) -> dict:
    url = args["url"]
    check_url(url, None)  # SSRF: never private/loopback unless allowlisted
    method = args.get("method", "GET").upper()
    if method not in ("GET", "HEAD"):
        return {"ok": False, "error": f"method not allowed: {method}"}
    timeout = min(float(args.get("timeout_s", 20)), 120)
    async with httpx.AsyncClient(trust_env=False,
                                 timeout=timeout) as client:
        resp = await client.request(method, url,
                                    headers={"User-Agent": "AIR/0.1.0"})
    body = resp.content[:MAX_FETCH_BYTES]
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        text = f"<{len(body)} non-UTF8 bytes>"
    return {"ok": resp.status_code < 400, "status": resp.status_code,
            "url": url, "content_type": resp.headers.get("content-type", ""),
            "body": redact_secrets(text)}


def build_default_registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(ToolDefinition(
        name="fs.read", description="Read a UTF-8 text file under the workspace.",
        input_schema={"type": "object",
                      "properties": {"path": {"type": "string"},
                                     "max_bytes": {"type": "integer"}},
                      "required": ["path"]},
        capability=CapabilityClass.READ), _fs_read)
    reg.register(ToolDefinition(
        name="fs.write", description="Write a UTF-8 text file under the workspace.",
        input_schema={"type": "object",
                      "properties": {"path": {"type": "string"},
                                     "content": {"type": "string"}},
                      "required": ["path", "content"]},
        capability=CapabilityClass.WRITE), _fs_write)
    reg.register(ToolDefinition(
        name="shell.exec", description="Execute a command as an argv list (no shell).",
        input_schema={"type": "object",
                      "properties": {"argv": {"type": "array",
                                              "items": {"type": "string"}},
                                     "cwd": {"type": "string"},
                                     "timeout_s": {"type": "number"}},
                      "required": ["argv"]},
        capability=CapabilityClass.EXECUTE), _shell_exec)
    reg.register(ToolDefinition(
        name="http.fetch", description="Fetch a URL (GET/HEAD, allowlisted hosts).",
        input_schema={"type": "object",
                      "properties": {"url": {"type": "string"},
                                     "method": {"type": "string"},
                                     "timeout_s": {"type": "number"}},
                      "required": ["url"]},
        capability=CapabilityClass.NETWORK), _http_fetch)
    return reg
