# Learning Engine Redesign: Formal Learning Problem and Policy Representation

**Status: design revision, not implementation.** Written 2026-10-04,
after the D-series falsified the v1 learning mechanism. No code
changes follow from this document until it is reviewed. The rule
this document exists to enforce: do not build a learner whose
hypothesis space is incapable of representing the phenomenon under
study — the exact mistake the D-series exposed.

## 1. Research state

- **H1**: dynamic cognitive organization has benefits sufficient to
  overcome its coordination cost. **Not demonstrated.** The
  baseline measured a coordination tax (C 65/80 vs A/B 75/80,
  p=0.0294).
- **H2**: AIR can learn improved organization policies from
  verified experience. **Implementation falsified; underlying
  hypothesis unresolved.** The v1 engine aggregates run
  completion, never verification verdicts; its policy format
  (global strategy boosts) cannot represent task-kind × strategy
  interactions; its policy evaluation is at ceiling.
- **H3**: a sufficiently expressive, verdict-aware learning
  mechanism can discover task-conditioned organization policies.
  **Untested.** This document specifies the mechanism that would
  test it.

## 2. The formal learning problem

**Input.** A set of verified-outcome records (section 3), each
binding task features, the allocation that was chosen, the
organization that executed, the evidence produced, and the
evaluation + assurance verdicts on the claimed outcome.

**Hypothesis space.** Finite sets of `AllocationRule`
(section 5): conditional rules mapping task features to
organization requirements and strategy-scoring adjustments. The
space is fixed and inspectable; learning selects within it, never
outside it.

**Objective.** Maximize the verified-success rate on *unseen*
tasks (generalization), as measured by discriminating evaluation
(section 8). Fitting the training experiences is necessary but
never sufficient; a rule set that memorizes D1 without
transferring to D3 is a failure, detectable by construction.

**Constraints.**
- No LLM judge anywhere in the loop. Every artifact is
  mechanically inspectable: every rule cites its evidence
  experiences, every promotion cites its evaluation and
  assurance records.
- Only promotion-gated policy changes may influence allocation.
  The learner proposes; the gates dispose.
- The learner never sees task IDs, condition labels, phase
  labels, or any field correlated with the intended answer.

## 3. Input contract: the VerifiedOutcome record

The v1 engine consumed raw run records (completion aggregates).
The redesigned engine consumes only this record. A run that does
not produce one is invisible to learning.

```
VerifiedOutcome
├── task_features      # fixed mechanical schema (section 6)
├── allocation         # strategy chosen, scores at decision time,
│                      # organization proposed (roles, capabilities)
├── execution          # what actually ran (agents, tool calls)
├── evidence           # evidence objects cited for the outcome
├── evaluation
│   ├── verdict        # SUPPORTED | FALSIFIED | INCONCLUSIVE | INVALID | UNTESTED
│   ├── metrics        # per-check results
│   └── evaluator_version
└── assurance
    ├── verdict        # SOUND | INCONCLUSIVE | UNSOUND (or NOT_RUN)
    └── assurance_version
```

## 4. Learning eligibility

The D1 pathology was inferring positive utility from a completed
but INCONCLUSIVE run. Eligibility is now a function of the
verdicts, and it is the same epistemic hierarchy the rest of AIR
uses: execution → evidence → evaluation → assurance → learning
eligibility.

| evaluation | assurance | eligibility |
|---|---|---|
| SUPPORTED | SOUND | **positive** learning evidence |
| SUPPORTED | not SOUND (INCONCLUSIVE, UNSOUND, NOT_RUN) | excluded from directional updates (verified but unassured; counts toward coverage statistics only) |
| FALSIFIED | any | **negative** learning evidence |
| INCONCLUSIVE | any | **no directional reward** (excluded from updates) |
| INVALID | any | **excluded** entirely |
| UNTESTED | any | **excluded** entirely |

A rule's supporting evidence is the set of eligible records it
was derived from; a rule with no eligible evidence cannot exist.

## 5. Policy representation: AllocationRule

Replaces global strategy boosts, which are incapable of
expressing "parallel research is good for research tasks but bad
for artifact-producing tasks without a production capability."

```
AllocationRule
├── id
├── WHEN: predicate            # conjunction of atomic conditions
│                              # over the fixed task-feature schema
├── THEN: action                # one of:
│                              #   require_capability(CapabilityClass)
│                              #   require_role(role)
│                              #   boost_strategy(Strategy, delta)
│                              #   penalize_strategy(Strategy, delta)
├── WITH:
│   ├── evidence               # VerifiedOutcome ids (eligible only)
│   ├── confidence             # mechanical: function of the
│   │                          #   verified-rate differential magnitude
│   │                          #   and eligible evidence count.
│   │                          #   Formula versioned; never a vibe.
│   ├── scope                  # task-feature strata the rule was
│   │                          #   validated on; the rule does not
│   │                          #   apply outside its scope
│   ├── constraints            # hard limits, e.g. max |delta|,
│   │                          #   "may not override safety constraints",
│   │                          #   "may not remove verifier from
│   │                          #   production tasks"
│   └── priority               # integer; resolves rule conflicts
```

Semantics. At allocation time, for a task with features F:
applicable rules are those whose WHEN holds and whose scope
covers F. Constraints are vetoes (a rule violating a constraint
is inert, loudly). `require_*` actions are hard requirements:
organizations not satisfying them are removed from consideration.
Scoring actions adjust the existing `score_strategies` outputs.
Conflicts between applicable rules resolve by priority, then by
narrower scope, deterministically. The full decision (applicable
rules, conflicts, resolution) is recorded on the allocation
record — the allocator's reasoning is itself auditable.

Example (illustrative, not hardcoded):

