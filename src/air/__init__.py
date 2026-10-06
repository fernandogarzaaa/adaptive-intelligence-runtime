"""Adaptive Intelligence Runtime (AIR).

Quick start:
    import air

    # Decide how to organize cognition for a goal
    plan = air.allocate("Write a summary of this repository")
    print(plan.strategy)  # Strategy.SINGLE_AGENT

    # Use the frozen pv2 policy (learned from verified experience)
    strategy = air.allocate_strategy("Research three competitors")
    print(air.allocation_explanation("Research three competitors"))

For full runs with execution, use the CLI (`air run`) or the API server.
"""

from __future__ import annotations

__version__ = "0.2.0"
__product__ = "Adaptive Intelligence Runtime"


def __getattr__(name: str):
    """Lazy-load the public API to keep `import air` fast."""
    if name in ("Strategy", "CognitivePlan", "allocate"):
        from air.allocation import allocator
        return getattr(allocator, name)
    if name == "allocate_strategy":
        from air._lib import allocate_strategy
        return allocate_strategy
    if name == "allocation_scores":
        from air._lib import allocation_scores
        return allocation_scores
    if name == "allocation_explanation":
        from air._lib import allocation_explanation
        return allocation_explanation
    if name == "frozen_policy":
        from air._lib import frozen_policy
        return frozen_policy
    raise AttributeError(f"module 'air' has no attribute {name!r}")


__all__ = [
    "__version__",
    "__product__",
    "Strategy",
    "CognitivePlan",
    "allocate",
    "allocate_strategy",
    "allocation_scores",
    "allocation_explanation",
    "frozen_policy",
]
