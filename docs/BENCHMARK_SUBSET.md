# Benchmark Subset: 18 Tasks for AIR vs LangGraph

## Source

**Repo:** https://github.com/sweta2503/agent-framework-benchmark (cloned to `/tmp/bench-repos/afb`)

The original benchmark ran 24 unique data engineering tasks through LangGraph, CrewAI, and AutoGen (107+ executions per framework). Stored results confirm: LangGraph 77.8% success / 2,690 avg tokens / 12.6s latency; CrewAI 74.8% / 5,081 / 20.0s; AutoGen 72.9% / 5,586 / 18.3s.

**Why this repo:** It has the cleanest harness (single `agent.py`, ~900 lines), tasks stored in `benchmark.db`, and the task format is pure natural language — no external fixtures or services required. The other two candidates were rejected: `hamzaahsan334-dev/langgraph-vs-crewai` references a `tasks/` directory and `benchmark/` package that don't exist in the repo (incomplete); `PCSchmidt/agent-framework-bakeoff` is aviation-domain (TLE/NOTAMs/ADSB), not data engineering.

## Task Format

**Input contract:** `(task_type: str, task_name: str)` — two strings. `task_type` is one of six category slugs. `task_name` is a natural language prompt.

**Harness API** (from `/tmp/bench-repos/afb/agent.py`):
```python
from agent import run_langgraph_task, run_crewai_task, run_autogen_task

result = run_langgraph_task("sql_generation", "Write a window function...")
# Returns dict:
# {
#   "framework": "LangGraph",
#   "task_type": "sql_generation",
#   "task_name": "...",
#   "tokens_used": 2690,        # int, sum across all LLM calls
#   "latency_seconds": 12.6,    # float, wall clock
#   "success": 1,               # 1 = no exception, 0 = exception
#   "error_message": "",        # str, truncated to 200 chars
#   "output_preview": "...",    # str, first 300 chars of final output
#   "boilerplate_lines": 54,    # int, framework wiring lines
# }
```

**Original LangGraph topology** (lines 89-158 of `agent.py`): plan → execute → validate → (retry → validate if invalid, max 2 attempts). Four nodes, one conditional edge.

**Original CrewAI topology** (lines 185-275): three sequential agents (Data Engineer → QA Reviewer → Tech Lead), each a scoped LLM call with role/goal/backstory system prompts.

**Original AutoGen topology** (lines 292-360): UserProxy ↔ AssistantAgent conversational loop, 3 rounds max, terminates on "TERMINATE".

## Scoring Methodology (Original)

**Success = 1 if the framework function returns without raising an exception, else 0.** This is a weak metric — it measures "didn't crash," not "correct output." There is no automated correctness check; output quality was assessed via a separate Gradio UI with LLM-as-judge (`analyze_with_groq`).

**For the AIR benchmark, use a stronger metric.** Recommended: LLM-as-judge with a per-task rubric (see "Success Criteria" per task below), plus the original crash-based success as a floor.

## Setup Requirements

- Python 3.11+
- `pip install -r /tmp/bench-repos/afb/requirements.txt` (key deps: `langgraph`, `groq`, `anthropic`, `python-dotenv`)
- **LLM provider:** Original used Groq Llama 3.3 70B (`llama-3.3-70b-versatile`). For AIR vs LangGraph, **both frameworks must use the same model**. Recommended: Nebius `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` (available via `~/workspace/skills/nebius/`).
- No external data files, no database fixtures, no network services beyond the LLM API. All tasks are self-contained natural language prompts.

## Selected Subset: 18 Tasks

3 per category. Selected for: (a) self-contained (no external context needed), (b) range of complexity within each category, (c) at least one per category where multi-agent organization plausibly matters (marked ★).

---

### Category: sql_generation

**BENCH-01** — *7-day rolling average*
- Task type: `sql_generation`
- Prompt: `Write a window function to calculate 7-day rolling average revenue by region`
- Success criteria: Valid SQL with a window function (OVER clause), 7-day frame (ROWS BETWEEN 6 PRECEDING or RANGE), partitioned/grouped by region, references a revenue column.
- Complexity: Low. Single well-defined output.

**BENCH-02** — *SCD Type 2 merge* ★
- Task type: `sql_generation`
- Prompt: `Generate a slowly changing dimension Type 2 merge query for a customer table`
- Success criteria: Valid SQL implementing SCD2 semantics: handles new records (INSERT), changed records (expire old + insert new with version/effective dates), unchanged records (no-op). Mentions effective_date/expiry or equivalent.
- Complexity: Medium. Requires understanding temporal modeling, multiple logical steps.

**BENCH-03** — *Recursive CTE*
- Task type: `sql_generation`
- Prompt: `Write a recursive CTE to flatten a self-referencing org hierarchy`
- Success criteria: Valid SQL with RECURSIVE CTE (WITH RECURSIVE or equivalent), anchor member + recursive member, terminates correctly, outputs flattened hierarchy with level/depth.
- Complexity: Medium. Recursive logic is a known LLM weak point.

---

### Category: pipeline_debugging

