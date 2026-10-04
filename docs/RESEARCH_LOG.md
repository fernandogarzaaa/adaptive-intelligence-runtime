# AIR Research Log

Methodological record for the eventual paper/report. Facts only; no
performance claims until the protocol has been run.

## Methodological state (2026-10-04)

```
AIR-SELF-AUDIT-v1
        |
        |-- discovered evaluator exploit
        v
Evidence grounding fix (cffc08d)
        |
        |-- TOOL_SUCCESS != CLAIM_SUPPORTED
        v
SELF-AUDIT-v2
        |
        |-- exploit falsified
        v
Protocol verification
        |
        v
Frozen A / B / C
        |
        v
Experiment
        |
        v
failure-first analysis
        |
        v
D1 -> learning -> D2 -> learning -> D3
```

Core architecture is FROZEN from 2026-10-04 until the baseline
experiment finishes. No optimization of AIR while a frozen run is
underway; the system produces whatever result it produces.

## The research question

Under equal externally imposed resources and identical
evaluation/assurance conditions, does dynamically allocating
cognitive organization produce a statistically and practically
meaningful improvement in independently verified task success over
the predefined baselines?

Legitimate outcomes, all scientifically useful:

- A > B > C: baselines win; useful.
- A ≈ B ≈ C: no measurable difference; useful.
- C < A: dynamic organization carries a measurable coordination
  tax; arguably more interesting than a marginal win.
- C wins only after D1 → D2 → D3: evidence for the stronger
  thesis — AIR's value may not be its initial allocation policy
  but its ability to learn an organization policy from
  experience. That distinction is central to the program.

AIR losing is a legitimate result.

## The exploit history (for the paper)

Not "we fixed an evaluator bug." The stronger methodological
story, preserved with artifacts:

Before benchmarking, AIR's assurance system autonomously exposed a
reward-hacking vulnerability in its own evaluation boundary: the
`event_evidence` gate accepted the mere presence of a
`tool.completed` event as grounding, so an agent that read one
unrelated file and claimed "quarterly report written" received
SUPPORTED. The vulnerability was independently reproduced,
converted into an explicit invariant (Invariant #13: a successful
tool execution is not, by itself, evidence of task success — it is
only an eligible evidence source; TOOL_SUCCESS ≠ CLAIM_SUPPORTED),
fixed mechanically in three structural layers
(validity → relevance → outcome) without introducing an LLM judge,
and then subjected to a second self-audit that failed to reproduce
the exploit.

Artifacts:

- `~/workspace/air-audits/v1-first-run/` — original finding,
  status: confirmed, fixed_by: cffc08d (finding text preserved
  unchanged; annotation only).
- `~/workspace/air-audits/v2-post-fix/` — falsification run
  (critical=0, high=0, medium=0, low=1).
- `tests/test_evidence_grounding.py`, `tests/test_evaluator_adversarial.py`
  — permanent regression + adversarial coverage.
- `docs/INVARIANTS.md` #13.

That sequence is more meaningful evidence for the assurance
architecture than another green test count.

## Contamination controls (12)

Defined 2026-10-04; enforced mechanically by
`python -m air.experiments.verify_protocol` before any frozen run:

1. Equivalent task information per condition (input hashes).
2. Condition blindness (label never reaches the runtime).
3. Identical evaluation/assurance (suite + probe ids/versions).
4. Budgets enforced, not configured (attempt-to-exceed tests).
5. Random seeds recorded.
6. Failed runs remain in the dataset.
7. INCONCLUSIVE/INVALID/REFUTED bucketed explicitly, never
   silently converted in aggregation.
8. No learned-policy leakage between conditions.
9. Evaluation artifacts immutable by the evaluated system.
10. Primary metric computed post-hoc from immutable artifacts.
11. Task ordering fixed/recorded; cannot be a training signal.
12. Experiment registry append-only.

#12 matters most for AIR: experience → evaluation → assurance →
policy evolution is the architecture, so the registry is also the
most plausible leakage channel. It is exactly what makes D1→D2→D3
interesting and exactly what must be contained during A/B/C.

## Baseline experiment v1 (2026-10-04): AIR lost

First frozen A/B/C run under the protocol. 240 runs (5 reps × 16
tasks × 3 conditions), task set v1 hashed
`b6691448f4ea547b940ca1514289dbb32b84281837dccb3d909a85cc1ed3af55`,
mechanical protocol verification green (12 contamination checks),
pre-registered before the run, failure ledger complete.

| condition | verified successes | rate | mean agents/run |
|---|---|---|---|
| A single agent | 75/80 | 93.75% | 1.00 |
| B static hierarchical | 75/80 | 93.75% | 4.00 |
| C dynamic allocation | 65/80 | 81.25% | 1.38 |

Fisher's exact, two-sided: C vs A p=0.0294, C vs B p=0.0294,
B vs A p=1.0.

The failure mode is specific and measured: on multi-file writes
(t05/t07), the real allocator chose `parallel_agents` in all 5
reps — three researchers + one synthesizer, no producer — so no
agent ever wrote the files and `outcome_grounding` correctly
refused SUPPORTED. That is a genuine coordination tax on dynamic
allocation, not a harness artifact: the strategy selection was
wrong for the task shape. B matched A on success at 4× the agent
cost (spawn efficiency 0.23 vs 0.94).

Interpretation (per the research question): with scripted
behaviors of identical competence, dynamic allocation does not
currently produce a meaningful improvement in verified task
success; it produces a measurable loss on tasks where its
strategy choice misfires. This is the C < A branch: more
interesting than a marginal win.

What the shakedown did and did not test: it exercised the
allocator's per-task strategy selection, the full evidence
pipeline as sole judge, the failure ledger, and the anti-gaming
machinery. It did NOT test model-backed judgment, mid-run
spawning, content-level truth, or learning. Valid shakedown of
the apparatus, not a claim about AIR vs humans.

Artifacts: `~/workspace/air-experiments/baseline-v1/` (report,
pre-reg, sealed registry, per-condition ledgers).

Next: D1 → experience/learning → D2 → experience/learning → D3,
testing whether AIR learns the allocation distinction — the
stronger thesis (value in learning the organization policy, not
the initial policy).
