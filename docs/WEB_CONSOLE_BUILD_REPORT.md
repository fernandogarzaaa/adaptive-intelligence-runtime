# AIR Web Console Build Report

**Date:** 2026-10-07
**Task:** Build the React web console in `web/`, verify it, document how to serve it.

## Verdict: BUILD SUCCEEDS, SERVES CORRECTLY

The console was never "unbuilt" in the broken sense — a `dist/` from Oct 4 already existed,
but a fresh `npm run build` works cleanly on first attempt with no fixes needed.

## Build Evidence

```
$ cd web && npm run build
> air-console@0.1.0 build
> tsc --noEmit && vite build

vite v6.4.3 building for production...
transforming...
✓ 49 modules transformed.
rendering chunks...
computing gzip size...
dist/index.html                   0.40 kB │ gzip:  0.27 kB
dist/assets/index-Dy7BghBb.css    8.11 kB │ gzip:  2.31 kB
dist/assets/index-CdMBfpuL.js   286.15 kB │ gzip: 81.99 kB
✓ built in 18.78s
```

- `tsc --noEmit` passed: **zero TypeScript errors**
- 49 modules transformed, no warnings
- Node v24.20.0, npm 10.9.4 (environment toolchain)

## Source Inventory

- `web/src/App.tsx` + `web/src/main.tsx` — entry
- 11 screens: `Live, RunDetail, Approvals, Authority, Capabilities, Experience, Memory, Metrics, Policies, PolicyDetail, Runs`
- Components: `ui.tsx`, `CognitiveGraph.tsx`, `EvidenceChain.tsx`, `blocked.tsx`
- Lib: `api.ts` (REST, same-origin via `VITE_API_BASE ?? ''`), `ws.ts` (single WebSocket to `/ws/events` with cursor replay), `query.ts`, `types.ts`
- Design contract (from package.json): *"Consumes only the backend REST + WebSocket contracts; never simulates runtime state."*
- Dependencies: React 18.3, react-router-dom 6.28, Vite 6.0.3, TypeScript 5.6.3 — all pinned in `package-lock.json`

## Live Serve Verification (2026-10-07)

Started backend via `air start --port 18766` (venv `~/.venvs/air-runtime`), then:

| Check | Result |
|-------|--------|
| `GET /` | **200**, returns `AIR Console` HTML |
| `GET /assets/index-CdMBfpuL.js` | **200**, bundle serves |
| `GET /runs` (client SPA route) | **200**, SPA fallback serves index.html |
| `GET /runs?limit=1` (API) | **200**, returns real run data |
| Console API base | same-origin (`VITE_API_BASE` unset → `''`) |
| WebSocket | `ws.ts` connects to `/ws/events` with `last_event_id` cursor replay |

Backend serving code: `src/air/api/app.py` lines 699-751 — mounts `/assets`, SPA fallback for all non-API routes, looks for `web/dist` in repo layout or `<sys.prefix>/web/dist` in installed layout.

## Acceptance Tests

`web/tests/acceptance/test_console_acceptance.py` (Playwright, full operator scenario against live backend).
Playwright is not installed in this sandbox, so tests were not re-run here.
Last recorded run (from `web/tests/acceptance/last_run_output.txt`, Oct 4): **1 passed in 33.67s, "ACCEPTANCE COMPLETE"**.

## How to Serve the Console

**Development** (hot reload, proxies to backend):
```bash
cd web
npm install
VITE_API_BASE=http://127.0.0.1:8765 npm run dev   # console at http://localhost:5173
```

**Production** (same origin as backend):
```bash
cd web && npm run build     # produces web/dist
air start                   # serves web/dist at / (SPA fallback included)
# Open http://127.0.0.1:8765
```

## Known Gap: Console Not in PyPI Wheel

`pip install adaptive-intelligence-runtime` does **not** ship the console:
- `unzip -l dist/*.whl` → 0 matches for `web/dist`
- sdist tarball → 0 matches for `web/dist/index.html`

The serving code (`app.py`) already handles this: it checks `<sys.prefix>/web/dist` for the "deployed alongside" layout. Per the standing packaging decision (AGENTS.md: "The console is packaged/deployed alongside it, never bundled into the wheel in a way that distorts the runtime architecture"), this is intentional, not a bug. But it means **PyPI users currently get no console** — `air doctor` reports `frontend: web/ not yet built`, and `/` serves whatever the API fallback is without `web/dist`.

## Handoff Note

- **Goal:** Build the AIR React web console in `web/`, verify it, document serving.
- **Key decisions:**
  - No source changes were needed; the build works as-is. Did not touch any code.
  - Verified live serving through the real backend (`air start`), not just `vite preview`.
  - Did not attempt Playwright acceptance re-run (playwright not installed in sandbox).
  - Did not touch sealed experiment files.
- **Evidence pointers:**
  - Build output: `web/dist/` (fresh 2026-10-07 build)
  - Serve verification: `src/air/api/app.py:699-751`
  - Live test: `curl` checks against `air start --port 18766` (200s across `/`, `/assets/*`, SPA fallback, API)
  - Wheel gap: `unzip -l dist/adaptive_intelligence_runtime-0.2.0-py3-none-any.whl | grep index.html` → 0
  - Prior acceptance: `web/tests/acceptance/last_run_output.txt` (PASSED, 33.67s)
- **Open items:**
  - **Needs Inan's input:** How should PyPI/Docker users get the console? Options: (a) CI builds `web/dist` and a post-install step copies it to `<sys.prefix>/web/dist`; (b) Docker image builds console in image; (c) separate `air-console` npm/distribution artifact; (d) leave as repo-checkout-only. The wheel-serving code path (`<sys.prefix>/web/dist`) exists but nothing populates it.
  - Re-run Playwright acceptance tests in an environment with playwright installed (could not verify here).
