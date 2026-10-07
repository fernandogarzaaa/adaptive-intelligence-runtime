# AIR v0.2.0 Dogfood Report

**Date:** 2026-10-07
**Tester:** subagent (fresh-user simulation)
**Method:** Clean venv, `pip install adaptive-intelligence-runtime==0.2.0` from PyPI, exercised CLI + Python API as a new user would. No repo source used; everything below is the shipped package.

## What worked well

1. **Install is clean.** `pip install adaptive-intelligence-runtime` in a fresh venv pulled 0.2.0 with all deps, no errors, ~50s.
2. **`import air` is fast and pleasant.** 49ms import (lazy loading works). `allocate_strategy`, `allocation_explanation`, `allocation_scores`, `frozen_policy`, `allocate` all work as documented.
3. **`air init` onboarding is smooth.** Clear menu, sensible defaults, `-y` flag works, sanity check runs, next-steps printed. Good first-run experience.
4. **`air doctor` is genuinely useful.** `[ok]`/`[FAIL]` per check, actionable. Correctly flagged ollama unreachable and frontend unbuilt.
5. **`air allocate` output is readable.** v1 scores, pv2 adjustments, both choices, rules fired. The explanation format is the best part of the product.
6. **Backend-down errors are helpful.** `air runs`, `air capabilities` etc. print `error: ... (is the backend running? \`air start\`)`. Good.
7. **Secrets redacted.** `air config` shows `"database_url": <redacted>`. Nice touch.
8. **Backend API works.** `air start`, `/health`, `POST /runs`, `GET /runs/{id}` all respond correctly.

## Bugs (broken in the shipped package)

### B1. `air run` crashes with a raw traceback (P0)
`cmd_run` reads `run.get("id")` but `POST /runs` returns `{"run_id": "..."}`. The CLI then polls `GET /runs/?`, which returns a *list*, and crashes on `status_data.get(...)`:

```
Run ? submitted. Watching...
AttributeError: 'list' object has no attribute 'get'
```

A new user running the flagship command gets a Python traceback. Reproduced on the PyPI-installed 0.2.0.

### B2. `air init` "skip" choice doesn't persist (P0)
Choosing `3) skip` prints `[skip] execution disabled` but never writes `provider.env`. The file keeps whatever a previous run wrote (`AIR_PROVIDER=ollama`). The user's explicit choice is silently discarded.

### B3. `air run` without backend prints error before the plan (P1)
The allocation plan prints to stdout (buffered) while the connection error goes to stderr (unbuffered), so the user sees the error *first*, then the plan for a run that will never execute. Confusing ordering; the plan should not print when submission failed.

### B4. `air allocate` on empty goal silently returns single_agent (P2)
No input validation. `air.allocate_strategy("")` → `single_agent`, no warning. Non-string input raises raw `AttributeError: 'int' object has no attribute 'lower'`.

## Product gaps (works, but the value prop doesn't land)

### G1. Capability sensitivity is unreachable through the public API (P0)
This is the biggest gap. The flagship validated claim (CTC-1: pv2 responds to organizational *capability* structure, not lexical cues) cannot engage for real users because:

- `air/_lib.py` builds the task dict as `{"effects": [], "operations": [], "artifacts": []}` — always empty.
- `extract_features()` on plain text is extremely coarse: "Write the results to a file" produces **no WRITE effect**; only `uncertainty: 0.5, complexity: 0.5`.
- The capability annotations CTC-1 validated come from hand-written experiment JSON, not from text analysis.

A user typing natural goals never exercises the validated behavior. The product demo ("it understands what your task *needs*") is hollow through the shipped interface.

### G2. pv2 is extremely conservative (P1)
8 diverse goals tested (coding, research, writing, planning, debugging, data, docs, multi-step build): **7/8 → single_agent**, and the **same 6 rules** (rule_0005, rule_0006, rule_0007, rule_0013, rule_0014, rule_0015) fired for every single goal. For a product pitched as "intelligent cognitive organization," nearly always answering "just use one agent" is a weak demo and will read as "the AI does nothing."

