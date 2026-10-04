# Learning Engine Redesign: Formal Learning Problem and Policy Representation

**Status: design revision v2, amended per review 2026-10-04.**
Implementation of the learner and the synthetic shakedown is
approved **after** these amendments. No D-series until the
shakedown passes and the promotion protocol is sealed. No code
changes follow from this document until then.

Changelog from v1: declared_kind removed from the default
hypothesis space (three-class feature schema); mechanical
task-effect features added; eligibility matrix fixed
(FALSIFIED+UNSOUND excluded); overlap requirements added;
evaluation pool sealed with one-candidate discipline;
parent-vs-candidate intervention runs required; non-inferiority
margins replace raw "no regression"; multiplicity and paired
tests specified; resource efficiency in the promotion gate;
confidence renamed to evidence_strength; typed canonical scope
and rule hashing; feasibility-before-topology for hard
requirements; attribution defined as conditional association,
not causation; shakedown uses matched minimal pairs and
behavioral equivalence.

## 1. Research state

- **H1**: dynamic cognitive organization has benefits sufficient to
  overcome its coordination cost. **Not demonstrated.** The
  baseline measured a coordination tax (C 65/80 vs A/B 75/80,
  p=0.0294).
- **H2**: AIR can learn improved organization policies from
  verified experience. **Implementation falsified; underlying
  hypothesis unresolved.**
- **H3**: a sufficiently expressive, verdict-aware learning
  mechanism can discover task-conditioned organization policies.
  **Untested.** This document specifies the mechanism that would
  test it.

## 2. The formal learning problem

**Input.** Verified-outcome records (section 3): task features,
allocation chosen, organization executed, evidence produced,
evaluation + assurance verdicts.

**Hypothesis space.** Finite sets of `AllocationRule`
(section 5): conditional rules mapping task features to
organization requirements and strategy-scoring adjustments. The
space is fixed and inspectable; learning selects within it,
never outside it.

**Objective.** Maximize the verified-success rate on *unseen*
tasks (generalization), as measured by discriminating evaluation
(section 8). Fitting the training experiences is necessary but
never sufficient.

**Constraints.**
- No LLM judge anywhere. Every artifact is mechanically
  inspectable: every rule cites its evidence experiences, every
  promotion cites its evaluation and assurance records.
- Only promotion-gated policy changes may influence allocation.
- The learner never sees task IDs, condition labels, phase
  labels, or any field correlated with the intended answer.

## 3. Input contract: the VerifiedOutcome record

```
VerifiedOutcome
├── task_features      # three-class schema (section 6)
├── allocation         # strategy chosen, scores at decision time,
│                      # organization proposed (roles, capabilities,
│                      # topology)
├── execution          # what actually ran (agents, tool calls)
├── evidence           # evidence objects cited for the outcome
├── evaluation
│   ├── verdict        # SUPPORTED | FALSIFIED | INCONCLUSIVE
│   │                  # | INVALID | UNTESTED
│   ├── metrics
│   └── evaluator_version
└── assurance
    ├── verdict        # SOUND | INCONCLUSIVE | UNSOUND | NOT_RUN
    └── assurance_version
```

## 4. Learning eligibility

The D1 pathology was inferring positive utility from a completed
but INCONCLUSIVE run. A subtler pathology would be learning from
an evaluator's negative verdict that assurance itself distrusts:
"this evaluator cannot be trusted" must never coexist with "let's
nevertheless learn from its verdict." Eligibility binds the
assurance verdict too:

| evaluation | assurance | eligibility |
|---|---|---|
| SUPPORTED | SOUND | **positive** learning evidence |
| SUPPORTED | not SOUND | **excluded** |
| FALSIFIED | SOUND | **negative** learning evidence |
| FALSIFIED | not SOUND | **excluded** |
| INCONCLUSIVE | any | **nondirectional** (excluded from updates) |
| INVALID / UNTESTED | any | **excluded** |

