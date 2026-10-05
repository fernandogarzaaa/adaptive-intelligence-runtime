# Preregistration: D-Series v2 (D1 -> Learning -> D2 -> Learning -> D3)

**Experiment ID:** dseries-v2
**Protocol version:** v2.1 (amended; see Section 11)
**Status:** PREREGISTERED v2.1. A v2.0 pilot D1 (36 runs) was executed,
quarantined as METHODOLOGICAL_PILOT, and permanently excluded from
hypothesis testing. No runs have been executed under protocol v2.1.
**Design document:** docs/LEARNING_REDESIGN.md v2 (implementation contract)
**Learner:** src/air/learning_v2/ as implemented at the freeze commit.
**Learning schema:** learning-schema/v2.1

## 0. What this experiment tests

398ce14 established that the redesigned learner can recover a known
allocation principle under controlled conditions (shakedown PASS).
It did not establish H3. This experiment tests Phase 5:

**Can the verdict-aware, feature-conditioned learner discover a
useful task-conditioned organization policy from verified
experience and generalize it to unseen tasks?**

The critical result is not "D3 accuracy increased." It is whether
the learned organizational rule improves verified outcomes on
previously unseen task distributions without violating resource,
safety, or class-specific non-inferiority constraints.

## 1. Frozen code

- Product code (including `src/air/learning_v2/` and
  `src/air/allocation/`) is frozen at the v2.1 freeze commit
  (see Section 11), verified clean.
- The v2.0 freeze was 398ce14. Protocol v2.1 amends the learning
  schema (product change, versioned); the freeze was restarted per
  the "no small fix exception" rule.
- The experiment harness (`src/air/experiments/v2harness.py`,
  `src/air/experiments/tasks/v2/`) is experiment code. Freeze
  criterion, mechanically checkable: `git diff <freeze> --stat`
  shows **only additions** for experiment code, and the product
  delta is exactly the versioned v2.1 schema change.
- Any further bug fix becomes a new protocol version and restarts
  the freeze. There is no "small fix" exception.

## 2. Task sets (frozen, hashed)

Five disjoint sets under `src/air/experiments/tasks/v2/`.
No `kind` field exists in any task: the extractor derives class-A
features mechanically from operations/effects/artifacts only.

| set | n | role | sha256 (first 12) |
|---|---|---|---|
| d1 | 12 | training matrix (task x strategy) | f9ccf0e98d48 |
| s1 | 8 | sealed promotion pool 1 | c1814edba2e2 |
| d2 | 12 | confirmation under active policy | e41d4ded33aa |
| s2 | 8 | sealed promotion pool 2 | cf480694f1c1 |
| d3 | 12 | held-out generalization | a32eeaaff97c |

Full hashes in `SHA256SUM`. No adding/removing tasks after seeing
outcomes. No feature-schema changes.

**v2.1 task fixes (pilot findings, Section 11):** claim effect `read`
-> canonical `observe` (was ungroundable: no tool emits `read`);
`shell.exec` args `{"command": ...}` -> `{"argv": ["sh", "-c", ...]}`
(the tool requires `argv`). Both are task-definition bugs, not
product changes. The v2.0 task hashes are preserved in the pilot
quarantine record.

Goal-wording design (preregistered): goal strings deliberately mix
the v1 allocator's lexical parallel-triggers ("three",
"multiple", "several", "each") across production tasks (where the
lexical rule misfires) and research tasks (where parallel is
legitimate). This is the phenomenon under study: the parent's
lexical rule vs the structural rule the learner must discover.
The learner never sees goal text; only the parent's frozen
scoring does.

## 3. Organization space and policies

**Organization space** (scope decision, documented): three pinned
strategies, each producing a real organization via the real
allocator with a pinned strategy:
- `single_agent` -> 1 specialist (phases: all)
- `hierarchical_agents` -> planner/researcher/coder/synthesizer
- `parallel_agents` -> researchers + synthesizer (no produce role)

