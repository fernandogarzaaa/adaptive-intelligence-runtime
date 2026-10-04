"""Provenance: every artifact in AIR is labeled with how it came to be.

OBSERVED     — measured from real execution (tool results, test outcomes).
DERIVED      — computed from observed data (aggregations, scores).
FORECAST     — predicted about the future (allocator estimates).
SIMULATED    — produced by a simulation; NEVER an observation.
HYPOTHETICAL — assumed for reasoning ("what if").
COUNTERFACTUAL — what would have happened under another strategy.

The runtime must never confuse simulation with observation. Learning
aggregations only consume OBSERVED and DERIVED records.
"""

from enum import Enum


class Provenance(str, Enum):
    OBSERVED = "OBSERVED"
    DERIVED = "DERIVED"
    FORECAST = "FORECAST"
    SIMULATED = "SIMULATED"
    HYPOTHETICAL = "HYPOTHETICAL"
    COUNTERFACTUAL = "COUNTERFACTUAL"


# Provenance levels the learning engine is allowed to train on.
LEARNABLE = {Provenance.OBSERVED, Provenance.DERIVED}
