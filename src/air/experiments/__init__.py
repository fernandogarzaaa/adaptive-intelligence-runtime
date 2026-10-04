"""AIR experiment harness: baselines A/B/C under RESEARCH_PROTOCOL.md.

This is a research instrument, not a product feature. It drives the
runtime exclusively through public entrypoints (create_run, start_run,
register_behavior, call_tool, Evaluator, AssuranceEngine) and never
modifies product code paths. All experiment-only code lives in this
package plus tests/test_experiments.py.

Design notes (see docs/RESEARCH_PROTOCOL.md sections 1-5, 7):

- The independent variable is cognitive organization only. Task set,
  budgets, tool registry, capability grants, evaluation suite,
  assurance probes, and machine are held constant and pinned in the
  pre-registration record.
- No model provider exists in this environment: every agent runs a
  scripted behavior (the documented test control). Competence is
  therefore identical across conditions by construction; the report
  states plainly what this limits.
- Learning is OFF: the harness never invokes the learning engine,
  and each condition runs in its own database (physical
  experience-store isolation). Policy version is asserted per run.
- Verified success comes ONLY from the evaluation/assurance
  boundary reading the immutable ledger, post-hoc. No agent-reported
  score flows into any metric.
- Failures are first-class: every run is recorded and reported,
  including timeouts and verification failures. No filtering.
"""
