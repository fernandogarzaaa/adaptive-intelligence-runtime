"""AIR Python library examples.

Run with: python examples/library_usage.py
"""

import air

print("=== 1. Basic allocation ===")
strategy = air.allocate_strategy("Write a summary of the repository")
print(f"Strategy: {strategy}")
print()

print("=== 2. Explanation ===")
print(air.allocation_explanation("Research three competitors and compare"))
print()

print("=== 3. v1 baseline plan (full CognitivePlan) ===")
plan = air.allocate("Summarize this codebase", agent_budget=2)
print(f"Strategy: {plan.strategy}")
print(f"Agent specs: {len(plan.agent_specs)}")
print(f"Scores: {plan.scores}")
print()

print("=== 4. Frozen policy info ===")
policy = air.frozen_policy()
print(f"Version: {policy['version']}, Rules: {policy['n_rules']}")
print(f"Frozen: {policy['frozen']}")
print()

print("=== 5. Batch allocation ===")
goals = [
    "Write unit tests for the parser",
    "Research five LLM providers and compare pricing",
    "Design a database schema for user profiles",
]
for g in goals:
    s = air.allocate_strategy(g)
    print(f"  [{s:20}] {g}")