**BENCH-04** — *dbt duplicate rows* ★
- Task type: `pipeline_debugging`
- Prompt: `Debug a dbt model that produces duplicate rows after a join`
- Success criteria: Identifies likely root causes (fan-out from many-to-many join, missing dedup, grain mismatch), suggests concrete fixes (pre-aggregate, distinct, join on correct keys), mentions how to verify.
- Complexity: Medium. Diagnostic reasoning, multiple hypotheses.

**BENCH-05** — *Airflow DAG error*
- Task type: `pipeline_debugging`
- Prompt: `Given this Airflow DAG error log, identify the root cause and suggest a fix`
- Success criteria: Provides a structured root-cause analysis framework (even without the actual log, a good answer covers: task failure vs scheduler issue vs resource exhaustion, where to look, common patterns). Suggests specific fix categories.
- Complexity: Low-Medium. Note: prompt references a log that isn't provided; tests how the framework handles underspecified input.

**BENCH-06** — *Schema mismatch*
- Task type: `pipeline_debugging`
- Prompt: `Diagnose a schema mismatch error in a Fivetran → Snowflake sync`
- Success criteria: Covers likely causes (source schema evolution, type coercion, column rename/drop, case sensitivity), suggests fixes (schema drift handling, explicit casting, Fivetran sync settings), mentions prevention.
- Complexity: Medium. Requires cross-system knowledge.

---

### Category: data_quality

**BENCH-07** — *Great Expectations suite*
- Task type: `data_quality`
- Prompt: `Write Great Expectations checks for an orders table`
- Success criteria: Produces valid GE expectations (expect_column_values_to_not_be_null, expect_column_values_to_be_between, etc.) covering at least: primary key uniqueness, null checks on required columns, value ranges for amounts/dates. Syntactically plausible GE code.
- Complexity: Medium. Requires framework-specific API knowledge.

**BENCH-08** — *Schema profiling*
- Task type: `data_quality`
- Prompt: `Profile this dataset schema and flag anomalies, nulls, and outliers`
- Success criteria: Provides a systematic profiling approach: null rate per column, distinct counts, distribution stats, outlier detection method, anomaly flagging criteria. Structured output.
- Complexity: Low-Medium. Methodical rather than creative.

**BENCH-09** — *SLA report* ★
- Task type: `data_quality`
- Prompt: `Generate a data SLA report comparing actual vs expected row counts by hour`
- Success criteria: Defines SLA structure (expected vs actual, threshold, breach definition), hourly granularity logic, alerting criteria, example query or pseudocode for computing the comparison.
- Complexity: Medium. Multi-part deliverable (definition + computation + alerting).

---

### Category: metadata_generation

**BENCH-10** — *Data catalog entry*
- Task type: `metadata_generation`
- Prompt: `Generate a data catalog description and lineage notes for a revenue fact table`
- Success criteria: Covers: table purpose, key columns with descriptions, grain, refresh cadence, upstream sources, downstream consumers, owner/steward fields. Lineage notes trace at least 2 hops.
- Complexity: Low. Structured documentation.

**BENCH-11** — *PII tagging* ★
- Task type: `metadata_generation`
- Prompt: `Tag PII columns in a schema and generate a compliance summary`
- Success criteria: Identifies PII categories (direct identifiers, quasi-identifiers), tagging methodology, compliance summary covering at least GDPR/CCPA implications, remediation suggestions (masking, tokenization, access controls).
- Complexity: Medium. Requires regulatory knowledge + technical classification.

**BENCH-12** — *dbt YAML docs*
- Task type: `metadata_generation`
- Prompt: `Auto-document a dbt project by generating YAML descriptions from SQL`
- Success criteria: Produces valid dbt `schema.yml` structure (models, columns, descriptions, tests), demonstrates deriving descriptions from SQL logic (e.g., inferred from CASE statements, joins, aggregations).
- Complexity: Medium. Format-sensitive output.

---

### Category: etl_orchestration

**BENCH-13** — *3-step ELT pipeline* ★
- Task type: `etl_orchestration`
- Prompt: `Design a 3-step ELT pipeline: Postgres → DuckDB → Streamlit dashboard`
- Success criteria: Covers all three stages with concrete tooling choices, data flow between stages, scheduling, error handling, and how the dashboard reads from DuckDB. Each step is actionable.
- Complexity: Medium-High. Multi-system design, integration points.

**BENCH-14** — *Ingestion DAG with retries*
- Task type: `etl_orchestration`
- Prompt: `Orchestrate a multi-source ingestion DAG with retry logic and alerting`
- Success criteria: DAG structure with multiple sources, retry policy (backoff, max attempts), alerting integration (on failure, on SLA breach), idempotency considerations.
- Complexity: Medium. Standard pattern but requires completeness.

**BENCH-15** — *Backfill strategy*
- Task type: `etl_orchestration`
- Prompt: `Design a backfill strategy for 2 years of historical event data`
- Success criteria: Covers: chunking/partitioning approach, parallelism level, idempotency, progress tracking, validation per chunk, rollback plan, estimated timeline reasoning.
- Complexity: Medium-High. Operational planning, trade-off reasoning.