A rule's supporting evidence is the set of eligible records it
was derived from; a rule with no eligible evidence cannot exist.
This is a blocking semantic: the earlier draft allowed
FALSIFIED+UNSOUND as negative evidence, which violates the
evaluator-independence principle the assurance architecture was
built to protect.

## 5. Policy representation: AllocationRule

```
AllocationRule
├── id
├── WHEN: predicate            # conjunction of atomic conditions
│                              # over the admissible feature schema
├── THEN: action                # one of:
│                              #   require_capability(CapabilityClass)
│                              #   require_role(role)
│                              #   boost_strategy(Strategy, delta)
│                              #   penalize_strategy(Strategy, delta)
├── WITH:
│   ├── evidence               # eligible VerifiedOutcome ids
│   ├── evidence_strength      # deterministic, versioned formula
│   │                          #   of association magnitude and
│   │                          #   eligible evidence count.
│   │                          #   NOT statistical confidence.
│   ├── uncertainty            # kept separate: effect estimate,
│   │                          #   confidence interval, sample size,
│   │                          #   overlap statistics, p-value
│   ├── scope                  # typed canonical predicate +
│   │                          #   feature_schema_version; the rule
│   │                          #   does not apply outside its scope
│   ├── constraints            # hard limits, e.g. max |delta|,
│   │                          #   "may not override safety
│   │                          #   constraints"
│   └── priority               # integer; resolves rule conflicts
├── rule_schema_version
├── feature_schema_version
├── organization_schema_version
├── policy_semantics_version
└── rule_hash                  # hash of the canonical form;
                               # reproducible, drift-proof
```

Semantics. For a task with features F, applicable rules are
those whose WHEN holds and whose scope covers F. Constraints are
vetoes. `require_*` actions are **hard requirements evaluated
during strategy feasibility, before topology commitment**:

```
strategy
  → feasible organization(s)
  → apply hard requirements (veto violators)
  → score surviving organizations
  → choose
```

NOT: choose topology first, discover the violation afterward.
A requirement that fires after the organization is built has
merely moved the D1 failure one stage later. Scoring actions
adjust the existing `score_strategies` outputs for survivors.
Conflicts resolve deterministically: constraints → hard
requirements → scoring adjustments → priority → narrower scope.
The allocator records applicable rules, conflicts, and
resolutions on every allocation record.

The representation can express the observed failure
(`WHEN task requires multiple output artifacts THEN
require_role(producer)`) while still permitting (`WHEN task
requires research AND work units are independent THEN
boost_strategy(parallel_agents, +δ)`) — task-conditioned
organization, not global strategy preference.

## 6. Task features: three classes, mechanical extraction

Predicates range over a fixed, versioned feature schema computed
by deterministic extractor functions of the task definition.
The schema is split because not all "features" are
epistemically equal:

**A. Runtime-observable task features** (admissible by
default — available naturally before allocation):

- `requires_output_artifact`: bool
- `output_artifact_count`: int
- `has_named_output_targets`: bool
- `requested_write_effect`, `requested_read_effect`,
  `requested_execute_effect`: bool
- `independent_work_unit_count`: int
- `dependency_depth`: int
- `multi_step`: bool

The critical distinction: *"write" appears in the sentence*
versus *the task specification requires a write effect*. The
schema encodes the second — mechanically derived from the task
specification's declared operations and targets, not from
lexical proxies. The intended learned rule is then
"output-producing task → organization containing production
capability," a far better research object than "task contains
the word write → producer."

**B. User/task-author declarations** (admissible only when they
genuinely exist before execution): `declared_kind`,
`declared_constraints`, `declared_output_type`.

**C. Benchmark/evaluation labels** (never admissible to the
learner): `production_task`, `correct_strategy`,
`expected_role`, `condition`, `phase`, and — for our
benchmarks — the task record's `kind` field (`write_multi`,
...), which was assigned by the experiment designer and is
benchmark metadata, not a pre-allocation observable.
`declared_kind` is therefore **excluded from the default H3
hypothesis space**. A production deployment with genuine
user-declared kinds may admit class B; our task sets may not.
Allowing `WHEN declared_kind = write_multi THEN
require_role(producer)` would let the learner "succeed" without
learning anything from outcomes, silently redefining H3.

