"""MCP teardown regression tests.

AnyIO requires cancel scopes and task groups to be exited in the same
task that entered them. MCPClientManager binds the whole connection
lifecycle (transport + session contexts) to a dedicated supervisor task,
so disconnect() is safe to call from any task and survives a dead
server. These tests fail if teardown ever leaks the
"Attempted to exit cancel scope in a different task than it was entered
in" RuntimeError again.
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys
import time

from air.mcp.client import MCPClientManager, MCPServerConfig
from air.persistence.db import Database, find_migrations_dir

SERVER_SRC = """\
from mcp.server.fastmcp import FastMCP
mcp = FastMCP("teardown-probe")

@mcp.tool()
def ping() -> str:
    return "pong"

if __name__ == "__main__":
    mcp.run()
"""


def _write_server(tmp_path) -> str:
    # Unique filename per test dir so the child scan below can only
    # match this test's server process, never anything else.
    script = tmp_path / f"mcp_teardown_{tmp_path.name.replace('-', '_')}.py"
    script.write_text(SERVER_SRC)
    return str(script)


def _child_server_pids(marker: str) -> list[int]:
    """PIDs of DIRECT children of this process whose cmdline contains
    the marker. Never matches the parent shell (its own cmdline may
    contain the marker via a heredoc)."""
    me = os.getpid()
    out = []
    for pid in os.listdir("/proc"):
        if not pid.isdigit() or int(pid) == me:
            continue
        try:
            with open(f"/proc/{pid}/stat") as f:
                ppid = int(f.read().split()[3])
            if ppid != me:
                continue
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                cmd = f.read().replace(b"\0", b" ").decode(
                    "utf-8", "replace")
        except (FileNotFoundError, ProcessLookupError, IndexError,
                ValueError):
            continue
        if marker in cmd:
            out.append(int(pid))
    return out


def _db(tmp_path):
    db = Database(tmp_path / "air.db")
    db.migrate(find_migrations_dir())
    return db


def test_disconnect_from_different_task_is_clean(tmp_path):
    """Connect in task A, disconnect in task B: no RuntimeError, DB
    status returns to REGISTERED, and reconnect works."""
    db = _db(tmp_path)
    script = _write_server(tmp_path)

    async def main():
        mgr = MCPClientManager(db.conn)
        await mgr.register(MCPServerConfig(
            id="mcp_teardown", transport="stdio",
            command=sys.executable, args=[script], timeout_s=20.0))
        session = await asyncio.create_task(
            mgr.connect("mcp_teardown"), name="connector")
        assert session is not None
        # The session is usable from yet another task.
        res = await asyncio.create_task(
            mgr.call("mcp_teardown", "ping", {}), name="caller")
        assert res["content"][0]["text"] == "pong"
        # Teardown from a task that never touched the connection.
        await asyncio.create_task(
            mgr.disconnect("mcp_teardown"), name="disconnector")
        status = db.conn.execute(
            "SELECT status FROM mcp_servers WHERE id='mcp_teardown'"
        ).fetchone()[0]
        assert status == "REGISTERED"
        # Reconnect after a clean teardown works.
        await mgr.connect("mcp_teardown")
        await mgr.disconnect("mcp_teardown")

    asyncio.run(main())


def test_disconnect_after_server_crash_is_clean(tmp_path):
    """SIGKILL the server mid-session, then disconnect from another
    task: teardown must not raise, hang, or leak the RuntimeError."""
    db = _db(tmp_path)
    script = _write_server(tmp_path)

    async def main():
        mgr = MCPClientManager(db.conn)
        await mgr.register(MCPServerConfig(
            id="mcp_crash", transport="stdio",
            command=sys.executable, args=[script], timeout_s=20.0))
        await asyncio.create_task(mgr.connect("mcp_crash"))
        pids = _child_server_pids(os.path.basename(script))
        assert pids, "server subprocess not found"
        for pid in pids:
            os.kill(pid, signal.SIGKILL)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if not _child_server_pids(os.path.basename(script)):
                break
            await asyncio.sleep(0.2)
        assert not _child_server_pids(os.path.basename(script))
        # A call against the dead server fails as MCPError, not a hang.
        try:
            await asyncio.wait_for(
                mgr.call("mcp_crash", "ping", {}), timeout=20)
            raise AssertionError("call against dead server should fail")
        except Exception as e:  # noqa: BLE001
            assert type(e).__name__ != "RuntimeError", e
        # Teardown is still clean.
        await asyncio.create_task(mgr.disconnect("mcp_crash"))
        status = db.conn.execute(
            "SELECT status FROM mcp_servers WHERE id='mcp_crash'"
        ).fetchone()[0]
        assert status == "REGISTERED"
        # And the manager recovers: a fresh server can connect again.
        await mgr.connect("mcp_crash")
        await mgr.disconnect("mcp_crash")

    asyncio.run(main())


def test_failed_connect_leaves_no_session(tmp_path):
    """A connect that fails during setup propagates the error and
    leaves nothing behind: no session entry, disconnect is a no-op."""
    db = _db(tmp_path)

    async def main():
        mgr = MCPClientManager(db.conn)
        await mgr.register(MCPServerConfig(
            id="mcp_bad", transport="stdio",
            command="/nonexistent/mcp-server-binary-xyz",
            args=[], timeout_s=5.0))
        try:
            await asyncio.wait_for(mgr.connect("mcp_bad"), timeout=30)
            raise AssertionError("connect to bad binary should fail")
        except Exception as e:  # noqa: BLE001
            assert type(e).__name__ != "RuntimeError", e
        assert "mcp_bad" not in mgr._sessions
        await mgr.disconnect("mcp_bad")  # no-op, must not raise

    asyncio.run(main())
