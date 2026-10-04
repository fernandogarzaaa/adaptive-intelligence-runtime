"""Learning engine v2: verdict-aware, rule-based, mechanically inspectable.

Implements docs/LEARNING_REDESIGN.md v2. The learner consumes
LearningEvidence (evaluation + assurance verdicts) and produces
PolicyCandidates (AllocationRules). It never sees run completion
status and never touches the allocator: the only path to the
allocator is Learner -> Candidate -> Evaluation -> Assurance ->
Promotion -> PolicyVersion -> Allocator.
"""

from air.learning_v2 import (  # noqa: F401
    attribution,
    contracts,
    evaluation,
    extraction,
    generation,
    promotion,
    shakedown,
)