OrganizationProperties per strategy are computed from the real
plan specs: roles from agent_specs; capabilities derived
mechanically from role phases (prepare->{READ}, produce->{WRITE,
EXECUTE}, verify->{READ}) via the frozen ROLE_PHASES;
topology from the plan.

**Parent policy (v1):** `argmax` over the three strategies of the
real `score_strategies(goal_features(goal), agent_budget=8)`,
deterministic tiebreak by strategy name. This is v1's dynamic
choice restricted to the experiment's organization space: the
actual current AIR behavior under test, not a strawman.

**Candidate policy:** base scores = parent's real scores;
apply v2 AllocationRules via `resolve_rules`; hard requirements
filter organizations for feasibility BEFORE scoring
(feasibility-before-topology); choose argmax of
base + adjustments; deterministic tiebreak by strategy name.

**Incoherence:** if hard requirements eliminate every
organization, the choice is INCOHERENT. This is recorded as a
distinct ledger outcome (not silently treated as ordinary rule
failure): it tests whether the learned policy language remains
composable under constraint interaction. In an intervention, an
incoherent candidate choice counts as candidate failure on that
task.

## 4. Feature extraction (mechanical, frozen)

From the task dict only (`kind` does not exist; goal text is
never read by the learner):

- requires_output_artifact = len(artifacts) > 0
- output_artifact_count = len(artifacts)
- has_named_output_targets = len(artifacts) > 0
- requested_write_effect = "create" in effects
- requested_read_effect = any op.tool == "fs.read"
- requested_execute_effect = "execute" in effects
- independent_work_unit_count = #produce-phase operations
- dependency_depth = #distinct phases - 1
- multi_step = len(operations) > 1

## 5. Learner configuration (pinned)

All values are the frozen v2 defaults; preregistered here so any
deviation is detectable:

- attribution: MIN_TREATED=2, MIN_UNTREATED=2, MIN_EFFECT=0.2
- robustness: same minimums per sub-stratum; contradiction =
  opposite sign or |sub-differential| < MIN_EFFECT/2
- generation: STRATEGY_DELTA=0.1, STRENGTH_SMOOTHING_K=4,
  evidence-strength/v1
- No LLM anywhere. Enumeration only.

## 6. Protocol

```
Frozen AIR, Policy v1 (parent)
        |
        v
  D1: 12 tasks x 3 strategies = 36 runs (training matrix)
        |  verified experiences (LearningEvidence)
        v
  Learner -> candidate1  (or NO_CANDIDATE: legitimate, stop learning)
        |
        v
  S1: sealed intervention, 8 tasks x {parent, candidate1} = 16 runs
        |  paired outcomes; McNemar/Fisher; non-inferiority; resources
        v
  Promotion 1 -> PROMOTE (PolicyVersion) | REJECT (parent stays)
        |
        v
  D2: 12 tasks x active policy = 12 runs (confirmation)
        |  new verified experiences
        v
  Learner on D1+D2 -> candidate2 (parent_version = active)
        |
        v
  S2: sealed intervention, 8 tasks x {active, candidate2} = 16 runs
        |
        v
  Promotion 2 -> final active policy
        |
        v
  D3: 12 tasks x {final active, parent} = 24 runs (held-out)
```

Total: 104 runs. Budgets per run (held constant, same as v1):
tool calls 24, wall time 120s, agents 8, tokens 64000, cost $5.
Capability grants READ+WRITE+EXECUTE for every task agent
(same disclosed experiment configuration as v1).
Seeds: sha256(exp_id|phase|task_id|policy), recorded per run.

Execution: real AgentRuntime runs with the frozen scripted
behaviors (competence identical across policies by
construction); real tool calls; real evidence-grounded
evaluation (suite_exp_v1 v1.0.0); real assurance probes.
Behaviors receive no policy label (blindness).

## 7. Evaluation (frozen)

- **Intervention:** parent vs candidate executed on the same
  sealed tasks, same environment, same budgets, same evaluator
  (suite_exp_v1 v1.0.0), same assurance probes. Paired outcomes.
