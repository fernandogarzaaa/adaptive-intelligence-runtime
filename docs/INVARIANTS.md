# AIR Invariants

This document states the properties AIR must never violate. Each invariant
maps to an implementation boundary and to tests at every level that can
express it. An invariant without a failing-then-passing test is a wish, not
a guarantee.

Status legend: `[held]` proven by a test that fails if the invariant
breaks; `[partial]` covered at some levels only; `[gap]` stated but not
yet tested. The production-hardening pass (2026-10-04) exists to close
every `[gap]`.

Test levels:
- **unit**: `tests/test_*.py` against one module
- **integration**: runtime + persistence + events together
- **adversarial**: hostile or malformed inputs, bypass attempts
- **e2e**: `tests/test_full_chain_experiment.py`, console acceptance
  (`web/tests/acceptance/test_console_acceptance.py`)

---

## 1. Authority

**No agent can directly execute a capability.** Every tool call passes
through the execution gateway, which independently resolves identity,
scope, capability grants, policy, budget, workspace, and approval.

- Boundary: `src/air/tools/gateway.py` (`ExecutionGateway.execute`),
  resolver `src/air/tools/resolver.py`, `src/air/security/policy.py`.
  Direct registry-handler invocation bypasses `ToolCall` auditing and is
  forbidden (see boundary audit 2026-10-04).
- unit: `tests/test_tools_adversarial.py` (resolver checks)
- integration: `tests/test_runtime_e2e.py`
- adversarial: `tests/test_invariants.py` (direct-execution attempts),
  `tests/test_security.py`
- e2e: console acceptance steps 6, 14 (COMMITTED call with checks;
  escalation denied with resolver checks visible)
- `[partial]` gap: adversarial coverage for direct handler invocation
  paths is now structural
  (`tests/test_tools_adversarial.py::test_no_direct_handler_invocation_outside_gateway`);
  MCP server restart / nested calls during execution.
- `[held]` (2026-10-04 hardening: supervisor-task teardown, lock
  narrowed to `_reserve()`, 50-way budget stress test green)

## 2. Promotion

**No policy candidate can modify the active policy without valid
evaluation and assurance references.** `promote()` resolves verdicts
from persisted `evaluation` and `assurance` rows, never from
caller-supplied dictionaries. Direct row mutation cannot move
`current_version`.

- Boundary: `src/air/learning/policies.py` (`PolicyStore.promote`),
  API service `src/air/api/services.py` (accepts `evaluation_id` /
  `assurance_id`, not verdict dicts).
- unit: `tests/test_policy_learning.py`
- adversarial: `tests/test_invariants.py::test_sql_promotion_bypass_fails`,
  `tests/test_policy_exploit.py` (spoofed verdicts, bypass attempts)
- e2e: console acceptance steps 9-13, 19 (SUPPORTED/SOUND, promote,
  bypass blocked with 409)
- `[held]`

## 3. Evidence

**No OBSERVED memory exists without an external evidence reference.**
Direct API memory writes are forced to `USER_ASSERTED`; only the
experience recorder, grounded in tool results, may write OBSERVED.

- Boundary: `src/air/memory/store.py`, `src/air/experience/recorder.py`,
  API service (write-kind coercion).
- unit: `tests/test_memory.py`
- integration: `tests/test_experience_world.py`
- adversarial: `tests/test_invariants.py` (kind-forgery attempts)
- `[partial]` gap: memory contamination across runs (cross-run read
  isolation under adversarial memory_scope).

## 4. Epistemic separation

**Simulation cannot be represented as observation.** Closed 2026-10-04;
formalized as Invariant #12 below.

`[held]`

## 5. Scope

**A run cannot access another run's task-scoped state.** Messages,
memory reads, and tool calls are namespaced by `run_id`; out-of-scope
communication is denied and recorded as `policy.blocked`.

- Boundary: `src/air/agents/runtime.py::send_message` (raises
  `PermissionError` after emitting `policy.blocked`),
  `src/air/api/services.py` (maps to 403, never 500).
