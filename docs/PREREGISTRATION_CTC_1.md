# CTC-1: Counterfactual Topology Challenge — Preregistration Protocol

**Status:** DRAFT (pending Inan review and freeze approval — NOT frozen, NOT implemented)
**Date:** 2026-10-06
**Freezes against:** pv2_cand_001 (immutable), D-series (closed), OOD-1 (closed)

## 1. Purpose

Determine whether pv2 conditions allocation on the actual capability
topology required to complete a task (E1), or whether it has learned
the shortcut "production/output tasks → single_agent" regardless of
whether parallel organization could satisfy the required capabilities
(E2).

This is a **diagnostic experiment**, not a promotion experiment. No
promotion decision. No learner update. pv2 frozen regardless of outcome.
An E2 result is excellent research: it precisely identifies pv2's
generalization boundary and specifies the target for the next learning
mechanism.

CTC-1 is a mechanistic consistency challenge, not a powered hypothesis
test of generalization. The D-series already supplied the performance
evidence. CTC-1 asks a different question: does the learned policy
respond to the causal/mechanical variable we believe it learned?

## 2. Competing Explanations

**E1 — capability feasibility:**
Parallelism is dispreferred when the selected organization cannot
satisfy the required capability. The decision tracks capability
feasibility. On counterfactual pairs, pv2 should SWITCH allocation
when feasibility changes.

**E2 — category shortcut:**
Production/output tasks should use single_agent. The decision tracks
task category, overgeneralized from D-series where parallel happened
to lack WRITE. On counterfactual pairs, pv2 should NOT switch;
it should choose single_agent for both members.

## 3. Experimental Design

### 3.1 Task Pool (48 tasks = 24 counterfactual pairs)

**12 AB pairs (24 tasks) — PRIMARY.**
Same task objective. Same lexical surface: task text byte-identical
(or semantically identical with only non-meaning-bearing variation).
The ONLY experimental difference is the `topology_override` in the
task environment/schema — never in the language given to the allocator.

- *A member (infeasible):* `parallel_agents` topology = researcher,
  researcher, synthesizer. Capabilities: READ, EXECUTE. No WRITE.
  Task requires READ + EXECUTE + WRITE.
- *B member (feasible):* `parallel_agents` topology = researcher,
  researcher, producer (write-capable). Capabilities: READ, EXECUTE,
  WRITE. Identical task requirements, identical surface text.

The manipulation exists in the task environment/schema, not in the
language. If the capability change were represented in natural-language
task text, the counterfactual would be contaminated.

**6 C pairs (12 tasks) — capability substitution.**
Same production task (requires WRITE). Vary WHICH role in the parallel
topology holds the WRITE capability:
- C_a: producer holds WRITE (feasible).
- C_b: synthesizer holds WRITE (feasible, different role).
Tests whether the decision follows capability presence rather than
role names. Both members should yield `parallel_agents` under E1.

**6 D pairs (12 tasks) — irrelevant capability.**
Same production task. Parallel topology gains additional capabilities
that do NOT resolve the WRITE bottleneck (e.g., deeper READ, debate,
verification capability).
- D_a: baseline infeasible (no WRITE).
- D_b: infeasible + irrelevant capabilities (still no WRITE).
pv2 must choose `single_agent` for both members under both E1 and E2.
This guards against the spurious rule "more capabilities → parallel
is good." D pairs do not distinguish E1/E2; they test invariance.

### 3.2 Topology Representation (Frozen Mechanism)

Strategy names are FIXED (`parallel_agents`, `single_agent`, etc.).
v1's lexical scorer is unchanged.

Each task may carry an optional `topology_override`:
```json
"topology_override": {
  "parallel_agents": {
    "roles": ["researcher", "researcher", "producer"],
    "capabilities": ["READ", "EXECUTE", "WRITE"]
  }
}
```

Semantics (frozen):
- When present, the `_feasible` check for the named strategy uses the
  override's capability set instead of the global `ORG_PROPS`.
- When absent, global `ORG_PROPS` apply (backward compatible).

### 3.3 Formal Invariants (Preregistered)

**Invariant T1 — v1 topology blindness.**
The CTC topology intervention must have no execution path into v1
scoring or feature extraction. Concretely:
- `goal_features(task["goal"])` never reads `topology_override`.
- `extract_features(task)` never reads `topology_override`.
- `score_strategies` receives no topology input.
- With empty rules, `_feasible` receives no requirements, so all
  strategies are feasible regardless of override.

A structural test MUST verify T1 before execution: for every task,
`parent_choose(task)` with and without the override present must
return identical choices. (v1 has no requirements, so the override
cannot affect it, but the test makes this mechanical, not assumed.)

