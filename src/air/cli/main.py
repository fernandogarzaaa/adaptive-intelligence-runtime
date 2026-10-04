"""AIR command line interface (stdlib argparse; no extra dependencies).

The CLI and UI share the same backend contracts.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from air import __version__
from air.config import AirConfig
from air.persistence.db import find_migrations_dir


def _migrations_dir() -> Path:
    return find_migrations_dir()


def cmd_doctor(_args: argparse.Namespace) -> int:
    """Check the runtime environment: Python, database, providers, filesystem."""
    import os

    config = AirConfig.from_env()
    checks: list[tuple[str, bool, str]] = []

    checks.append(("python", sys.version_info >= (3, 12),
                   f"{sys.version.split()[0]} (need >=3.12)"))

    try:
        config.ensure_dirs()
        writable = config.data_dir.is_dir() and os.access(config.data_dir, os.W_OK)
        checks.append(("data_dir", writable, str(config.data_dir)))
    except Exception as e:  # noqa: BLE001
        checks.append(("data_dir", False, str(e)))

    try:
        from air.persistence.db import Database
        db = Database(config.db_path)
        applied = db.migrate(_migrations_dir())
        db.conn.execute("SELECT 1").fetchone()
        checks.append(("database", True,
                       f"{config.db_path} (migrations applied this run: {len(applied)})"))
        db.close()
    except Exception as e:  # noqa: BLE001
        checks.append(("database", False, f"{type(e).__name__}: {e}"))

    from air.providers.registry import ProviderRegistry
    registry = ProviderRegistry(config)
    if registry.names():
        checks.append(("providers", True,
                       "configured: " + ", ".join(registry.names())
                       + (f"; unavailable: {registry.unavailable}"
                          if registry.unavailable else "")))
    else:
        checks.append(("providers", True,
                       "none with credentials; local-first scripted mode only"
                       + (f"; unavailable: {registry.unavailable}"
                          if registry.unavailable else "")))

    async def _health() -> None:
        for name in registry.names():
            p = registry.get(name)
            if p is None:
                continue
            try:
                h = await p.health()
                checks.append((f"provider:{name}", h.ok, h.reason or "ok"))
            except Exception as e:  # noqa: BLE001
                checks.append((f"provider:{name}", False, f"{type(e).__name__}: {e}"))

    asyncio.run(_health())

    web_dir = Path(__file__).resolve().parents[3] / "web"
    checks.append(("frontend", (web_dir / "package.json").exists(),
                   str(web_dir) if (web_dir / "package.json").exists()
                   else "web/ not yet built"))

    print(f"AIR {__version__} doctor")
    failed = 0
    for name, ok, detail in checks:
        mark = "ok  " if ok else "FAIL"
        if not ok:
            failed += 1
        print(f"  [{mark}] {name}: {detail}")
    if failed:
        print(f"{failed} check(s) failed")
        return 1
    print("all checks passed")
    return 0


def _api_base() -> str:
    config = AirConfig.from_env()
    return f"http://{config.host}:{config.port}"


def cmd_start(args: argparse.Namespace) -> int:
    """Start the AIR backend (API + websocket)."""
    import uvicorn
    from air.api.app import create_app

    config = AirConfig.from_env()
    uvicorn.run(create_app(), host=args.host or config.host,
                port=args.port or config.port)
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    """Submit a goal to the running backend."""
    import urllib.request

    body = json.dumps({
        "goal": args.goal,
        "strategy": args.strategy,
        "agent_budget": args.agent_budget,
    }).encode()
    req = urllib.request.Request(f"{_api_base()}/runs", data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            print(json.dumps(json.loads(resp.read()), indent=2))
    except Exception as e:  # noqa: BLE001
        print(f"error: {e} (is the backend running? `air start`)",
              file=sys.stderr)
        return 1
    return 0


def _get(path: str, params: str = "") -> int:
    import urllib.request

    try:
        with urllib.request.urlopen(f"{_api_base()}{path}{params}",
                                    timeout=30) as resp:
            data = json.loads(resp.read())
    except Exception as e:  # noqa: BLE001
        print(f"error: {e} (is the backend running? `air start`)",
              file=sys.stderr)
        return 1
    return data


def cmd_runs(args: argparse.Namespace) -> int:
    data = _get("/runs", f"?limit={args.limit}")
    if isinstance(data, int):
        return data
    for r in data:
        print(f"{r['id']}  {r['status']:12}  {r['goal'][:80]}")
    return 0


def cmd_agents(args: argparse.Namespace) -> int:
    params = f"?run_id={args.run_id}" if args.run_id else ""
    data = _get("/agents", params)
    if isinstance(data, int):
        return data
    for a in data:
        print(f"{a['id']}  {a['role']:14}  {a['status']:10}  {a['objective'][:70]}")
    return 0


def cmd_capabilities(_args: argparse.Namespace) -> int:
    data = _get("/capabilities")
    if isinstance(data, int):
        return data
    for c in data:
        print(f"{c['capability_id']}  {c['name']:24}  {c['validation_status']}")
    return 0


def cmd_config(_args: argparse.Namespace) -> int:
    config = AirConfig.from_env()
    print(config.model_dump_json(indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="air",
                                description="Adaptive Intelligence Runtime")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("doctor", help="check the runtime environment")
    sp.set_defaults(fn=cmd_doctor)

    sp = sub.add_parser("start", help="start the backend API")
    sp.add_argument("--host", default=None)
    sp.add_argument("--port", type=int, default=None)
    sp.set_defaults(fn=cmd_start)

    sp = sub.add_parser("run", help="submit a goal")
    sp.add_argument("goal")
    sp.add_argument("--strategy", default=None)
    sp.add_argument("--agent-budget", type=int, default=4)
    sp.set_defaults(fn=cmd_run)

    sp = sub.add_parser("runs", help="list recent runs")
    sp.add_argument("--limit", type=int, default=10)
    sp.set_defaults(fn=cmd_runs)

    sp = sub.add_parser("agents", help="list agents")
    sp.add_argument("run_id", nargs="?")
    sp.set_defaults(fn=cmd_agents)

    sp = sub.add_parser("capabilities", help="list capabilities")
    sp.set_defaults(fn=cmd_capabilities)

    sp = sub.add_parser("config", help="show effective configuration")
    sp.set_defaults(fn=cmd_config)
    return p


def app(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.fn(args)


def main() -> None:
    sys.exit(app())


if __name__ == "__main__":
    main()