## 7. The pipeline

```
EXPERIENCE (VerifiedOutcome records)
    │
    ▼
┌──────────────────┐
│ Verified Outcome │  assemble records; apply eligibility
│ Extraction       │  (section 4); the learning dataset
└────────┬─────────┘
         │
         ▼
┌─────────────────┐
│ Experience      │  CONDITIONAL ASSOCIATIONS, not causal
│ Attribution     │  attribution (section 8)
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Hypothesis      │  pruned enumeration over the property
│ Generator       │  schema × feature strata → candidate rules
│                 │  with evidence_strength
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Policy Candidate│  ONE candidate selected; conflicts resolved;
│ Generator       │  every rule carries its evidence
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Sealed          │  parent vs candidate INTERVENTION runs on a
│ Evaluation      │  sealed pool (section 9)
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Assurance       │  SOUND required for promotion eligibility
└────────┬────────┘
         │
    ┌────┴────┐
    ▼         ▼
PROMOTE     REJECT
```

Epistemology of the loop, kept clean:

```
observed experience
        ↓
conditional association
        ↓
hypothesis
        ↓
intervention
        ↓
measured effect
```

Observational data generates hypotheses; only the intervention
establishes effects. Nothing in the attribution stage is
described in causal language.

## 8. Experience Attribution: associations with overlap requirements

For each organizational property P in the fixed property schema
(strategy used, role set, capability coverage, **topology**),
partition eligible records by task-feature strata and compute
the verified-rate differential with vs without P. A proposed
relationship requires:

- minimum treated support (eligible records with P present)
- minimum untreated support (eligible records with P absent)
- minimum positive and negative support within each side
- feature-stratum overlap between the sides

Thresholds are preregistered constants, not tuned per run.
When overlap does not exist, the attribution outcome is
**INSUFFICIENT_OVERLAP** — a first-class result, never a causal
claim. In particular: P present in 10 failures with zero
untreated observations is hypothesis-generation material, not
evidence that P caused anything. This matters directly for D1,
whose data are one-sided by construction.

Report, per hypothesis: the differential, the support counts,
the confounders conditioned on, and the overlap statistics.

## 9. Sealed discriminating evaluation

Fixes both the ceiling problem and the test-set-as-tuning-set
problem.

1. **Decision delta.** Compute the task-feature regions where
   parent and candidate choose different organizations. If the
   delta is empty, evaluation is vacuous: promotion refused
   outright.
2. **Sealed pool.** The evaluation pool is feature-stratified;
   its outcomes are sealed **before** candidate generation.
   Discipline, strictly enforced:
   training experiences → candidate generation → ONE candidate
   selected → sealed pool → parent vs candidate execution →
   promotion decision. If a candidate fails, a revised
   candidate requires a **new sealed allocation**. Never
   modify-and-rerun against the same holdout; never let
   candidate generation adapt to test outcomes. This is the
   single most important statistical control in the design.
3. **Intervention, not retrospection.** The decisive
   evaluation executes parent and candidate on the same tasks,
   same environment, same budgets, same evaluator, same
   assurance — paired outcomes. Comparing historical
   experiences tagged v1 vs candidate repeats the old
   ceiling-evaluator weakness and is forbidden for the
   promotion decision.
4. **Statistical bar (preregistered).**
   - Performance classes: non-inferiority margins, not raw
     "no regression." The candidate's verified-rate lower
     bound must remain above parent − ε (ε preregistered per
     class), because 10/10→9/10, 50/50→49/50, and 5/5→0/5
     are not the same evidence.
   - Safety/invariant classes: hard zero-tolerance. Any
     violation rejects.
   - Improvement: required on at least one discriminating
     class at the preregistered bar.
   - Multiplicity: preregistered strategy across classes
     (e.g., hierarchical primary-then-secondary, or
     Holm–Bonferroni); the method is frozen before evaluation.
   - Paired holdout → exact McNemar-style paired test on
     binary verified success. Independent holdout → Fisher's
     exact. The choice is frozen before evaluation.