### G3. Strategy vocabulary mismatch (P1)
`Strategy` enum has **9 values** (`direct`, `single_agent`, `parallel_agents`, `hierarchical_agents`, `debate`, `research_then_execute`, `execute_then_verify`, `simulation_first`, `adaptive_spawn`) but README/QUICKSTART document only 3. `air.allocate("Summarize this codebase")` returned `Strategy.ADAPTIVE_SPAWN` — a value no doc explains. Users can't interpret what they can't look up.

### G4. Lexical brittleness in v1 (P2)
- "Research three competitors and compare" → v1: parallel_agents, pv2: parallel_agents
- "Research three competitors and compare their pricing" → v1: parallel_agents, pv2: **single_agent** (via a -0.200 adjustment)

Adding two words flips the pv2 decision. OOD-1 validated robustness to *trigger-word removal*, but ordinary paraphrase still moves the needle. Worth noting in docs as a known sensitivity.

### G5. Two-terminal requirement (P1)
`air run` requires `air start` in a separate terminal. There is no embedded/standalone run mode. The QUICKSTART documents this, but it's the kind of friction that loses new users in the first 5 minutes. `air run` should optionally start its own backend.

### G6. "Rules fired" is meaningless to users (P2)
`Rules fired: rule_0005, rule_0006, rule_0013...` — opaque IDs with no human-readable meaning. The explanation feature is good; the rule identifiers undermine it. Map to short descriptions ("single-step task → prefer single agent").

### G7. Frontend dead end (P3)
`air doctor` reports `[FAIL] frontend: web/ not yet built` with no guidance on what to do about it (build it? ignore it? is it needed?). Either build it in the wheel, document the build, or stop flagging it.

## What's missing for a real user

1. **Embedded run mode** — `air run` without a manually-started server.
2. **Capability elicitation** — since text extraction can't detect capabilities (G1), ask: `air allocate` could interactively clarify ("does this need file writes? network?") or accept `--needs write,execute` flags. This would actually engage the validated policy.
3. **Real-provider end-to-end test** — couldn't test execution; no Ollama here, no Nebius key in scope. The `air run` watch loop (60 polls x 5s) is untested against a live provider.
4. **Strategy documentation** — all 9 strategies explained, or the enum narrowed to what pv2 actually emits.
5. **`--strategy` choices in help** — `air run --help` shows `--strategy STRATEGY` as free text; should list valid choices.
6. **Provider connectivity check in `air init`** — currently writes config without verifying ollama is reachable; `air doctor` catches it later, but init is the moment to say "ollama isn't responding, want to skip?"

## Prioritized recommendations

**P0 — fix before any announcement:**
- Fix `air run` API contract (`run_id` vs `id`) and the list-vs-dict crash (B1).
- Fix `air init` skip persistence (B2).
- Engage capability annotations for plain-text goals, or add interactive/flag-based elicitation (G1). Without this, the core differentiator doesn't function.

**P1 — needed for the product to feel real:**
- Embedded run mode: `air run` starts its own backend if none is running (G5).
- Address pv2 conservatism: either tune, or document *when* it actually parallelizes so users can see it work (G2).
- Document all strategies or narrow the enum (G3).
- Human-readable rule explanations (G6).

**P2 — trust and polish:**
- Input validation with helpful errors (B4).
- Fix stderr/stdout ordering in `air run` (B3).
- Document paraphrase sensitivity honestly (G4).
- `air run --help` lists valid `--strategy` choices.

**P3 — later:**
- Frontend: build it, document the build, or drop the doctor check (G7).
- Provider connectivity check during `air init`.
- Live-provider end-to-end run test (needs Ollama or Nebius).

## Raw evidence

- Fresh venv: `/tmp/dogfood-venv` (`pip install adaptive-intelligence-runtime` → 0.2.0)
- `air init -y` and interactive (choice 3) — B2 reproduced
- `air allocate` x8 goals — G2 reproduced (7/8 single_agent, identical rules)
- `air run` without backend — B3 reproduced
- `air run` with backend on :18765 — B1 reproduced (traceback)
- `import air` API: empty/non-string/long/unicode goals — B4 reproduced
- Feature extraction probe: "Write the results to a file" → no WRITE effect — G1 reproduced
- `air doctor`, `air config`, `air runs`, `air capabilities`, bad subcommand — all behaved well
