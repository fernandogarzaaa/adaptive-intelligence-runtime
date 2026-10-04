# AIR Research Evaluation Protocol

**Status: protocol only. No benchmarks have been run under this
protocol, and none should be published until they have.**

This document defines how AIR's central research question gets
answered honestly:

> Does dynamic cognitive organization produce better *verified*
> outcomes than fixed organization under equivalent resources, and
> can AIR learn that distinction without corrupting its own evidence?

Anything that looks like "AIR improves X by Y%" without following
this protocol is marketing, not science.

---

## 1. Conditions (the question being asked)

Compare three organizations on the same work:

- **Baseline A — single agent.** One agent, the full budget, no
  spawning, no delegation.
- **Baseline B — static multi-agent workflow.** A fixed topology
  (e.g., planner → researcher → verifier) defined once, before the
  task set is seen. No runtime reorganization.
- **AIR — dynamic cognitive allocation.** The allocator chooses
  strategy per run; agents spawn, communicate, and terminate
  dynamically under the spawn policy.

The comparison is only meaningful if the *organization* is the
independent variable. Everything else is held constant (section 3).

## 2. Anti-gaming rules

These rules exist because the system under test is also the system
that records the evidence.

1. **The task set is frozen before any condition runs.** No adding
   tasks that favor one condition after seeing results.
2. **Evaluation and assurance are identical across conditions.**
   Same evaluators, same probes, same thresholds. AIR's learning
   engine may not run during the benchmark, or its policy is pinned
   to v1 for all conditions.
3. **No condition may write its own score.** Success is determined
   by the evaluation/assurance boundary from persisted evidence,
   never by agent self-report. (Invariant 11.)
4. **Simulated or scripted behaviors are labeled and excluded**
   from verified-success counts, or used identically in all
   conditions. (Invariant 4.)
5. **Budgets are enforced by the runtime, not by honor.**
   Token, cost, and tool-call budgets are hard caps per run,
   identical across conditions. Runs that exceed budget are
   failures, not footnotes.
6. **Every run's event ledger is retained.** Any published claim
   must be re-derivable from the ledgers by an independent party.
7. **Failures are published alongside successes.** Verification
   failures, policy regressions, and rollbacks count against the
   condition that produced them. A condition that is fast but
   wrong loses to one that is slow but verified.

## 3. Held constant

- task set (frozen, versioned)
- model and model configuration (same provider, same parameters)
- maximum budget per run (tokens, cost USD, tool calls, wall time)
- tool access (same registry, same capability grants policy)
- evaluation suite and assurance probes (identical)
- environment (same machine class, same network conditions)
- time horizon (same per-task timeout)

If any of these differ between conditions, the run is not a
comparison; it is an anecdote.

## 4. Metrics

Primary (verified outcomes only):

- **verified success rate**: fraction of tasks ending in
  `COMPLETED` with a SUPPORTED evaluation (and SOUND assurance
  where assurance applies)
- **cost per verified success**: USD (and tokens) spent divided by
  verified successes, not by attempts
- **latency per verified success**: wall-clock time divided by
  verified successes

Organization efficiency (where AIR must justify its complexity):

- **spawn efficiency**: verified successes per spawned agent
- **unnecessary spawning**: spawns whose marginal contribution to
  the outcome was zero or negative (measured by ablation:
  re-run the ledger without the spawned agent's events where
  feasible, or by evaluator judgment recorded as evidence)
- **verification failures**: evaluations returning REFUTED or
  assurance returning UNSOUND, by condition
- **policy regressions**: regressions detected under an evolved
  policy that did not occur under v1
- **capability transfer**: tasks solved in a new domain using
  capabilities validated in another, with the evidence chain
  intact
- **rollback frequency**: how often each condition's approach
  required rollback to a prior policy

Secondary (diagnostic, never headline):

- event volume per run, graph depth/width distributions,
  approval frequency, denial rate by check type.

## 5. Reporting requirements

A benchmark report under this protocol includes:

1. the frozen task set (version/hash) and the pin of every
   held-constant parameter;
2. per-condition tables for every primary and efficiency metric,
   with run counts (no cherry-picked subsets);
3. the failure ledger: every verification failure, regression,
   and rollback, with run IDs;
4. the AIR policy version used (pinned v1 unless the experiment
   is *about* policy evolution, in which case the evolution
   itself is part of the measured conditions);
5. a statement of what was *not* measured and why;
6. pointers to the retained event ledgers sufficient for
   independent re-derivation.

## 6. Explicitly out of scope (for now)

- Cross-model comparisons (one model at a time).
- Human-judged quality beyond the evaluation suite (the suite
  is the judge; improving the suite is separate work).
- Claims about generality beyond the frozen task set.

---

## 7. Pre-registration

Before running the comparison, write down:

- the task set hash,
- the exact three conditions and their configurations,
- the metrics to be reported,
- the number of runs per condition,
- the stopping rule.

Deviations are allowed only if recorded with a reason before
the affected runs are analyzed. This is the cheapest defense
against fooling ourselves, and the learning architecture's
whole thesis depends on not fooling ourselves.
