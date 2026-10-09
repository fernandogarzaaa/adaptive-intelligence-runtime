# LangGraph Baseline Run Report

**Date:** 2026-10-07
**Framework:** LangGraph (plan -> execute -> validate -> retry, max 2 attempts)
**Model:** nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B (Nebius Token Factory)
**Topology:** plan -> execute -> validate -> (retry -> validate, max 2 attempts)
**Executions:** 54 (18 tasks x 3 runs)

## Summary
- Crash-based success rate: **54/54 (100.0%)**
- Avg tokens per execution: **7,177**
- Avg latency per execution: **28.6s**
- Total tokens: 387,560
- Boilerplate lines (per-task wiring): 54

## Per-Category Breakdown

| Category | Tasks | Runs | Success | Avg tokens | Avg latency |
|---|---|---|---|---|---|
| data_quality | BENCH-07, BENCH-08, BENCH-09 | 9 | 9/9 | 9,823 | 26.3s |
| etl_orchestration | BENCH-13, BENCH-14, BENCH-15 | 9 | 9/9 | 7,617 | 22.1s |
| metadata_generation | BENCH-10, BENCH-11, BENCH-12 | 9 | 9/9 | 8,253 | 26.7s |
| pipeline_debugging | BENCH-04, BENCH-05, BENCH-06 | 9 | 9/9 | 5,867 | 17.8s |
| sql_generation | BENCH-01, BENCH-02, BENCH-03 | 9 | 9/9 | 5,569 | 19.6s |
| transformation | BENCH-16, BENCH-17, BENCH-18 | 9 | 9/9 | 5,933 | 59.1s |

## Per-Task Results

### BENCH-01 (sql_generation)
*Write a window function to calculate 7-day rolling average r*
- Success: 3/3, avg tokens: 2,217, avg latency: 10.2s, avg validate attempts: 1.0
  - run 1: OK (2748 tok, 11.31s)
  - run 2: OK (2277 tok, 10.601s)
  - run 3: OK (1627 tok, 8.647s)

### BENCH-02 (sql_generation)
*Generate a slowly changing dimension Type 2 merge query for *
- Success: 3/3, avg tokens: 12,179, avg latency: 36.9s, avg validate attempts: 1.7
  - run 1: OK (15001 tok, 39.756s)
  - run 2: OK (15126 tok, 48.188s)
  - run 3: OK (6409 tok, 22.856s)

### BENCH-03 (sql_generation)
*Write a recursive CTE to flatten a self-referencing org hier*
- Success: 3/3, avg tokens: 2,312, avg latency: 11.6s, avg validate attempts: 1.0
  - run 1: OK (1605 tok, 10.276s)
  - run 2: OK (3020 tok, 13.154s)
  - run 3: OK (2312 tok, 11.46s)

### BENCH-04 (pipeline_debugging)
*Debug a dbt model that produces duplicate rows after a join*
- Success: 3/3, avg tokens: 3,977, avg latency: 13.5s, avg validate attempts: 1.0
  - run 1: OK (5070 tok, 15.731s)
  - run 2: OK (2503 tok, 9.884s)
  - run 3: OK (4359 tok, 14.911s)

### BENCH-05 (pipeline_debugging)
*Given this Airflow DAG error log, identify the root cause an*
- Success: 3/3, avg tokens: 8,363, avg latency: 23.6s, avg validate attempts: 1.7
  - run 1: OK (14963 tok, 32.982s)
  - run 2: OK (4394 tok, 19.994s)
  - run 3: OK (5732 tok, 17.942s)

### BENCH-06 (pipeline_debugging)
*Diagnose a schema mismatch error in a Fivetran → Snowflake s*
- Success: 3/3, avg tokens: 5,260, avg latency: 16.3s, avg validate attempts: 1.0
  - run 1: OK (5240 tok, 18.42s)
  - run 2: OK (5431 tok, 15.361s)
  - run 3: OK (5108 tok, 15.218s)

### BENCH-07 (data_quality)
*Write Great Expectations checks for an orders table*
- Success: 3/3, avg tokens: 12,924, avg latency: 33.8s, avg validate attempts: 1.7
  - run 1: OK (17004 tok, 42.222s)
  - run 2: OK (6443 tok, 21.838s)
  - run 3: OK (15324 tok, 37.488s)

### BENCH-08 (data_quality)
*Profile this dataset schema and flag anomalies, nulls, and o*
- Success: 3/3, avg tokens: 11,843, avg latency: 30.3s, avg validate attempts: 1.7
  - run 1: OK (15425 tok, 45.277s)
  - run 2: OK (16033 tok, 33.73s)
  - run 3: OK (4070 tok, 12.038s)