- unit: `tests/test_security.py`
- integration: `tests/test_api_projection.py::test_out_of_scope_message_is_403_not_500`
- adversarial: console acceptance step 15 (cross-run message denied)
- `[partial]` gap: deeper cross-run credential/state isolation
  (workspace roots, approval bindings across runs).

## 6. Budget

**Concurrent execution cannot consume more budget than reserved.** The
gateway holds its reservation lock only for the atomic reservation
step; every spawn and every tool call checks the budget before
committing.

- Boundary: `src/air/tools/gateway.py` (reservation),
  `src/air/agents/runtime.py::spawn_agent` / `create_agent`.
- unit: `tests/test_budgets.py`
- adversarial: `tests/test_invariants.py::test_budget_cannot_be_bypassed_via_create_agent`,
  console acceptance step 16 (budget exhaustion denial)
- `[partial]` gap: gateway now holds the lock only for the atomic
  reservation step (`_reserve()`); a 50-concurrent-call stress test
  proves no over-consumption (`tests/test_budgets.py`). Remaining:
  none open on this invariant.
- `[held]` (2026-10-04 hardening)

## 7. Provenance

**Every consequential tool action has an auditable authorization
decision.** The decision records the resolver checks (identity, scope,
capability, policy, budget, workspace, approval) and is retrievable via
`/tool-calls/{id}/authorization` and the explain endpoints.

- Boundary: `src/air/tools/gateway.py` (decision persistence),
  `src/air/api/services.py::ExplainService`.
- integration: `tests/test_api_projection.py` (explain endpoints)
- e2e: console acceptance step 6 (checks visible in UI)
- `[held]`

## 8. Event integrity

**Mutation events cannot be inserted or replayed without preserving
ledger integrity.** The event ledger is an append-only hash chain in
SQLite; out-of-band inserts break the chain and are detectable via
`/ready` and the chain verifier.

- Boundary: `src/air/events/` ledger, `src/air/persistence/db.py`.
- unit: `tests/test_event_chain.py` (chain verification, tamper
  detection)
- integration: eight-thread mixed read/write stress (zero errors,
  chain verified)
- `[held]` (2026-10-04 hardening: append lock verified under
  8-thread contention at store and `emit` level; chain verifies)

## 9. Recovery

**Replaying the event history reconstructs equivalent runtime state.**
Every lifecycle transition (spawn, tool execution, approval,
experience, evaluation, assurance, promotion, rollback) is atomic,
recoverable, or explicitly resumable across process crash, DB
reconnect, WebSocket disconnect, MCP server crash, agent
cancellation, and tool timeout.

- Boundary: `src/air/persistence/` (migrations, connection handling),
  per-transition commit points.
- `[held]` (2026-10-04 hardening: `tests/test_crash_consistency.py`,
  17 tests; atomic commit points via `store.atomic()`; startup
  reconciler `src/air/persistence/recovery.py` moves crash-stuck
  tool calls to INTERRUPTED and stuck agents to FAILED, idempotently)

## 10. Event semantics

**Integrity is not correctness.** A perfectly hash-chained sequence of
incorrect events is still incorrect. Event ordering, causation IDs,
correlation IDs, sequence guarantees, duplicate handling, schema
migrations, and reducer determinism are audited independently of the
hash chain.

- Boundary: `src/air/events/` (envelope, fanout), canonical envelope
  (`event_id`, `event_type`, `schema_version`, `timestamp`, `run_id`,
  `agent_id`, `causation_id`, `correlation_id`, `sequence`, `payload`).
