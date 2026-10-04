"""The single frozen evaluation suite + assurance runner.

One suite, built once, used identically for every run in every
condition (contamination point 3). The suite id and version are
fixed constants, not random: ``save_suite`` is INSERT OR IGNORE, so
rebuilding is idempotent.

Suite cases (weights reflect the research question: grounding is
the primary gate):
- outcome_grounding (weight 3): layers 1+2+3 — every claimed
  outcome cites valid evidence covering its artifacts/effects.
- event_evidence (weight 1): layer 1 — at least one valid evidence
  source exists in the run.
- no_failures (weight 1): hygiene.
- agents_completed (weight 1): at least one agent completed.

Assurance runs post-hoc via AssuranceEngine.assure(evaluation_id)
and is persisted to assurance_runs. Probe ids are recorded per run
so the verification step can assert they are identical across
conditions.
"""

from __future__ import annotations

from air.assurance.probes import AssuranceEngine, AssuranceResult
from air.evaluation.suites import (
    EvalCase,
    EvalSuite,
    EvaluationResult,
    Evaluator,
)

SUITE_ID = "suite_exp_v1"
SUITE_VERSION = "1.0.0"
SUITE_NAME = "baseline-experiment-v1"


def build_suite() -> EvalSuite:
    return EvalSuite(
        id=SUITE_ID,
        name=SUITE_NAME,
        version=SUITE_VERSION,
        cases=[
            EvalCase(id="g1", name="outcome grounding",
                     check="outcome_grounding", params={}, weight=3.0),
            EvalCase(id="g2", name="event evidence",
                     check="event_evidence", params={}, weight=1.0),
            EvalCase(id="h1", name="no failures",
                     check="no_failures", params={}, weight=1.0),
            EvalCase(id="h2", name="agents completed",
                     check="agents_completed",
                     params={"min_completed": 1}, weight=1.0),
        ],
    )


def evaluate_run(conn, run_id: str) -> EvaluationResult:
    """Evaluate a finished run with the frozen suite. Post-hoc only."""
    evaluator = Evaluator(conn)
    suite = evaluator.save_suite(build_suite())
    return evaluator.evaluate_run(run_id, suite)


def assure_run(conn, evaluation_id: str) -> AssuranceResult:
    """Run assurance probes over a persisted evaluation. Post-hoc only."""
    engine = AssuranceEngine(conn)
    return engine.assure(evaluation_id)


def probe_ids(result: AssuranceResult) -> list[str]:
    return sorted(p.probe for p in result.probes)