**Invariant T2 — label leakage prohibition.**
The learner/policy must not receive as task features: `topology_override`,
pair identity, AB/C/D membership, expected prediction, counterfactual
labels, or any derived indicator thereof.

`topology_override` is part of the environmental mechanics (it determines
what the organization CAN do), not an epistemic label (it does not tell
the policy what it SHOULD do). The policy sees its effects only through
the feasibility mechanism, exactly as a real deployment would expose
capability constraints through execution, not through annotations.

A structural test MUST verify T2: `extract_features(task)` output must
be identical whether or not `topology_override` is present on the task
(the override affects only `_feasible`, never feature extraction).

### 3.4 Execution

- **Policies:** Frozen pv2 vs frozen v1 parent. Paired on all 48 tasks.
- **No learner update:** runs recorded as `phase: ctc-1`, excluded from
  all current and future learning corpora. No `learn` step will ever
  load `phase == "ctc-1"`.
- **Decision decomposition** recorded per (task, policy) with the
  feasibility block (Section 6).

### 3.5 Sealing

Before execution, freeze and hash: task pool, generator, topology
override schema, `extract_features`, `_feasible` implementation, v1
allocator (word lists), pv2 artifact, evaluator, outcome definitions,
analysis script, exclusion rules, resource thresholds, frozen-score
audit output (Section 4).

## 4. Frozen-Score Audit and Discriminating-Pair Requirement

### 4.1 Rationale

The E1 prediction for a B member ("pv2 switches to parallel_agents")
depends on the arithmetic of frozen scores: v1's lexical base for
parallel, minus pv2's penalty, compared against single_agent's score.
Dry-run estimates are NOT sufficient. The prediction must be computed
from the exact frozen allocator on the exact frozen tasks.

### 4.2 Score Audit Procedure (Pre-Freeze, Pre-Execution)

After task generation but before freezing:

1. For every AB pair member, compute using the frozen allocator
   (no task execution, allocation choices only):
   - `v1_base_scores`, `pv2_adjustments`, `pv2_final_scores`
   - `v1_choice`, `pv2_choice`
   - feasibility of each strategy under the task's topology
2. Record all scores in the freeze record.
3. Determine each pair's PREDICTION_STATUS mechanically:
   - **DISCRIMINATING** iff:
     - A: (`single_agent` final score > `parallel_agents` final score)
       AND/OR (`parallel_agents` infeasible), AND
     - B: (`parallel_agents` feasible) AND
       (`parallel_agents` final score > `single_agent` final score).
   - **NON_DISCRIMINATING** otherwise (e.g., B's parallel score does
     not exceed single_agent despite feasibility).

### 4.3 Discriminating-Pair Requirement at Freeze

**All 12 AB pairs MUST be DISCRIMINATING at freeze time.**

If any pair is NON_DISCRIMINATING (e.g., B's parallel score ≤
single_agent despite feasibility), regenerate or adjust that pair
before freezing. This pre-freeze iteration MUST NOT inspect pv2's
execution outcomes (there are none yet) — it inspects only the
frozen allocator's score arithmetic.

Rationale: a non-discriminating pair cannot distinguish E1 from E2.
If B's parallel is feasible but still scores lower, then pv2 choosing
single_agent for both members is consistent with BOTH "pv2 understands
feasibility but correctly doesn't prefer parallel" AND "pv2 uses a
category shortcut." Counting such a pair as an E1 failure would be
invalid. Requiring 12/12 discriminating pairs at freeze eliminates
this ambiguity by construction.

### 4.4 Post-Freeze Rules

- Once frozen: **no exclusions, no repairs, no removals, no rewrites**
  of any pair based on execution outcomes or pv2's actual decisions.
- The primary denominator is fixed at 12.
- Non-discriminating count at freeze must be 0/12 (enforced by 4.3).
- If a frozen pair behaves unexpectedly at execution, it is reported
  under the falsification matrix (Section 7), not excluded.

## 5. Preregistered Predictions

### 5.1 v1 Control

v1 chooses purely on lexical scores; all pair members share the
parallel-trigger word. Preregistered: v1 → `parallel_agents` for
every member of every pair (A, B, C, D).

Deviations are flagged (not excluded) and reported; a pair where v1
deviates is not a clean counterfactual for that task.

### 5.2 pv2 under E1

- **AB:** `single_agent` for A, `parallel_agents` for B (switch).
- **C:** `parallel_agents` for both members.
- **D:** `single_agent` for both members.

### 5.3 pv2 under E2

- **AB:** `single_agent` for both members (no switch).
- **C:** `single_agent` for both members.
- **D:** `single_agent` for both members (consistent with E1 here).

## 6. Decision Decomposition Schema (Extended for CTC-1)

Per (task_id, policy), in addition to OOD-1 fields:

```
feasibility: {
  <strategy>: {
    feasible: bool,
    infeasible_reasons: [<requirement descriptions>],
    capabilities_considered: [<capability list>],
    topology_source: "override" | "global"
  }
}
```

For AB pairs, the desired mechanistic signature is:

```
A member:
  parallel_agents
    → feasible: false
    → infeasible_reasons: ["REQUIRE_CAPABILITY WRITE unsatisfied",
                           "capabilities_considered: [READ, EXECUTE]"]
    → topology_source: "override"
    → eliminated from feasible set
  single_agent selected

B member:
  parallel_agents
    → feasible: true
    → capabilities_considered: [READ, EXECUTE, WRITE]
    → topology_source: "override"
    → final score exceeds single_agent
  parallel_agents selected
```

If B switches but the decomposition does NOT show this feasibility
path (e.g., different rules fired, or scores changed for unrelated
reasons), flag as mechanistic anomaly per the falsification matrix.

## 7. E1/E2 Falsification Matrix (Preregistered)

| Observation | Interpretation |
|---|---|
| A single + B parallel, feasibility explains both (per 6) | E1 supported for pair |
| A single + B single despite B parallel feasible AND B parallel score > single (per frozen audit) | E1 falsified for pair; E2 supported |
| A parallel | Mechanistic anomaly / protocol failure: A's parallel topology is infeasible by construction |
| B switches but feasibility did not eliminate A's parallel, or different rules fired | Mechanistic anomaly: report verbatim, do not count as E1 |
| D member changes decision between D_a and D_b | Irrelevant-capability sensitivity: spurious rule evidence |
| C fails to respond to capability substitution (both single despite feasible WRITE) | Capability-sensitivity failure |
| v1 deviates from parallel on any pair member | Control failure: pair flagged, counterfactual compromised for that task |

This matrix makes the experiment hard to reinterpret after seeing
results. Each observation maps to exactly one interpretation.

## 8. Analysis Plan (Preregistered, Frozen as Code)

### 8.1 PRIMARY: Counterfactual Consistency (AB pairs, n=12)

Count per the falsification matrix:
- E1-consistent: X/12
- E2-consistent: Y/12
- Anomalous: Z/12
- Non-discriminating: 0/12 (enforced at freeze per 4.3)

Directional threshold: ≥9/12 E1-consistent supports E1;
≥9/12 E2-consistent supports E2. Otherwise inconclusive.

Report raw counts, not just PASS/FAIL. No p-values (n=12 is a
mechanistic consistency challenge, not a powered hypothesis test).

### 8.2 SECONDARY: C-pair Capability Sensitivity (n=6)

Fraction with `parallel_agents` for both members.

### 8.3 SECONDARY: D-pair Invariance (n=6)

Fraction with `single_agent` for both members (no decision change
on irrelevant capability addition).

### 8.4 SECONDARY: Verified Outcomes and Resources

SUPPORTED+SOUND rates, pv2 vs v1, all 48 tasks.
Verified-success latency/cost within 2.0x (v2.2 gate).

### 8.5 Exclusion Rules

- No post-freeze exclusions for any reason related to pv2's choices
  or observed outcomes.
- v1-deviating pairs flagged per 5.1, reported with reasons.
- INCOHERENT recorded and reported, never imputed.

## 9. Claims Discipline

Permitted (if supported):
- "On preregistered counterfactual pairs holding task requirements
  and lexical surface constant, frozen pv2 changed allocation when
  the parallel organization's capability feasibility changed
  (X/12 E1-consistent), with decision decompositions confirming the
  feasibility mechanism as the causal path."
- "pv2 did not switch allocation on feasibility-matched pairs
  (Y/12 E2-consistent); the learned policy does not represent
  capability feasibility and has overgeneralized the
  production→single_agent association."

Not permitted:
- "This proves the feasibility mechanism caused pv2's decision."
  (The decomposition is necessary evidence; the claim is about
  the observed signature matching the preregistered mechanism.)
- "pv2 understands organizations."
- Pooling AB/C/D into a single accuracy number.
- Any pv2 modification based on CTC-1 outcomes.

## 10. What CTC-1 Does NOT Do

- No promotion decision. No learner update. pv2 frozen.
- No D-series, OOD-1 evidence modification (both closed).
- `phase: ctc-1` excluded from all learning corpora.
- No pv2 retraining on CTC-1 outcomes, even on E2 result.
  An E2 finding specifies the target for the next learning
  mechanism; it does not trigger a pv2 patch.

## 11. Success Criterion

CTC-1 succeeds iff the AB-pair counterfactual consistency test
decisively supports E1 or E2 (≥9/12 in either direction per the
falsification matrix) with decision decompositions confirming
(or refuting) the feasibility mechanism as the causal path.
An inconclusive split or unresolvable mechanistic anomaly is
reported as such.
