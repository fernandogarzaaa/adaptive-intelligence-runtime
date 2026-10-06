# Adaptive Intelligence Runtime (AIR)

AIR is an intelligence runtime that decides **how to organize cognition** for a given goal — not just what to think, but what *thinking structure* should exist. Given a task, it allocates single agents, parallel teams, or hierarchical organizations, learns from verified experience, and promotes only independently validated improvements.

> The runtime may change how it thinks, but it must never manufacture the evidence that proves the change was beneficial.

## Status

**Research platform** (v0.1.0). AIR is an experimental system for studying adaptive cognitive organization, not a production product. It works, it's tested (277 tests), and the core allocation-learning loop is validated through preregistered experiments — but expect research-grade rough edges, not polished UX.

**Validated through:** D-series (learning + transfer) → OOD-1 (lexical robustness) → CTC-1 (counterfactual capability sensitivity) → PV-1 (real-model validation). See `docs/RESEARCH_LOG.md`.

## What It Does

Most AI systems use a fixed architecture: one model, one prompt, one shot. AIR treats **cognitive organization as a decision**:

- **Allocation:** Given a goal, choose `single_agent`, `parallel_agents`, or `hierarchical_agents` based on learned rules about task structure — not just keywords.
- **Learning:** From verified execution outcomes, learn allocation rules (e.g., "production tasks needing WRITE should use single_agent because parallel orgs can't satisfy the capability").
- **Assurance:** An independent evaluator verifies outcomes. The learner never grades its own homework.
- **Multi-model:** The allocation policy is model-agnostic. It reasons about organizational capabilities (READ, WRITE, EXECUTE), not specific providers.

## Installation

**Requirements:** Python 3.12+, git.

```bash
git clone https://github.com/fernandogarzaaa/adaptive-intelligence-runtime.git
cd adaptive-intelligence-runtime
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

Verify:

```bash
.venv/bin/python -m air.cli.main doctor
```

You should see `[ok]` for python, data_dir, and database. Provider checks may fail if no model backend is configured — that's expected (see below).

**With Docker** (for reproducibility):

```bash
docker build -f docker/Dockerfile -t air .
docker run -p 8765:8765 air
```

> Note: the Dockerfile is provided for reproducibility but hasn't been build-verified in all environments. The venv install above is the tested path.

## Quick Start

### 1. Run a task (scripted mode)

Without a model provider, AIR runs with deterministic scripted agents — useful for testing the allocation logic itself:

```bash
.venv/bin/python -m air.cli.main run "Write a summary of this repository's structure"
.venv/bin/python -m air.cli.main runs    # list runs
.venv/bin/python -m air.cli.main agents  # list agents
```

### 2. Start the API server

```bash
.venv/bin/python -m air.cli.main start
```

Open `http://127.0.0.1:8765` for the API. The web console source is in `web/` (React + TypeScript; build separately).

### 3. With a real model provider

AIR supports pluggable model providers. Without one, agent execution honestly reports `MODEL_PROVIDER_UNAVAILABLE` instead of faking output.

Configure via environment (see `src/air/config.py` for all options):

```bash
# Example: Ollama (local)
export AIR_PROVIDER=ollama
export AIR_PROVIDER_BASE_URL=http://localhost:11434
```

Provider implementations live in `src/air/providers/`.

## Use Cases

**For AI researchers:** Study how learned allocation policies transfer across task distributions, respond to counterfactual interventions, and behave with real vs scripted execution. The `src/air/experiments/` harness provides preregistered experiment infrastructure with frozen protocols, hash-linked artifacts, and mechanical verification.

**For agent system builders:** Use AIR's allocation logic to route tasks to appropriate organizational structures. The policy (`pv2`, 12 frozen rules) demonstrates task-conditioned cognitive allocation that responds to capability requirements rather than surface cues.

**For evaluation:** The independent assurance architecture (`src/air/assurance/`, `src/air/evaluation/`) provides a template for separating *doing* from *verifying* — the evaluator checks artifacts mechanically and never inspects agent reasoning.

## How Allocation Works

```
Goal text
    ↓
Feature extraction (mechanical: effects, artifacts, operations)
    ↓
v1 baseline scores (lexical heuristics)
    ↓
pv2 rule application (mechanical adjustments + hard requirements)
    ↓
Feasibility check (can each org satisfy required capabilities?)
    ↓
Strategy selection (highest feasible score)
```

The frozen `pv2` policy (12 rules) was learned from verified experience in the D-series experiments and validated through OOD-1 (lexical shift), CTC-1 (counterfactual topology), and PV-1 (real-model execution). It is intentionally frozen — not updated — to serve as a stable reference.

## Project Layout

- `src/air/` — Backend (FastAPI, SQLite/WAL, asyncio)
  - `allocation/` — Strategy scoring and selection
  - `learning_v2/` — Experience learning and rule generation
  - `evaluation/`, `assurance/` — Independent verification
  - `experiments/` — Preregistered experiment harness (D-series, OOD-1, CTC-1, PV-1)
  - `providers/` — Model provider integrations
  - `cli/` — Command-line interface
  - `api/` — REST API
- `web/` — React + TypeScript console (build separately)
- `migrations/` — SQL migrations
- `tests/` — Unit, integration, structural (277 tests)
- `docs/` — Architecture, research protocols, experiment preregistrations

## Research Background

AIR's allocation policy was developed through a preregistered experimental sequence:

1. **D-series:** Learned `pv2` from verified experience. S1 showed significant improvement over baseline (p~6e-08); D2 confirmed convergence; D3 showed held-out transfer.
2. **OOD-1:** Tested lexical robustness. pv2 retained correct allocations after D-series trigger words were removed.
3. **CTC-1:** Counterfactual topology. With task text held byte-identical, pv2 switched allocations when organizational capabilities changed (12/12). Demonstrates sensitivity to capability structure, not just lexical cues.
4. **PV-1:** Real-model validation. With Nebius-hosted models, pv2's allocations outperformed baseline (8/10 complete pairs).

Each experiment is frozen with hash-linked artifacts in `docs/PREREGISTRATION_*.md`. The full log is in `docs/RESEARCH_LOG.md`.

**What AIR does NOT claim:** general intelligence, autonomous self-improvement, or a general causal theory of cognition. The validated claim is narrower: a learned allocation policy that responds to organizational capability structure under tested conditions.

## Honest Limitations

- **Research grade.** This is an experimental platform, not a polished product. Expect sharp edges.
- **Single-provider validation.** Real-model testing used one provider (Nebius). Cross-provider generality is untested.
- **Scripted baseline.** Most experiments used scripted agents; real-model validation is limited to PV-1's 10 complete pairs.
- **Frozen policy.** `pv2` is intentionally not updated. It's a research artifact, not a continuously improving system.
- **No PyPI.** Install from source only. `pip install adaptive-intelligence-runtime` is not yet available.

## Contributing

This is Fernando Garza's research project. Issues and PRs welcome, but the experimental protocols (`docs/PREREGISTRATION_*.md`) are frozen — don't modify sealed experiments.

## License

MIT. See `LICENSE`.
