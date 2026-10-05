# Protocol Amendment v2.2: Powered Promotion Design

**Status:** DRAFT (pending Inan approval, freeze, and push)  
**Date:** 2026-10-05  
**Amends:** Protocol v2.1 (learning-schema/v2.1)

## 1. Background

S1 under v2.1 (n=8) produced a mechanical REJECT verdict. The candidate
achieved 8/8 SUPPORTED vs parent 6/8, winning both discordant production
pairs (2/2). However, the preregistered promotion bar was mathematically
impossible to clear at n=8:

- McNemar exact test: with 2 discordant pairs, best possible one-sided
  p = 0.25 (requires p < 0.05).
- Wilson non-inferiority: at n=2 per class, max lower bound = 0.34
  (requires >= 0.90).
- Latency: candidate 227ms vs parent 171ms. The candidate was "slower"
  because it produced the requested artifact; the parent "faster" because
  parallel agents could not perform the production effect.

S1 is preserved as S1-pilot (see ledger quarantine record). It demonstrated
that the learned policy produces the predicted directional behavior, while
revealing the promotion apparatus lacked statistical power.

v2.2 addresses this via formal power analysis, stratified evaluation,
and redefined efficiency metrics. No thresholds were tuned to the S1-pilot
outcome (α remains 0.05, Wilson remains 95%).

## 2. Power Analysis

### 2.1 Paired superiority (McNemar exact test)

- H0: P(candidate win | discordant) = 0.5
- H1: P(candidate win | discordant) = 0.8 (preregistered minimum
  meaningful effect: 4:1 odds, representing clear practical advantage)
- α = 0.05 (one-sided), power target = 0.80

Exact binomial calculations:

| n_discordant | critical k | power at p1=0.8 |
|--------------|------------|-----------------|
| 18           | 13         | 0.867           |
| 20           | 15         | 0.804           |
| 21           | 15         | 0.891           |

**Requirement:** ≥18 discordant pairs in the production stratum.

Expected discordance rate in production stratum: 60% (conservative;
S1-pilot observed 100% on production tasks, but we do not use pilot
data to set design parameters).

**Production stratum size:** 30 tasks → expected 18 discordant pairs
at 60% discordance, yielding 87% power.

### 2.2 Per-class non-inferiority (Wilson, secondary)

Moved to secondary metrics per v2.2 hierarchy (Section 4). For
informative confidence intervals:

| n per class | Wilson 95% lower (perfect) |
|-------------|---------------------------|
| 20          | 0.839                     |
| 30          | 0.887                     |
| 35          | 0.901                     |

**Minimum class size:** 20 (production: 30, research: 20, mixed: 10).
The 0.90 threshold for formal non-inferiority requires n=35, which is
noted but not gating in v2.2.

### 2.3 Strata

| Stratum    | n   | Definition                              | Role      |
|------------|-----|-----------------------------------------|-----------|
| production | 30  | requires_write_effect, multi-unit       | Primary   |
| research   | 20  | requires_read_effect                    | Secondary |
| mixed      | 10  | requires both effects                   | Secondary |
| **Total**  | **60** |                                      |           |

Strata are preregistered task-family labels for evaluation only. The
candidate policy does not receive stratum labels (preserves Class C
prohibition). The evaluator uses strata for stratified analysis.

All 60 tasks are independently specified instances. No duplication of
S1-pilot tasks.

## 3. Metric Redefinition

### 3.1 The latency problem

v2.1 measured raw wall-clock latency. This rewards fast failure:
- Parent: 171ms, 0/2 verified production outcomes.
- Candidate: 227ms, 2/2 verified production outcomes.

Calling the parent "more efficient" because it failed faster is
undesirable.

### 3.2 Verified-success metrics (v2.2)

**Primary efficiency metric:** Latency and cost computed ONLY among
runs with SUPPORTED verdicts (verified successful outcomes).

- `verified_latency_mean`: mean latency_ms among SUPPORTED runs.
- `verified_cost_mean`: mean cost among SUPPORTED runs.

Computed per stratum. Requires n>=5 SUPPORTED runs for both policies
in a stratum; otherwise the comparison is skipped (insufficient data,
not a failure).

**Diagnostic metric:** Raw latency/cost (all runs) retained for
transparency but not gating.

### 3.3 Rationale

The candidate is not rewarded for being slower. A failed organization
is not rewarded for terminating early. Efficiency is measured as
resource expenditure per verified successful outcome.

## 4. Metric Hierarchy

### Primary (gating promotion)

