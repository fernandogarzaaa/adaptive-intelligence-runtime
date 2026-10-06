# AIR Quickstart (5 minutes)

AIR decides how to organize AI work for your goal: one agent, parallel agents,
or a hierarchy. It uses a validated policy (pv2) learned from verified experience.

## Install

```bash
pip install adaptive-intelligence-runtime
```

## Setup

```bash
air init
```

This creates `~/.air/`, asks for your model provider (Ollama for local,
Nebius for cloud), and runs a sanity check. No provider needed for allocation.

## See the plan (no execution)

```bash
air allocate "Research three competitors and compare their pricing"
```

Output shows v1 baseline scores, pv2 adjustments, and the chosen strategy:

```
v1 baseline scores:
  parallel_agents: 0.950
  single_agent: 0.525
  ...

v1 choice:  parallel_agents
pv2 choice: parallel_agents
```

## Run with execution

Start the backend, then submit goals:

```bash
air start          # in one terminal
air run "Summarize the README of this repo"   # in another
```

`air run` shows the allocation plan, submits the run, and watches progress.

## Python library

```python
import air

# Just the strategy name
strategy = air.allocate_strategy("Write API documentation")
print(strategy)  # single_agent

# Full explanation
print(air.allocation_explanation("Research three competitors"))

# Scores for debugging
scores = air.allocation_scores("My goal here")
print(scores["pv2_choice"], scores["fired_rules"])

# The validated policy
print(air.frozen_policy())
# {'version': 'v2', 'n_rules': 12, 'frozen': True, ...}
```

## When to use which strategy

| Strategy | Use when |
|----------|----------|
| `single_agent` | One coherent task, direct reasoning suffices |
| `parallel_agents` | Independent subtasks that can run concurrently |
| `hierarchical_agents` | Complex task needing decomposition and oversight |

AIR picks automatically. Override with `--strategy` on `air run`
or `force_strategy` in `air.allocate()`.

## Next steps

- `air doctor` — check your environment
- `air runs` — list recent runs
- `air capabilities` — see validated capabilities
- Full docs: https://github.com/fernandogarzaaa/adaptive-intelligence-runtime