- **Decision delta:** tasks where parent/candidate choices
  differ. Empty delta -> VACUOUS -> promotion refused.
- **Improvement:** exact one-sided McNemar on paired binary
  verified outcomes, alpha=0.05, required on >=1 class.
- **Non-inferiority:** per class, candidate Wilson lower bound
  >= parent rate - 0.1. Classes (mechanical from features):
  "multi" (artifacts>1), "single" (1 artifact), "exec",
  "read" (read-only).
- **Multiplicity:** "multi" is the designated primary class
  (the D1 failure lived there); remaining classes are
  non-inferiority guardrails, not separate superiority tests.
- **Safety:** zero tolerance. No task carries safety invariants;
  any recorded safety violation rejects.
- **Resources:** verified success must improve (per above);
  cost and latency non-inferior within +25% bounds.
- **D3 analysis:** verified rates for final-active vs parent on
  D3, per-class non-inferiority, resource comparison. The
  defensible claim, if supported: "Under the preregistered
  protocol, AIR learned a task-conditioned cognitive-allocation
  policy from verified experience that improved held-out task
  performance while satisfying the predefined resource and
  safety constraints." Nothing broader.

## 8. Promotion (frozen)

A candidate becomes active ONLY by surviving the sealed
intervention. Promotion additionally requires mechanical
policy assurance = SOUND:
1. all rule hashes recompute correctly
2. all rules cite evidence ids present in the training ledger
3. all rule features in the class-A schema
4. candidate hash matches
5. evaluation verdict PASS (re-checked)

PROMOTE produces an immutable PolicyVersionV2; the allocator
side activates it (unchanged from the v2 isolation design).
REJECT keeps the parent active. Rejections are permanent
ledger records.

## 9. Failure modes (all legitimate results)

1. **No learning:** D1 -> no admissible candidate. The evidence
   was not sufficient. Parent remains active; D2/D3 run under
   parent as a no-learning baseline.
2. **Candidate rejected:** the intervention fails promotion.
   Demonstrates the assurance boundary functioning.
3. **Promotes but does not generalize:** D2 improves, D3 does
   not. The learner found an experience-fitting rule, not a
   generalizable principle. Particularly interesting.
4. **D3 improves:** strongest result, but the claim stays
   narrow (section 7).
5. **D3 improves only via resource explosion:** fails the
   thesis. The resource gate exists to catch exactly this:
   an expensive workaround is not efficient cognitive
   organization.
6. **Incoherence:** candidate rules compose into unsatisfiable
   requirements. Recorded distinctly (section 3); a finding
   about the policy language's composability, not a mere
   rule failure.

## 10. Contamination controls

- Task sets frozen and hashed before D1 (section 2).
- Policy blindness: behaviors take no policy label.
- Identical evaluation, assurance, budgets, grants.
- Seeds recorded; failed runs retained.
- Append-only experiment ledger (JSONL, seq-numbered).
- The learner sees only LearningEvidence (verdicts +
  features); never goal text, task ids, or set membership.
- S1/S2 sealed: never in any training set. D3 never trained on.
- Fixed task order within each phase.

## 11. What would falsify H3 here

- The learner produces no candidate from the D1 matrix, or
- its candidates do not survive the sealed interventions, or
- a promoted policy does not beat the parent on D3 under the
  preregistered bar.

Any of these bounds the approach without invalidating the
shakedown result (Phase 4 stands on its own).

## 12. Protocol amendment v2.1 (pilot findings)

### What the v2.0 pilot established

A 36-run D1 pilot was executed under protocol v2.0 and quarantined
(`pilot_quarantine` record in `~/workspace/air-experiments/dseries-v2/`):

- 36 runs: 10 SUPPORTED, 26 INCONCLUSIVE, 0 FALSIFIED.
- learn-1: NO_CANDIDATE (0 rules).
- Key discovery: the evaluation protocol has no negative-outcome
  representation for organizational incapability when execution
  completes but the requested outcome cannot be produced. The suite
  maps this to INCONCLUSIVE (score 2/6: h1/h2 pass), which the v2.0
  eligibility correctly treats as nondirectional. The D1 matrix's
  primary contrast therefore yields zero negative evidence.