1. **Verified task success**: SUPPORTED rate on production stratum.
2. **Paired superiority**: McNemar exact test on production stratum
   discordant pairs. Require p < 0.05 (one-sided).
3. **Safety**: Zero safety violations for candidate.

### Secondary (reported, soft gates)

4. **Per-class rates**: Wilson 95% CIs per stratum.
5. **Verified-success latency**: candidate <= 2.0x parent (per stratum,
   where n>=5 for both).
6. **Verified-success cost**: candidate <= 2.0x parent (same).
7. **Agent count**: diagnostic.
8. **Raw latency**: diagnostic only.

## 5. Promotion Rule (v2.2)

### 5.1 Primary endpoint

Production verified-success rate is the primary outcome measure.
Candidate-vs-parent superiority on this endpoint is formally tested
using the preregistered one-sided McNemar exact test (Section 2.1).
No separate arbitrary success-rate threshold is imposed; the McNemar
test is the inferential gate.

### 5.2 Promotion gates (all hard)

**PROMOTE** iff ALL of:

- (P1) **Paired superiority**: McNemar one-sided p < 0.05 on production
  stratum discordant pairs, AND
- (P2) **Safety**: Zero candidate safety violations, AND
- (P3) **Resource efficiency**: For every resource-evaluable stratum,
  BOTH of the following hold:
  - `candidate_verified_latency_mean / parent_verified_latency_mean <= 2.0`
  - `candidate_verified_cost_mean / parent_verified_cost_mean <= 2.0`

**REJECT** if (P1) fails with adequate information (Section 5.4), or
(P2) fails, or (P3) fails on any evaluable stratum.

### 5.3 Resource gate evaluability (frozen)

A stratum is **resource-evaluable** iff both policies have >=5 SUPPORTED
runs in that stratum.

- If at least one stratum is resource-evaluable: (P3) applies to all
  evaluable strata. Violation on any evaluable stratum → REJECT.
- If NO stratum is resource-evaluable: resource gate = UNEVALUABLE →
  promotion is NOT_ELIGIBLE (neither PROMOTE nor REJECT on resource
  grounds; experiment classified per Section 5.4).

The "n < 5" rule cannot silently become a pass. It is explicitly
preregistered here as blocking promotion when no stratum is evaluable.

### 5.4 Underpowered realized discordance (frozen)

The 60% planned discordance is a design assumption, not a guarantee.
If the realized number of production-stratum discordant pairs is
substantially below the design target of 18:

- The McNemar p-value remains valid and is reported.
- A non-significant McNemar result with <18 discordant pairs is
  classified as **UNDERPOWERED/INCONCLUSIVE**, not as evidence against H3.
- The experiment did not achieve its planned information level.

This is a preregistered classification rule, not a post-hoc threshold
adjustment. The promotion criterion (Section 5.2) is unchanged; the
UNDERPOWERED classification reflects insufficient information to
apply it meaningfully.

Outcome taxonomy for S1 v2.2:
- **PROMOTE**: All gates pass with >=18 discordant pairs.
- **REJECT**: Gates fail with >=18 discordant pairs.
- **UNDERPOWERED/INCONCLUSIVE**: <18 discordant pairs realized.

Per-class Wilson non-inferiority is NOT gating in v2.2 (moved to
secondary per Section 4).

## 6. What v2.2 Does NOT Change

- α = 0.05 (not relaxed to 0.10).
- Wilson 95% (not relaxed to 80%).
- Safety: zero violations required.
- Learner, evaluator, assurance unchanged.
- D1, learn-1, candidate-1 unchanged.
- S1-pilot preserved as methodological history.

## 7. Experimental Sequence (v2.2)

```
v2.2 amendment (this document)
    │
freeze protocol + push
    │
fresh S1 (60 tasks, stratified)
    │
┌───┴───┐
│       │
promote reject
│       │
▼       ▼
D2     terminate
│
learn-2
│
▼
S2 (powered per v2.2 principles)
│
▼
D3
```

If S1 v2.2 REJECTs, the experiment terminates (does not proceed to D2).
A rejection under a properly powered test is a genuine H3 result.

## 8. Freezing Checklist

Before fresh S1:
- [ ] Power calculation frozen (Section 2)
- [ ] Strata frozen (Section 2.3)
- [ ] Task pool generated (60 independent tasks) and hashed
- [ ] Metric definitions frozen (Section 3)
- [ ] Promotion thresholds frozen (Section 5)
- [ ] Harness implements v2.2 metrics
- [ ] Amendment committed and pushed
- [ ] S1-pilot quarantine verified in ledger
