# OOD-1: Generalization Challenge — Preregistration Protocol

**Status:** DRAFT (pending Inan final approval and freeze)
**Date:** 2026-10-05
**Freezes against:** pv2_cand_001 (immutable), protocol v2.2 (frozen), D-series (closed)

## 1. Purpose

Falsify the narrower explanation that D-series gains depended on lexical
triggers inherited from v1's allocator, rather than on pv2's mechanical
task-effect rules.

This is a **diagnostic experiment**, not a promotion experiment. No promotion
decision will be made. No learner update will occur. pv2 remains frozen
regardless of outcome.

OOD-1 does not try to prove pv2 is "abstract." It tests whether the
lexical-trigger dependence hypothesis survives.

## 2. Research Questions and Hypotheses

Two explicitly separated hypotheses:

**H₁ — mechanical discrimination:**
Given identical or near-identical lexical surfaces, pv2 changes allocation
as a function of mechanically extracted task effects.

This tests mechanical-feature conditioning. It does NOT test generalization
or abstraction discovery; the rule is already explicitly encoded
(`requested_read_effect == False` → penalize parallel). A positive result
confirms the rule fires on effects, not on words.

**H₂ — lexical OOD transfer:**
pv2 retains its production allocation advantage when D-series lexical
triggers are removed from the goal text.

This is the genuinely interesting generalization test. D-series production
tasks contained v1 lexical parallel-triggers ("three", "multiple",
"several"). OOD production tasks preserve the mechanical requirements
but remove those triggers. If pv2 still allocates correctly, the
mechanical rules are sufficient without lexical support.

## 3. Architectural Context (Frozen)

The allocator is compositional:

```
choose_strategy(task) = argmax over (
    v1_lexical_base_scores(task.goal) + pv2_mechanical_adjustments(task.features)
)
```

- `v1_lexical_base_scores`: from `goal_features(task["goal"])` using word
  lists including `_PARALLEL_WORDS = {"each", "multiple", "several",
  "independently", "three", "3 ", "list of"}`.
- `pv2_mechanical_adjustments`: from `resolve_rules(pv2_rules,
  extract_features(task))`. `extract_features` reads ONLY task structure
  (artifacts, effects, operations). It never reads goal text.

pv2's 12 rules penalize `parallel_agents` and require EXECUTE+WRITE when:
- `requested_read_effect == False` (strength 0.83, strongest), or
- `independent_work_unit_count > 1`, or
- `output_artifact_count > 1`, or
- `multi_step == False`.

pv2 has NO rule preferring `parallel_agents` for research tasks. On
research tasks (read_effect=True), no pv2 rule fires and the decision
defers to v1's lexical base scores. This is the frozen learned policy;
no research-preferring rule will be added for OOD-1.

## 4. Experimental Design

### 4.1 Task Pool (40 tasks, sealed before execution)

**Primary set — production-OOD (16 tasks):**
Mechanical requirements identical to D-series production (no read effect,
write/execute required, multiple artifacts). Goal text contains zero v1
lexical triggers. Tests H₂.

**Diagnostic sets:**
- *Research-OOD (16 tasks):* read-heavy, observe effects only, scrubbed
  vocabulary. Specificity/control: pv2 should not go INCOHERENT or
  regress vs v1. No specific organization expected.
- *Mechanical-contrast pairs M1, M2 (4 tasks):* shared surface template
  INCLUDING a v1 lexical parallel-trigger ("multiple"/"several"), but
  different mechanical effects (production vs research member). v1
  chooses `parallel_agents` for both (lexical). Tests H₁: does pv2
  discriminate based on effects despite shared trigger?
- *Lexical-contrast pairs L1, L2 (4 tasks):* different surface templates,
  identical mechanical features (verified by equal
  `mechanical_features_hash`). L1: both production. L2: both research.
  Tests lexical invariance: same allocation required unless there is a
  deliberate mechanical difference (there is none).

### 4.2 Lexical Prohibitions (production-OOD and research-OOD only)

Goal text MUST NOT contain:
- Number words: "three", "two", "four", "five", "multiple", "several",
  "various", "numerous", "each", "independently", "list of"
- Production verbs: "create", "write", "generate", "produce"
- Artifact nouns: "file", "files", "document", "report", "manifest"
- Research verbs: "read", "inspect", "check", "review", "examine"

(M-pair templates deliberately retain "multiple"/"several" as the
shared trigger; this is the controlled exception.)

### 4.3 Execution

- **Policies:** Frozen pv2 vs frozen v1 parent. Paired on the same 40 tasks.
- **pv2 blindness:** no task labels, stratum labels, or expected-organization
  annotations. Same as S1.
- **No learner update:** runs recorded as `phase: ood-1`, excluded from all
  current and future learning corpora. No `learn` step will ever load
  `phase == "ood-1"`.
- **Decision decomposition recorded** for every (task, policy) choice
  (Section 6).

### 4.4 Sealing

Before execution, freeze and hash:
1. task pool JSON (`ood_1.json`)
2. task generator script (`gen_ood_1.py`)
3. mechanical feature extraction (`extract_features` in v2harness.py)
4. v1 allocator (`goal_features`, `score_strategies`, word lists)
5. pv2 policy artifact (12 rules, hashes)
6. evaluator and assurance
7. outcome definitions (SUPPORTED+SOUND = verified)
8. analysis script (`analyze_ood_1.py`, frozen)
9. exclusion rules (Section 5.3)
10. resource thresholds (2.0x verified-success latency/cost, same as v2.2)
11. SHA256 of all of the above in the freeze record