- This is an instrumentation/measurement problem, not evidence that
  the learner failed. The pilot is preserved as methodological
  evidence and permanently excluded from hypothesis testing.

### Amendment honesty

The D1 pilot exposed a previously unspecified measurement failure.
The protocol was amended before the confirmatory D1 restart, with
the original pilot permanently excluded from hypothesis testing.
This changes the observation model after seeing pilot data; that is
a real researcher degree of freedom and is documented here rather
than minimized. The amendment does not encode the desired rule
("production -> WRITE"); it makes failure to satisfy an explicitly
requested effect observable as negative evidence. The learner still
has to discover the organizational relationship.

### A. Task-definition fixes (not product changes)

1. Claim effect `read` -> canonical `observe`. No tool emits
   `read`; `fs.read` emits `observe`. The v2.0 tasks were
   ungroundable on read claims.
2. `shell.exec` args `{"command": ...}` -> `{"argv": ["sh","-c",...]}`.
   The tool requires `argv`; v2.0 exec tasks failed at the tool
   layer.

### B. Outcome-failure interpretation (narrow)

A deterministic learning-outcome interpretation, separate from the
evaluator verdict:

```
Evaluator verdict
  |-- SUPPORTED + SOUND ------> POSITIVE
  |-- FALSIFIED + SOUND ------> NEGATIVE
  |-- INCONCLUSIVE -----------> normally EXCLUDED
  |      but if ALL hold:
  |      |-- required outcome explicitly specified
  |      |   (non-empty required effects AND targets, from task claims)
  |      |-- execution evidence (run completed, >=1 agent completed)
  |      |-- every required target positively checked
  |      |-- required effect not satisfied (mechanical:
  |          required effects not subset of observed effects,
  |          or a required target absent from the run workspace)
  |-- otherwise --------------> EXCLUDED
```

The record preserves both layers:

```
evaluator_verdict = INCONCLUSIVE        # never rewritten
learning_outcome  = NEGATIVE_OUTCOME
outcome_basis     = REQUIRED_EFFECT_NOT_SATISFIED
failure_certificate = {required_effects, required_targets,
                       observed_effects, targets_satisfied, ...}
```

The learner consumes `learning_outcome`, not raw evaluator labels.

### C. Invariant (protocol v2.1)

**An evaluator INCONCLUSIVE verdict may generate negative learning
evidence only through a separately defined, deterministic
outcome-failure certificate; absence of evidence, absence of
declared outcomes, or evaluator uncertainty alone can never
constitute negative learning evidence.**

Enforced in code: `interpret_learning_outcome` (contracts.py) has no
path from INCONCLUSIVE to NEGATIVE_OUTCOME except the certified one;
`LearningEvidence.__post_init__` refuses NEGATIVE_OUTCOME without
basis REQUIRED_EFFECT_NOT_SATISFIED and a non-empty certificate.

### D. Schema version

- `LEARNING_SCHEMA_VERSION = "learning-schema/v2.1"`.
- `LearningEvidence` carries `learning_outcome`, `outcome_basis`,
  `failure_certificate`, `learning_schema_version`.
- `eligibility_for` derives from the interpreted outcome;
  NEGATIVE_OUTCOME is directional negative.

### E. Pilot exclusion

The v2.0 D1 (36 runs) is quarantined as METHODOLOGICAL_PILOT,
`eligible_for_learning: false`. Preserved: original task hashes,
code/policy hashes, all run records, evaluator/assurance versions,
the 26 INCONCLUSIVE verdicts, the task-definition bugs, diagnosis,
and this amendment. The confirmatory D1 runs in a clean experiment
directory (`AIR_EXP_ID=dseries-v2-1`).

### F. Fresh D1

The entire D1 restarts from clean state under v2.1. No tuning based
on the restarted D1. learn-1 must produce a properly interpretable
learning corpus before any sealed intervention.