```
IF requires_artifact = true AND artifact_count > 1
THEN require_capability(WRITE)
WITH evidence=[...], confidence=0.81, scope={write_multi},
     constraints={max_boost: 0.5}, priority=10
```

The research question is whether AIR can *discover* such rules.
They must never be hand-written into the policy.

## 6. Task features: mechanical, fixed, versioned

Predicates range over a fixed feature schema computed by
deterministic, versioned extractor functions of the task
definition (goal text, declared task fields). Never hand-labeled
per task after results are seen; never derived from the task ID
or any answer-correlated label. Initial schema (v1):

- `names_production_verb`: bool (deterministic verb list:
  write/create/build/generate/produce — versioned, inspectable)
- `artifact_mentions`: int (count of path-like tokens)
- `involves_reading`: bool, `involves_execution`: bool
- `declared_kind`: the task record's kind field (write_multi,
  read_write, …) — a task-specification property, not a label
- `multi_step`: bool (more than one operation in the task spec)

The extractor is itself a versioned artifact; changing it is a
protocol change, not a tuning knob.

## 7. The pipeline

```
EXPERIENCE (VerifiedOutcome records)
    │
    ▼
┌──────────────────┐
│ Verified Outcome │  assemble records; drop ineligible runs
│ Extraction       │  (section 4); output is the learning dataset
└────────┬─────────┘
         │
         ▼
┌─────────────────┐
│ Experience      │  contrastive: for each organizational
│ Attribution     │  property P in the fixed property schema
│                 │  (strategy used, role set, capability coverage),
│                 │  partition eligible records by task-feature
│                 │  strata and compute the verified-rate
│                 │  differential with vs without P, with minimum
│                 │  evidence counts. Report differentials that
│                 │  persist under conditioning, plus the
│                 │  confounders checked.
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Hypothesis      │  exhaustive-but-pruned enumeration over the
│ Generator       │  property schema × feature strata:
│                 │  differentials above threshold and evidence
│                 │  minimum become candidate AllocationRules
│                 │  with provisional confidence.
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Policy Candidate│  candidate = parent policy + candidate rules;
│ Generator       │  rule conflicts resolved per section 5;
│                 │  every rule carries its evidence.
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Discriminating  │  evaluate parent vs candidate on a set built
│ Evaluation      │  to discriminate (section 8)
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

The critical addition over v1 is Experience Attribution: the
learner asks "given the task's characteristics, what
organizational property distinguishes verified successes from
verified failures?" — not "which strategy had successful runs?"

## 8. Discriminating evaluation

Fixes the ceiling problem. Construction:

1. Compute the *decision delta*: the task-feature regions where
   parent and candidate policies choose different organizations.
   If the delta is empty, evaluation is vacuous and promotion
   is refused outright (never "no difference detected, promote
   anyway").
2. The discriminating set covers the delta regions plus a
   regression set (regions where both agree, to catch
   regressions). It must include, at minimum, one class per
   qualitatively distinct allocation regime:
   - research task → parallel research good
   - single artifact → direct/single producer good
   - multi-artifact → producer (+ verifier) good
   - multi-file research + production → researcher(s) +
     producer (+ verifier) good
   - production task with misleading lexical cues → producer
     required despite wording good (the D3 "three" shortcut:
     the evaluation must contain the confounder class, or the
     learner is never tested against shortcut learning)

Promotion criterion (preregistered): the candidate must not
regress on any class and must improve on at least one class, at
a preregistered minimum effect size and statistical bar
(Fisher's exact on verified outcomes, preregistered alpha).
INCONCLUSIVE/INVALID outcomes are bucketed explicitly per the
contamination rules; they never count as successes.

## 9. Mechanical inspectability

- Every rule cites eligible VerifiedOutcome ids. No evidence,
  no rule.
- Every promotion cites the discriminating evaluation id and
  the assurance id.
- Confidence is a versioned formula of differential magnitude
  and evidence count, recomputable by hand.
- The allocator records applicable rules, conflicts, and
  resolutions on every allocation record.
- An independent party with the ledgers can re-derive every
  rule, every promotion, and every rejection.

## 10. Learning Engine Shakedown (gated milestone)

Before another D-series, the redesigned learner must pass a
shakedown on synthetic cases where the correct rule is known
but hidden from the learner:

| task type | strategy | verified outcome |
|---|---|---|
| research | parallel | success |
| research | direct | success |
| production | parallel | failure |
| production | direct | success |
| mixed | parallel | failure |
| mixed | hierarchical | success |

Pass criterion: the learner recovers a conditional policy
distinguishing production from research (a rule whose scope and
predicate capture the production requirement, not a global
penalty on parallel).

Then confounders, to test causal vs lexical learning:

- "three researchers" (research, parallel good)
- "three files" (production, producer required)
- "three sources" (research, parallel good)
- "three sections" (production, producer required)

The learner must acquire the structural feature (production
requirement → production capability), not the word "three".
Only after passing both stages does another D-series run.

## 11. What would falsify H3

- The redesigned learner passes the shakedown but cannot
  recover a useful rule from real D1-style data.
- Its rules do not survive D3 generalization.
- No mechanically-inspectable learner (within this hypothesis
  space) beats the lexical-shortcut problem — that would bound
  the approach and force the question of whether semantic
  hypothesis generation (behind assurance, adversarially
  evaluated) is necessary after all.

## 12. Non-goals

- No LLM judge, no LLM hypothesis generator in this revision.
  An LLM may eventually become a hypothesis *generator*, but it
  must never be the authority that declares its own hypothesis
  successful; that stays with discriminating evaluation +
  assurance.
- No change to the evidence, evaluation, assurance, or
  epistemic layers. This redesign consumes their outputs; it
  does not redefine them.
- No retrospective re-explanation of the D-series. The v1
  results stand as the falsification record.