5. **Resource efficiency in the gate.** The baseline showed B
   matching A at 4× the agent cost; a learner that "solves" a
   failure by spawning more agents is a bad adaptive policy.
   Verified success must improve (per the bar above); cost and
   latency must be non-inferior within preregistered bounded
   increases; agent count and tool calls are measured as
   diagnostics in this revision.

## 10. Mechanical inspectability

- Every rule cites eligible VerifiedOutcome ids. No evidence,
  no rule.
- Every promotion cites the sealed evaluation id and the
  assurance id.
- evidence_strength is a versioned deterministic formula,
  recomputable by hand; uncertainty (interval, n, overlap,
  p-value) is reported alongside, never conflated with it.
- The allocator records applicable rules, conflicts, and
  resolutions per allocation.
- An independent party with the ledgers re-derives every rule,
  promotion, and rejection.

## 11. Learning Engine Shakedown (gated milestone)

No D-series until this passes and the promotion protocol is
sealed.

**Stage 1: known-rule recovery.** Synthetic cases where the
correct rule is known but hidden:

| task type | strategy | verified outcome |
|---|---|---|
| research | parallel | success |
| research | direct | success |
| production | parallel | failure |
| production | direct | success |
| mixed | parallel | failure |
| mixed | hierarchical | success |

**Stage 2: matched minimal pairs.** Surface features held
≈ identical while the required effect differs — matched on
word count, the token "three," artifact mentions, step count,
read operations, execution flags:

- Research: "Review three sources and summarize them."
  (parallel good)
- Production: "Create three files containing the requested
  material." (producer required)

plus the confounder set: "three researchers" (research),
"three files" (production), "three sources" (research),
"three sections" (production).

**Pass criterion: behavioral equivalence, not syntax.** For
the prespecified shakedown population, the learned policy must
select production-capable organizations for the production
strata, preserve parallel research behavior, and reject the
lexical confounders — whether it expresses that as
`requires_output AND output_count > 1 → require_role(producer)`
or as `requested_write_effect → require_capability(WRITE)`.
Simplicity and generality are measured separately. A learner
that emits the right string for the wrong reason (e.g.,
`artifact_mentions > 2 → producer`) fails the minimal pairs.

## 12. What would falsify H3

- The redesigned learner passes the shakedown but cannot
  recover a useful rule from real D1-style data.
- Its rules do not survive D3 generalization.
- No mechanically-inspectable learner in this hypothesis
  space beats the lexical-shortcut problem — bounding the
  approach and forcing the question of whether semantic
  hypothesis generation (behind assurance, adversarially
  evaluated) is necessary.

## 13. Non-goals

- No LLM judge, no LLM hypothesis generator in this revision.
- No change to the evidence, evaluation, assurance, or
  epistemic layers. This redesign consumes their outputs.
- No retrospective re-explanation of the D-series results.

## Appendix: the loop

What this design is becoming — a small causal-adaptation loop:

```
                 EXPERIENCE
                     │
                     ▼
             VERIFIED OUTCOME
                     │
                     ▼
          CONDITIONAL ASSOCIATIONS
                     │
                     ▼
              HYPOTHESIS SPACE
                     │
                     ▼
            POLICY CANDIDATE
                     │
             ┌───────┴───────┐
             ▼               ▼
        parent policy    candidate policy
             │               │
             └───────┬───────┘
                     ▼
              SEALED INTERVENTION
                     │
                     ▼
               EVALUATION
                     │
                     ▼
                ASSURANCE
                     │
             ┌───────┴───────┐
             ▼               ▼
           REJECT          PROMOTE
```

The learner never decides what constitutes evidence that the
learner improved. Discriminating intervention + assurance do.
