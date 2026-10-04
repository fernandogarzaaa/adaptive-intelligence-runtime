"""Evidence grounding: what counts as verification, and what does not.

Evidence is a DETERMINISTIC PROJECTION of ``tool.completed`` ledger
events. There is no separate evidence table and no mutable evidence
state: the ledger stays the source of truth, and every Evidence object
can be rebuilt from it at any time.

Three layers, in order:

1. **Validity** (``validity.py``): did this actually happen? A
   ``tool.completed`` event counts as grounding evidence only if the
   event exists, ``payload.ok`` is true, the ``tool_calls`` row holds a
   persisted result whose recomputed hash matches
   ``payload.result_hash``, and the producing agent is not a simulator.
   A failed, interrupted, blocked, timed-out, or malformed tool
   execution can NEVER constitute grounding evidence. Validity is
   necessary but explicitly not sufficient.

2. **Relevance** (``relevance.py``): does it establish the claim?
   Structural only. Every claimed outcome must cite >=1 evidence
   reference; every cited reference must resolve to valid evidence;
   every cited evidence's verification scope must cover the claim's
   asserted artifacts and effects. Fail-closed: no citation, invalid
   evidence, or scope mismatch means not grounded.

3. **Outcome evaluation** (``grounding.py`` + the evaluator's
   ``outcome_grounding`` check): did the objective actually succeed?
   SUPPORTED requires every claimed outcome grounded through layers
   1 and 2.

TOOL_SUCCESS and CLAIM_SUPPORTED are never conflated: a successful
tool execution is only an *eligible evidence source*, never by itself
evidence of task success.

Explicit limitations (by design, not oversight):

- There is NO LLM relevance judge anywhere in this module. Relevance
  is structural: artifact identifiers and declared effects must
  match. A sophisticated attacker who fabricates coherent-but-false
  claim + artifact declarations (e.g. writes a file full of
  plausible-looking nonsense and claims the objective) is beyond the
  structural layer; this module will call that evidence valid and
  relevant. Semantic verification is deferred by design and, when
  built, must sit behind the assurance machinery and be
  adversarially evaluated itself.
- Unknown / opaque tools (``shell.exec``, unregistered tools) carry
  empty target lists. They can NEVER be relevant evidence for an
  artifact claim, fail-closed.
- Evidence trusts the ledger as the root of trust. Direct DB-write
  attackers are outside the threat model (same standing assumption
  as the memory store's evidence_ref check).
"""

from air.evidence.grounding import ClaimGrounding, ground_claims
from air.evidence.model import ClaimOutcome, Evidence, VerificationScope
from air.evidence.relevance import check_relevance
from air.evidence.validity import build_evidence, valid_evidence_for_run

__all__ = [
    "ClaimGrounding",
    "ClaimOutcome",
    "Evidence",
    "VerificationScope",
    "build_evidence",
    "check_relevance",
    "ground_claims",
    "valid_evidence_for_run",
]