---

### Category: transformation

**BENCH-16** — *pandas to PySpark* ★
- Task type: `transformation`
- Prompt: `Convert this pandas transform to a PySpark equivalent with partitioning`
- Success criteria: Produces PySpark code using DataFrame API (not RDD), includes explicit partitioning (repartition or partitionBy), handles the pandas-to-Spark semantic differences (e.g., no in-place mutation, lazy evaluation). Code is syntactically plausible.
- Complexity: Medium-High. Requires API translation + distributed computing concepts.

**BENCH-17** — *Cursor to dbt*
- Task type: `transformation`
- Prompt: `Rewrite a row-by-row SQL cursor as a set-based dbt model`
- Success criteria: Demonstrates understanding of why cursors are slow, provides set-based equivalent using joins/aggregations/window functions, structured as a dbt model (config block, refs). Explains the transformation logic.
- Complexity: Medium. Paradigm shift (procedural → declarative).

**BENCH-18** — *BigQuery optimization*
- Task type: `transformation`
- Prompt: `Optimize a 10-table star schema join query for BigQuery`
- Success criteria: Covers BigQuery-specific optimizations: partition pruning, clustering, avoiding cross joins, join order, SELECT only needed columns, approximate aggregation where acceptable. Actionable recommendations.
- Complexity: Medium-High. Requires platform-specific knowledge.

---

## Harness Code for AIR Adapter

The AIR adapter should implement the same function signature as the original frameworks:

```python
def run_air_task(task_type: str, task_name: str) -> dict:
    """Run a task through AIR and return benchmark-shaped result."""
    import time
    start = time.time()
    try:
        # 1. AIR allocation decides the strategy
        strategy = air.allocate_strategy(task_name)
        # 2. Execute via AIR's runtime using the allocated strategy
        #    (adapter builder: wire this to air's execution path)
        output, tokens = execute_with_air(task_name, strategy)
        latency = round(time.time() - start, 3)
        return {
            "framework": "AIR",
            "task_type": task_type,
            "task_name": task_name,
            "tokens_used": tokens,
            "latency_seconds": latency,
            "success": 1,
            "error_message": "",
            "output_preview": output[:300],
            "boilerplate_lines": 0,  # AIR: no per-task wiring needed
        }
    except Exception as exc:
        latency = round(time.time() - start, 3)
        return {
            "framework": "AIR",
            "task_type": task_type,
            "task_name": task_name,
            "tokens_used": 0,
            "latency_seconds": latency,
            "success": 0,
            "error_message": str(exc)[:200],
            "output_preview": "",
            "boilerplate_lines": 0,
        }
```

**Key design decision for adapter builder:** AIR's value proposition is that allocation is automatic (`air.allocate_strategy` needs only the goal text). The `boilerplate_lines` for AIR is effectively 0 per task — no graph definition, no crew wiring. This is a legitimate advantage to measure, not a cheat: it reflects the product difference.

## Harness Code for LangGraph Baseline

Reuse directly from `/tmp/bench-repos/afb/agent.py`:

```python
import sys
sys.path.insert(0, "/tmp/bench-repos/afb")
from agent import run_langgraph_task

result = run_langgraph_task("sql_generation", "Write a window function...")
```

**Critical:** Replace the `_llm_call` function in `agent.py` to use Nebius instead of Groq, so both frameworks use the same model. The function is at lines 62-70. Swap `client.chat.completions.create(model=MODEL, ...)` with a Nebius call using `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B`. Keep the token counting (sum prompt + completion tokens from the response).

## Comparison Protocol

1. Both frameworks use the **same LLM** (Nebius Nemotron-3-Nano-30B).
2. Run each of the 18 tasks **3 times** per framework (54 executions per framework) to account for LLM variance.
3. Record: success (crash-based), tokens, latency, output preview.
4. **Correctness scoring:** Use LLM-as-judge (separate Nebius call) with the per-task success criteria above as the rubric. Score each output 0/1 per criterion, or use a 1-5 scale.
5. Report: success rate, correctness rate, avg tokens, avg latency, per-category breakdown.
6. Statistical test: McNemar's test for paired success/correctness comparisons (same tasks, both frameworks).

## Excluded Tasks (and why)

From the original 24, excluded 6:
- `Write a window function...` (duplicate under `data_quality` — data entry error in original)
- `Create an incremental load query using watermark-based change detection` (sql_generation) — overlaps heavily with BENCH-01/02 concepts
- `Trace a silent data loss issue in a Spark streaming job` (pipeline_debugging) — requires Spark runtime context not available
- `Detect and quarantine late-arriving records in an event stream` (data_quality) — requires streaming infra context
- `Create a data dictionary for a 20-column clickstream schema` (metadata_generation) — no schema provided, too underspecified
- `Build a CDC pipeline from MySQL binlog to a Kafka topic` (etl_orchestration) — requires specific infra (MySQL, Kafka) not available
- `Translate a legacy SSIS package logic into a Python Airflow DAG` (transformation) — no SSIS package provided, too underspecified

Kept 18 that are fully self-contained as natural language prompts.
