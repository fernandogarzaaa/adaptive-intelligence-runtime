# Adaptive Intelligence Runtime (AIR)

An intelligence runtime that dynamically allocates cognitive processes: given a
goal, it decides **what cognitive organization should exist** to solve it —
direct reasoning, delegation, sibling-agent spawning, critics, verifiers —
learns from experience, and promotes only independently validated capabilities.

> The runtime may change how it thinks, but it must never manufacture the
> evidence that proves the change was beneficial.

## Status

v0.1.0 — initial build in progress. See `docs/` for architecture.

## Install

```bash
git clone <repo>
cd adaptive-intelligence-runtime
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/air doctor
.venv/bin/air start
```

With Docker:

```bash
docker build -f docker/Dockerfile -t air .
docker run -p 8765:8765 air
```

## Use

```bash
air run "Audit this repository for authentication vulnerabilities"
air runs
air agents
```

Open the console at `http://127.0.0.1:8765` (frontend build lands in `web/`).

## Local-first honesty

Without a configured model provider the runtime still boots: UI, memory, run
history, configuration, evaluation infrastructure, and scripted/deterministic
agents all work. Model-dependent agent execution honestly reports
`MODEL_PROVIDER_UNAVAILABLE` instead of faking output.

## Layout

- `src/air/` — backend (FastAPI, SQLite/WAL, asyncio)
- `web/` — React + TypeScript console
- `migrations/` — SQL migrations
- `tests/` — unit, integration, adversarial
- `docs/` — architecture, security, API, operations
