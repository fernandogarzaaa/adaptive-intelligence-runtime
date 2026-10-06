# Adaptive Intelligence Runtime (AIR)

AIR decides **how to organize cognition** for a given goal — not just what to think, but what *thinking structure* should exist. Given a task, it allocates single agents, parallel teams, or hierarchical organizations using a validated policy learned from verified experience.

> The runtime may change how it thinks, but it must never manufacture the evidence that proves the change was beneficial.

## Install

```bash
pip install adaptive-intelligence-runtime
```

**Requirements:** Python 3.12+.

## Quick Start

```bash
air init                                  # guided setup (provider, sanity check)
air allocate "Research three competitors"  # see the cognitive plan, no execution
```

Or as a Python library:

```python
import air

strategy = air.allocate_strategy("Write API documentation")
print(strategy)  # single_agent

print(air.allocation_explanation("Research three competitors"))
# v1 baseline scores, pv2 adjustments, chosen strategy, rules fired
```

See `docs/QUICKSTART.md` for the 5-minute guide and `examples/library_usage.py` for more.

## What It Does

Most AI systems use a fixed architecture: one model, one prompt, one shot. AIR treats **cognitive organization as a decision**:

- **Allocation:** Given a goal, choose `single_agent`, `parallel_agents`, or `hierarchical_agents` based on learned rules about task structure — not just keywords.
- **Learning:** From verified execution outcomes, learn allocation rules (e.g., "production tasks needing WRITE should use single_agent because parallel orgs can't satisfy the capability").
- **Assurance:** An independent evaluator verifies outcomes. The learner never grades its own homework.
- **Multi-model:** The allocation policy is model-agnostic. It reasons about organizational capabilities (READ, WRITE, EXECUTE), not specific providers.

## CLI Reference

| Command | What it does |
|---------|--------------|
| `air init` | Guided first-time setup |
| `air allocate "goal"` | Show allocation plan (no execution) |
| `air run "goal"` | Submit goal, show plan, watch progress |
| `air start` | Start the backend API server |
| `air runs` / `air agents` | List runs and agents |
| `air capabilities` | List validated capabilities |
| `air doctor` | Check environment |
| `air config` | Show effective configuration |

## Python API

```python
import air

air.allocate("goal")              # full CognitivePlan (v1 baseline)
air.allocate_strategy("goal")     # strategy name (frozen pv2 policy)
air.allocation_scores("goal")     # v1 vs pv2 scores, fired rules
air.allocation_explanation("goal")# human-readable breakdown
air.frozen_policy()               # policy metadata (v2, 12 rules)
```

## Use Cases

**For developers:** `air run "your goal"` or `import air` — let the validated policy decide how to organize the work. Override with `--strategy` when you know better.

**For agent system builders:** Use AIR's allocation logic to route tasks to appropriate organizational structures. The frozen `pv2` policy (12 rules) demonstrates task-conditioned allocation that responds to capability requirements rather than surface cues.

**For AI researchers:** Study how learned allocation policies transfer across task distributions, respond to counterfactual interventions, and behave with real vs scripted execution. The `src/air/experiments/` harness provides preregistered experiment infrastructure with frozen protocols, hash-linked artifacts, and mechanical verification.

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

The frozen `pv2` policy (12 rules, bundled with the package) was learned from verified experience in the D-series experiments and validated through OOD-1 (lexical shift), CTC-1 (counterfactual topology), and PV-1 (real-model execution). It is intentionally frozen — not updated — to serve as a stable reference.

## With a Real Model Provider

Without a provider, agent execution honestly reports `MODEL_PROVIDER_UNAVAILABLE` instead of faking output. `air init` walks through setup.

```bash
# Ollama (local, no API key)
export AIR_PROVIDER=ollama

# Nebius (cloud, NVIDIA open models)
export AIR_PROVIDER=nebius
export NEBIUS_API_KEY=your-key
```

Provider implementations live in `src/air/providers/`. See `src/air/config.py` for all options.

## Research Background

AIR's allocation policy was developed through a preregistered experimental sequence:

1. **D-series:** Learned `pv2` from verified experience. S1 showed significant improvement over baseline (p~6e-08); D2 confirmed convergence; D3 showed held-out transfer.
2. **OOD-1:** Tested lexical robustness. pv2 retained correct allocations after D-series trigger words were removed.
3. **CTC-1:** Counterfactual topology. With task text held byte-identical, pv2 switched allocations when organizational capabilities changed (12/12). Demonstrates sensitivity to capability structure, not just lexical cues.
4. **PV-1:** Real-model validation (incomplete). Interrupted by provider infrastructure after 19/24 runs. Across 9 complete pairs, pv2 won 8, v1 won 0, 1 tied. The preregistered 9/12 threshold was not reached.

Each experiment is frozen with hash-linked artifacts in `docs/PREREGISTRATION_*.md`. The full log is in `docs/RESEARCH_LOG.md`.

**What AIR does NOT claim:** general intelligence, autonomous self-improvement, or a general causal theory of cognition. The validated claim is narrower: a learned allocation policy that responds to organizational capability structure under tested conditions.

## Honest Limitations

- **Young product.** v0.2.0 is the first product release. The core allocation logic is validated; the UX is still maturing.
- **Single-provider validation.** Real-model testing used one provider (Nebius). Cross-provider generality is untested.
- **PV-1 incomplete.** Real-model validation was interrupted by infrastructure issues. Results are directional, not conclusive.
- **Frozen policy.** `pv2` is intentionally not updated. It's a validated artifact, not a continuously improving system.
- **Web console.** The React console in `web/` is source only; build separately.

## Project Layout

- `src/air/` — Runtime (FastAPI, SQLite/WAL, asyncio)
  - `allocation/` — Strategy scoring and selection
  - `learning_v2/` — Experience learning and rule generation
  - `evaluation/`, `assurance/` — Independent verification
  - `experiments/` — Preregistered experiment harness
  - `providers/` — Model provider integrations
  - `cli/` — Command-line interface
  - `api/` — REST API
  - `data/` — Bundled frozen policy
- `web/` — React + TypeScript console (build separately)
- `docs/` — Architecture, research protocols, quickstart
- `examples/` — Library usage examples
- `tests/` — 277 tests

## Contributing

Issues and PRs welcome. The experimental protocols (`docs/PREREGISTRATION_*.md`) are frozen — don't modify sealed experiments.

## License

MIT. See `LICENSE`.
