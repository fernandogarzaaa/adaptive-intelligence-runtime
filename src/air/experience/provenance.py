"""Provenance: every artifact in AIR is labeled with how it came to be.

OBSERVED     — measured from real execution (tool results, test outcomes).
               Requires an evidence_ref. An LLM statement must NEVER be
               stored as OBSERVED.
DERIVED      — computed from observed data (aggregations, scores).
INFERRED     — model inference. Never an observed fact.
FORECAST     — predicted about the future (allocator estimates).
SIMULATED    — produced by a simulation; NEVER an observation.
HYPOTHETICAL — assumed for reasoning ("what if").
COUNTERFACTUAL — what would have happened under another strategy.
USER_ASSERTED — stated by the operator; trusted as an assertion, not a
               measurement.

The runtime must never confuse simulation with observation, nor inference
with measurement. Learning aggregations only consume OBSERVED and DERIVED.
"""

from enum import Enum


class Provenance(str, Enum):
    OBSERVED = "OBSERVED"
    DERIVED = "DERIVED"
    INFERRED = "INFERRED"
    FORECAST = "FORECAST"
    SIMULATED = "SIMULATED"
    HYPOTHETICAL = "HYPOTHETICAL"
    COUNTERFACTUAL = "COUNTERFACTUAL"
    USER_ASSERTED = "USER_ASSERTED"


# Provenance levels the learning engine is allowed to train on.
LEARNABLE = {Provenance.OBSERVED, Provenance.DERIVED}

# Provenance kinds that can never ground a verification verdict, an
# observation, or a capability claim. They may inform allocation and
# generate hypotheses, but simulation can never become evidence that a
# hypothesis is true in reality. Enforced at the evaluation, assurance,
# memory, and experience boundaries (Invariant #12).
NON_EVIDENTIARY = frozenset({
    Provenance.SIMULATED,
    Provenance.FORECAST,
    Provenance.HYPOTHETICAL,
    Provenance.COUNTERFACTUAL,
})

# Trust caps: no matter the stated confidence, these provenances cannot be
# trusted beyond the cap. Applied at retrieval time, recorded in metadata.
TRUST_CAP = {
    Provenance.SIMULATED: 0.3,
    Provenance.HYPOTHETICAL: 0.3,
    Provenance.COUNTERFACTUAL: 0.3,
    Provenance.FORECAST: 0.4,
    Provenance.INFERRED: 0.6,
}