Dry-run allocation inspection is permitted before freezing (no task
execution). After freezing, no modifications based on predicted or
observed choices.

## 5. Analysis Plan (Preregistered, Frozen as Code)

### 5.1 PRIMARY: Production-OOD Allocation (tests H₂)

**Metric:** fraction of 16 production-OOD tasks where pv2 chooses
`single_agent`.

**Comparison:** v1 parent's choices on the same 16 tasks (descriptive;
v1 is expected to differ because its lexical triggers are removed).

**Interpretation:**
- High pv2 accuracy (e.g., ≥14/16): mechanical rules sufficient without
  lexical triggers. Weakens the lexical-dependence explanation.
- Low pv2 accuracy: pv2's D-series advantage depended on v1 lexical
  base scores being in a specific regime. Defines the generalization
  boundary precisely.

### 5.2 SECONDARY: Mechanical-Contrast Discrimination (tests H₁)

For M1 and M2: does pv2 choose different organizations for the two
members?

- M_a (production, shared trigger): expected `single_agent`
  (pv2 rule fires on `requested_read_effect == False`).
- M_b (research, shared trigger): expected `parallel_agents`
  (no pv2 rule fires; v1 lexical base dominates).

Discrimination on both pairs = H₁ supported. This confirms
mechanical-feature conditioning, not abstraction discovery.

### 5.3 SECONDARY: Research-OOD Stability

Verify: zero INCOHERENT outcomes for pv2; pv2 verified-outcome rate
not worse than v1 by more than 2 tasks (descriptive non-regression
bound, not a statistical test).

**Exclusion rule:** research-OOD tasks are excluded from the primary
H₂ analysis. They test a different proposition (non-regression).

### 5.4 SECONDARY: Lexical-Contrast Invariance

For L1 (both production) and L2 (both research): pv2 must choose the
SAME organization for both members. Verified by equal
`mechanical_features_hash` with unequal `raw_text_hash`.

### 5.5 SECONDARY: Verified Outcome Rates

SUPPORTED+SOUND rates for pv2 vs v1 on all 40 tasks. Confirms
allocation choices translate to actual outcomes. Resource check:
verified-success latency/cost within 2.0x of v1 (same gate as v2.2).

### 5.6 Interpretation Table (Preregistered)

| Outcome | Interpretation |
|---|---|
| H₂ high accuracy + H₁ discriminates | Lexical-dependence explanation substantially weakened; mechanical rules operate on effects |
| H₂ low accuracy | D-series advantage depended on lexical regime; boundary identified |
| H₂ high but H₁ fails | Rules fire broadly; not effect-sensitive (unexpected given rule semantics) |
| pv2 INCOHERENT / regresses on research | Overgeneralization of parallel penalty |

**A failure is diagnostically valuable.** Do NOT retrain pv2 on OOD-1.

## 6. Decision Decomposition (Frozen Ledger Schema)

For every (task_id, policy) allocation decision, record:

```
task_id
raw_text_hash            # SHA256 of goal text
mechanical_features_hash # SHA256 of canonical extract_features(task) dict
v1_base_scores           # {strategy: score} from score_strategies(goal_features(goal))
pv2_adjustments          # {strategy: delta} from resolve_rules (0.0 if no rule fires)
pv2_final_scores         # {strategy: base + adjustment} over feasible set
v1_choice                # argmax of v1_base_scores
pv2_choice               # argmax of pv2_final_scores
decision_delta           # v1_choice != pv2_choice
fired_rules              # [rule_id, ...] that contributed adjustments
```

This makes the causal attribution inspectable: for any task where pv2
differs from v1, the ledger shows exactly which mechanical rule fired
and how large its adjustment was relative to the lexical base.

For L-pairs, the audit must show:
`mechanical_features_hash_A == mechanical_features_hash_B`
`raw_text_hash_A != raw_text_hash_B`

For M-pairs:
`mechanical_features_hash_A != mechanical_features_hash_B`
`raw_text_hash_A ≈ raw_text_hash_B` (shared template)

## 7. Claims Discipline

Permitted (if supported):
- "Frozen pv2 retained its task-allocation advantage on the preregistered
  production-OOD task set despite removal of the lexical triggers present
  in the D-series production tasks."
- "Mechanical-contrast pairs showed pv2's allocation decision varied with
  mechanically extracted task effects despite shared lexical parallel
  triggers."

Not permitted:
- Broad claims about "general intelligence," "universal abstraction,"
  or "understanding task effects."
- Pooling all 40 tasks into a single headline rate (they test different
  propositions).
- Any claim that M-pair discrimination demonstrates abstraction
  discovery (it demonstrates mechanical-feature conditioning of an
  already-encoded rule).

## 8. What OOD-1 Does NOT Do

- No promotion decision.
- No learner update (pv2 frozen; no learn step loads `phase == "ood-1"`).
- No threshold modifications.
- No changes to D-series evidence (closed chapter).
- No pv2 retraining on OOD-1 outcomes, even on failure.

## 9. Success Criterion

OOD-1 succeeds iff it decisively weakens or confirms the
lexical-dependence explanation for D-series gains — i.e., H₂ is
clearly supported or clearly refuted on the preregistered
production-OOD set, with the decision decomposition showing the
causal path. An inconclusive result due to task design flaws is an
experiment failure, not a neutral outcome.
