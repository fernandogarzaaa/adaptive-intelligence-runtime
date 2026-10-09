# AIR Benchmark Run Report

**Date:** 2026-10-07
**Model:** Nebius `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` (same for all runs)
**Tasks:** 18 data engineering tasks × 3 reps = 54 executions
**Results file:** [benchmark_air_results.json](sandbox://workspace/adaptive-intelligence-runtime/docs/benchmark_air_results.json)

## Summary

| Metric | AIR | Original LangGraph* |
|--------|-----|---------------------|
| Success rate | 98.1% (53/54) | 77.8% |
| Avg tokens | 1,388 | 2,690 |
| Avg latency | 7.1s | 12.6s |
| Boilerplate lines | 0 | 54 |

\* Original benchmark used Groq Llama 3.3 70B. Model difference means
these comparisons are directional, not apples-to-apples.

## Strategy Distribution

| Strategy | Count | % |
|----------|-------|---|
| single_agent | 50 | 94% |
| hierarchical_agents | 3 | 6% |
| parallel_agents | 0 | 0% |

The 3 hierarchical allocations were all in `data_quality` (BENCH-07/08/09:
Great Expectations suite, schema profiling, SLA report). No task
triggered parallel_agents.

## Per-Category Breakdown

| Category | n | Avg tokens | Avg latency | Strategies |
|----------|---|------------|-------------|------------|
| sql_generation | 9 | 1,137 | 6.6s | 9× single |
| pipeline_debugging | 9 | 1,146 | 6.1s | 9× single |
| data_quality | 9 | 2,241 | 9.9s | 6× single, 3× hierarchical |
| metadata_generation | 9 | 1,266 | 6.6s | 9× single |
| etl_orchestration | 9 | 1,268 | 6.8s | 9× single |
| transformation | 8 | 1,254 | 6.7s | 8× single |

## Failures

**1 failure:** BENCH-16 (pandas to PySpark), rep 1.
Error: `'NoneType' object is not subscriptable` — the Nebius API returned
a malformed response (missing `choices[0]`). Reps 2 and 3 of the same
task succeeded. This is a transient API issue, not an AIR bug.

## Key Observations

1. **Token efficiency is real.** AIR averages 1,388 tokens vs LangGraph's
   2,690. The single_agent strategy uses 1 LLM call vs LangGraph's 4-node
   pipeline (plan → execute → validate → retry). When the task doesn't
   need multi-agent organization, not spawning agents saves tokens.

2. **The allocation question is sharper now.** 94% single_agent means pv2
   sees these tasks as not benefiting from multi-agent organization.
   This is either (a) correct — these tasks genuinely don't need it, or
   (b) a sign the policy is too conservative. The 6 starred tasks
   (BENCH-02, 04, 09, 11, 13, 16) where multi-agent "plausibly matters"
   all got single_agent except none — worth investigating whether
   hierarchical/parallel would have produced better outputs.

3. **No parallel_agents is notable.** CTC-1 validated that pv2 responds
   to capability requirements, but none of these 18 tasks triggered it.
   Either the tasks don't have the right structure, or the text-only
   goal input doesn't surface capability needs (the `--needs` flag
   exists for this but wasn't used in the benchmark).

4. **Latency is competitive.** 7.1s average vs 12.6s for LangGraph,
   driven by fewer LLM calls per task.

## Limitations

- **Different models:** Original used Groq Llama 3.3 70B; this run used
  Nebius Nemotron-3-Nano-30B. Success rates aren't directly comparable.
- **Crash-based success metric:** As in the original, success = "didn't
  throw." Output correctness was not scored. LLM-as-judge scoring with
  the per-task rubrics in BENCHMARK_SUBSET.md is the recommended next step.
- **No LangGraph baseline re-run:** The comparison uses stored results
  from the original benchmark, not a fresh head-to-head on the same model.

## Recommended Next Steps

1. Re-run LangGraph's `run_langgraph_task` on the same 18 tasks with
   Nebius for a true apples-to-apples comparison.
2. LLM-as-judge correctness scoring using the per-task rubrics.
3. Investigate the 6 starred tasks: would `--needs` flags or explicit
   capability hints change the allocation? Does multi-agent produce
   better outputs where pv2 currently says single_agent?
