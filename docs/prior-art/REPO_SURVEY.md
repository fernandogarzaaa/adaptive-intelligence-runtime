# Prior-art survey (2026-10-04)

Read-only survey of 12 source repos, verified against source (not READMEs).
Full handoff from the survey coordinator is summarized here; the authoritative
detail lives in that handoff. Patterns marked ADOPTED are mirrored in AIR
(with upstream SHAs pinned below where relevant).

## Ranked patterns

1. **Hash-chained SQLite ledger, single write path** — genesis `src/ledger/ledger.ts`
   (`#append`, `verifyChain`). ADOPTED: `air/events/fabric.py::EventStore`.
2. **ProvenanceKind on every artifact** (observed/derived/forecast/simulated) —
   EVE---MIRO `core/world/events.py`. ADOPTED + extended: HYPOTHETICAL, COUNTERFACTUAL
   (`air/experience/provenance.py`).
3. **Grounding gate before success claims** — ape-mcp `src/agent/loop.js`
   (`checkGrounding`, `hasVerifyEvidence`). ADOPTED as verification stage: a
   sibling's self-report is a CLAIM, never a fact.
4. **Atomic budget admission at ledger level** — ape-mcp `src/runs.js::admitRun`
   + GHOST `stealth/budgets.py` atomic check-and-spend. ADOPTED: budgets enforced
   in code (`air/agents/runtime.py::_consume`), budget slicing + depth cap for children.
5. **Fencing-token claim protocol** — skein `src/skein/claim.py`. ADOPTED for
   sibling work claiming (lease fencing, TTL/heartbeat/reaper).
6. **Canonical execution gateway** — SYNK `harness/gateway.py::ExecutionGateway.execute()`
   + 12-step transaction lifecycle + default-deny policy. ADOPTED as the execution
   path shape; learn-only-from-verified (SYNK `workflow_store.py::WorkflowLearner`).
7. **ResultEnvelope universal result carrier** — chimeralang-mcp
   `chimeralang_mcp/envelope.py`. ADOPTED as the cross-stage result shape
   (confidence, provenance, claims, constraints_applied).
8. **Declarative run spec** — genesis `src/eval/spec.ts::EvalSpec`
   (verdicts SUPPORTED/FALSIFIED/INCONCLUSIVE/INVALID/UNTESTED). ADOPTED for
   evaluation + assurance verdicts.
9. **Assurance probes auditing the evaluators** — genesis `src/assurance/`.
   ADOPTED: assurance measures verifiers (false-accept/false-reject), never trusts them.
10. **Governance choke point** — ADAM `crates/adam-governance/src/gate.rs::GovernanceGate`.
    ADOPTED for capability/policy promotion: single authorize path + audit record.

## Do-not-copy list (verified gaps)

- In-memory "append-only" ledgers (EVE---MIRO `ledger.py`, SYNK EventJournal):
  append-only must be durable or it is not a ledger.
- `INSERT OR REPLACE` on parent rows with FK dependents: REPLACE deletes the row
  and cascades. (Also found independently as a live AIR bug, 2026-10-04.)
- Hand-rolled MCP JSON-RPC (ADAM, godmode): use the official MCP SDK.
- Zero-auth localhost servers (SYNK, EVE API): AIR ships an auth seam from day one.
- Docstring inflation (chimeralang-mcp "AGI" section: Jaccard overlap labeled
  "semantic similarity"); benchmark claims without n/caveats (AXIOM TTT n=3).
- AGPL-licensed vendored code (godmode `vendors/eve-miro/mirofish/`): AIR stays MIT-only.

## Architecture decisions (principal engineer, 2026-10-04)

1. Sibling execution: fresh Python runtime keeping ape-mcp's
   loop/delegate/budget/grounding shapes 1:1.
2. Coordination: Skein fencing-token + event-sourced log; execution path: SYNK
   lifecycle + postcondition verification; handoff payloads: AXIOM context-digest shape.
3. Hash-chaining: assurance-critical tables only
   (events, audit_entries, evaluations, assurance_runs, capability_versions,
   policy_versions). Ordinary state tables are not chained.
4. Auth: bearer-token seam (timing-safe compare, env-provided), local-open
   default for localhost UI; documented in SECURITY.md.
5. MCP: official Python MCP SDK for client and server; no hand-rolled protocol.
6. Provenance enum: OBSERVED, DERIVED, FORECAST, SIMULATED, HYPOTHETICAL,
   COUNTERFACTUAL. Simulations never become observations.
7. Verifiers are separate tool-chains (deterministic checks + separate model
   calls); first adversarial probe suites ship with the assurance engine.
8. No Dempster-Shafer; evidence combination is calibrated averaging with
   provenance, labeled heuristic.
9. UNVERIFIED is a first-class terminal run outcome, distinct from FAILED.
10. Upstream SHAs are pinned in this file for every mirrored pattern; never rely
    on drifting local checkouts.

## Pinned upstream SHAs (survey time)

- genesis @ 9f02289 (ledger.ts, spec.ts, assurance/)
- skein origin/main @ 910b00b (claim.py, graph.py, verify.py)
- SYNK /tmp/synk clone (gateway.py, transactions.py, policy.py, workflow_store.py)
- ape-mcp ~/workspace/ape-mcp (runs.js, loop.js, budget.js, registry.js, outcomes.js)
- chimeralang-mcp ~/workspace/chimera-mcp-repo (envelope.py, replay.py, protocol.py)
- EVE---MIRO /tmp/eve-miro @ 236dd73 (core/world/events.py, trust_profile.py)
- ADAM ~/workspace/adam @ 70b3b97 (gate.rs, envelope.rs, eventlog)
- GHOST-Chimera ~/workspace/ghost-chimera (event_bus.py, approvals.py, budgets.py)
- AXIOM-AETHER /tmp/axiom (task_board.rs, cost_ledger.rs, model_router.rs)
- godmode /tmp/godmode (server.js, dispatch.js, mods.js)
- artemis /tmp/artemis (telemetry/models.py, tracer.py, replay.py)
- fast-jev-compaction /tmp/fjc (compact.ts, state.ts)