### BENCH-09 (data_quality)
*Generate a data SLA report comparing actual vs expected row *
- Success: 3/3, avg tokens: 4,704, avg latency: 14.7s, avg validate attempts: 1.0
  - run 1: OK (6116 tok, 16.879s)
  - run 2: OK (5697 tok, 15.156s)
  - run 3: OK (2299 tok, 12.156s)

### BENCH-10 (metadata_generation)
*Generate a data catalog description and lineage notes for a *
- Success: 3/3, avg tokens: 4,653, avg latency: 15.4s, avg validate attempts: 1.0
  - run 1: OK (6329 tok, 20.985s)
  - run 2: OK (4220 tok, 13.02s)
  - run 3: OK (3411 tok, 12.222s)

### BENCH-11 (metadata_generation)
*Tag PII columns in a schema and generate a compliance summar*
- Success: 3/3, avg tokens: 9,456, avg latency: 29.3s, avg validate attempts: 1.7
  - run 1: OK (13089 tok, 36.552s)
  - run 2: OK (1798 tok, 9.292s)
  - run 3: OK (13480 tok, 41.988s)

### BENCH-12 (metadata_generation)
*Auto-document a dbt project by generating YAML descriptions *
- Success: 3/3, avg tokens: 10,651, avg latency: 35.5s, avg validate attempts: 1.7
  - run 1: OK (6828 tok, 29.071s)
  - run 2: OK (16211 tok, 52.223s)
  - run 3: OK (8914 tok, 25.172s)

### BENCH-13 (etl_orchestration)
*Design a 3-step ELT pipeline: Postgres → DuckDB → Streamlit *
- Success: 3/3, avg tokens: 4,424, avg latency: 15.9s, avg validate attempts: 1.0
  - run 1: OK (3393 tok, 12.09s)
  - run 2: OK (6203 tok, 20.787s)
  - run 3: OK (3675 tok, 14.888s)

### BENCH-14 (etl_orchestration)
*Orchestrate a multi-source ingestion DAG with retry logic an*
- Success: 3/3, avg tokens: 12,372, avg latency: 34.0s, avg validate attempts: 1.7
  - run 1: OK (7120 tok, 24.32s)
  - run 2: OK (15687 tok, 39.059s)
  - run 3: OK (14310 tok, 38.651s)

### BENCH-15 (etl_orchestration)
*Design a backfill strategy for 2 years of historical event d*
- Success: 3/3, avg tokens: 6,054, avg latency: 16.2s, avg validate attempts: 1.0
  - run 1: OK (6544 tok, 16.047s)
  - run 2: OK (6656 tok, 18.465s)
  - run 3: OK (4962 tok, 14.198s)

### BENCH-16 (transformation)
*Convert this pandas transform to a PySpark equivalent with p*
- Success: 3/3, avg tokens: 5,222, avg latency: 60.6s, avg validate attempts: 1.3
  - run 1: OK (3615 tok, 36.035s)
  - run 2: OK (7562 tok, 85.789s)
  - run 3: OK (4490 tok, 60.002s)

### BENCH-17 (transformation)
*Rewrite a row-by-row SQL cursor as a set-based dbt model*
- Success: 3/3, avg tokens: 6,357, avg latency: 74.0s, avg validate attempts: 1.0
  - run 1: OK (6440 tok, 72.482s)
  - run 2: OK (6585 tok, 84.631s)
  - run 3: OK (6046 tok, 64.992s)

### BENCH-18 (transformation)
*Optimize a 10-table star schema join query for BigQuery*
- Success: 3/3, avg tokens: 6,219, avg latency: 42.5s, avg validate attempts: 1.0
  - run 1: OK (5454 tok, 38.383s)
  - run 2: OK (6553 tok, 43.701s)
  - run 3: OK (6649 tok, 45.442s)

## Failures
None. All 54 executions completed without exceptions.

## Notes
- LLM calls use Nebius via stored credential (surrogate exchange); no raw keys in code or logs.
- Token counting sums usage.prompt_tokens + usage.completion_tokens per call.
- Nemotron-3-Nano is a reasoning model: responses include a `reasoning` field; the harness prefers `content` and falls back to `reasoning` only when content is empty (occurred 0 times at the chosen budgets).
- Token budgets raised vs the Groq reference (plan/execute 2500/3000) to accommodate reasoning overhead.
- Full outputs stored in `benchmark_langgraph_results.json` (`output` field) for the LLM-as-judge correctness step.
- No Anthropic fallback (would break model parity with the AIR side). Rate-limit backoff: 3 retries with exponential waits.