- unit: `tests/test_event_chain.py`
- `[held]` (2026-10-04 hardening: `tests/test_event_semantics.py`,
  15 tests; reducer rejects unknown schema versions loudly;
  determinism A == B verified; fuzz documents replay-order and
  dedup rules). Causation/correlation populated 2026-10-04:
  `causation_id` is the immediately preceding event that directly
  caused this one; `correlation_id` is the logical workflow
  (run_id for run-scoped events; policy name / capability id /
  connector id for non-run workflows; null means "no known
  workflow", never a guess). Roots declare `causation_id = null`
  explicitly; the store boundary refuses dangling references
  loudly. Threaded chains: run.created -> run.started ->
  agent.created -> agent.started -> tool.requested -> validated ->
  authorized/denied -> approval_requested -> approved -> dispatched
  -> completed/failed -> run.completed -> experience.created ->
  evaluation.completed -> assurance.completed; capability lifecycle
  (proposed -> validated -> promotion_reviewed -> promoted/rejected;
  rolled_back <- promoted); policy reject/rollback chains.
  Tests: `tests/test_causation.py` (9 tests); the two former
  tripwires in `tests/test_event_semantics.py` were deliberately
  rewritten to pin the correlation rule and an explicit allowlist
  of legitimate causation_id value shapes.

## 11. Independence

**The system being evaluated cannot unilaterally establish that its
own improvement is valid.** Evaluation and assurance are separate
stages with separate evidence; the learning engine consumes only
their persisted verdicts. AIR auditing AIR must route through the
same boundary.

- Boundary: `src/air/evaluation/`, `src/air/assurance/`,
  `src/air/learning/` (bridge consumes persisted rows).
- adversarial: `tests/test_policy_exploit.py` (evaluator spoofing),
  console acceptance step 17 (spoofed verdicts rejected with 422)
- `[partial]` gap: the AIR-audits-AIR experiment (adversarial
  researcher agents hunting reward hacking, evaluator correlation,
  selection/survivorship bias, capability/memory contamination,
  policy overfitting, spawn inflation, verification avoidance,
  cost/quality masking, false capability transfer, distribution
  shift) must itself pass through evaluation + assurance before any
  finding changes the system.

---

## 12. Epistemic separation (formal)

**SIMULATED, FORECAST, HYPOTHETICAL, and COUNTERFACTUAL provenance
can never ground a verification verdict, an observation, or a
capability claim.** They may inform allocation and generate
hypotheses, but simulation can generate evidence *for* a hypothesis
without ever *becoming* evidence that the hypothesis is true in
reality.

Enforcement lives at the data-model and evaluation boundaries, not
merely inside the evaluator:

- `src/air/experience/provenance.py`: `NON_EVIDENTIARY` frozenset;
  the four kinds are first-class, not stringly-typed.
- Agents carry a persisted `epistemic_kind` (migration `0011`,
  default OBSERVED). The allocator marks simulator specs; spawn
  heredity prevents laundering simulator output through an
  OBSERVED child.
- `src/air/evaluation/suites.py::epistemic_refusal`: the
  evaluation entry point refuses all-simulator runs and
  non-evidentiary experience with an auditable INVALID verdict
  (never SUPPORTED).
- `src/air/assurance/probes.py::_probe_epistemic_separation`:
  SUPPORTED-on-simulated-evidence is a false accept; the system
  verdict stays INVALID (never SOUND, never promotable).
- `src/air/memory/store.py`: OBSERVED requires an `evidence_ref`
  resolving to a real ledger event from a non-simulator agent;
  fabricated refs and simulator-produced refs are refused loudly.
- `src/air/experience/recorder.py`: all-simulator runs record
  SIMULATED provenance (never silently OBSERVED); simulator tool
  output is labeled per-action.
- The promotion gate requires SUPPORTED; simulated evaluations
  are INVALID, so simulation-only policy candidates are blocked
  (`GateBlocked`).
- Malicious `"verified": true` tool output stays inside the
  gateway's untrusted framing and never reaches the ledger as fact.

Tests: `tests/test_epistemic_separation.py` (11 tests) covering
simulation/forecast -> SUPPORTED, simulation -> SOUND,
hypothesis -> observed-fact write, counterfactual -> experience
provenance, plus the five adversarial variants (wrapped as
OBSERVED, evaluator-supplied simulation, malicious self-verified
tool output, simulation-only capability claims, simulation-only
policy evidence).

`[held]` (2026-10-04)

---

## Mapping summary

| Invariant | unit | integration | adversarial | e2e | status |
|---|---|---|---|---|---|
| 1 Authority | yes | yes | yes | yes | held |
| 2 Promotion | yes | — | yes | yes | held |
| 3 Evidence | yes | yes | partial | — | partial |
| 4 Epistemic separation | — | — | — | — | held |
| 5 Scope | yes | yes | yes | yes | partial |
| 6 Budget | yes | — | yes | yes | held |
| 7 Provenance | — | yes | — | yes | held |
| 8 Event integrity | yes | yes | — | — | held |
| 9 Recovery | yes | yes | yes | — | held |
| 10 Event semantics | yes | — | yes | — | held |
| 11 Independence | — | — | yes | yes | partial |
| 12 Epistemic separation (formal) | yes | yes | yes | — | held |
| 13 Evidence grounding | yes | yes | yes | — | held |

---

## 13. Evidence grounding

**A successful tool execution is not, by itself, evidence of task
success. It is only an eligible evidence source.** TOOL_SUCCESS and
CLAIM_SUPPORTED are never conflated: the former is a property of a
tool call, the latter a verdict about a claimed proposition, and the
verdict requires strictly more.

SUPPORTED means: *the available evidence establishes the claimed
proposition within the declared verification scope.* Not "something
happened that looks vaguely related." Not "the agent said it
succeeded."

Enforcement is three structural layers (`src/air/evidence/`):

1. **Evidence Validity** — did this actually happen? A
   `tool.completed` event counts as evidence only if it exists in
   the evaluated run's ledger, `ok` is true, the `tool_calls` row
   holds a persisted result, `sha256(result_redacted)` matches the
   event's `result_hash` (recomputed, never trusted), and the
   producer's `epistemic_kind` is evidentiary (Invariant 12: a
   simulator's success is not real-world evidence). Necessary but
   explicitly not sufficient.
2. **Evidence Relevance** — does it establish the claim? Every
   claimed outcome must cite ≥1 `evidence_ref`; every ref must
   resolve to valid evidence; every asserted artifact/effect must be
   covered by the cited evidence's `verification_scope`
   (tool/targets/effect, derived mechanically from the call, never
   by an LLM judge). Fail-closed: uncited claims, invalid evidence,
   and scope mismatches are never grounded. Opaque tools (empty
   targets) can never ground artifact claims.
3. **Outcome Evaluation** — did the objective actually succeed?
   The evaluator's `outcome_grounding` check requires every claimed
   outcome grounded through layers 1+2 before SUPPORTED.

Evidence is a deterministic projection of `tool.completed` ledger
events (`evidence_id = ev_<source_event_id>`); no separate mutable
evidence table. Claims declare outcomes in completion payloads
(`result.outcomes` with `evidence_refs`, `artifacts`, `effects`);
`claim_ref` is bound bidirectionally at evaluation time.

Tests: `tests/test_evidence_grounding.py` (4: the quarterly-report
attack in strong and weak variants, the ok=false case, the genuine
`fs.write` positive control) and `tests/test_evaluator_adversarial.py`
(6: cross-run citation, tampered hash, simulator evidence,
double-citation, denied-tool citation, dangling ref).

Known limitation, by design: no LLM relevance judge exists. A
sophisticated attacker who fabricates coherent-but-false
claim+artifact declarations (plausible nonsense written to the
correctly-named file) produces evidence that is valid and relevant
under this layer. Content-level truth is beyond structural
grounding; semantic verification, when built, must sit behind the
assurance machinery and be adversarially evaluated itself.

`[held]` (2026-10-04; closes the AIR-SELF-AUDIT v1 reward-hacking
finding, falsified by the v2 audit run)

Closing every `[gap]` and `[partial]` is the production-hardening
program. No new features until this table reads `held` throughout.
