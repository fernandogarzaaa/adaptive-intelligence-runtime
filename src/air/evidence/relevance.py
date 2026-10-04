"""Layer 2 — Evidence Relevance: does it establish the claim?

Structural only, no semantic judgment. For one claimed outcome and
the valid evidence it cites:

- every claimed outcome must cite >=1 evidence reference;
- every cited reference must resolve to *valid* evidence (layer 1);
- every asserted artifact must appear in the targets of >=1 cited
  evidence's verification scope;
- every asserted effect must be covered by >=1 cited evidence's
  effect (``create/modify`` covers ``create`` and ``modify``).

Fail-closed on every axis: no citation, an invalid citation, an
uncovered artifact, or an uncovered effect means the outcome is not
grounded. A claim that declares no artifacts and no effects cannot
be grounded structurally: there is nothing to relate evidence to,
and vague prose is not evidence.

What this layer cannot do (documented, by design): it cannot tell
whether the artifact's *content* actually achieves the objective. A
``fs.write`` of plausible-looking nonsense to the right filename is
valid, relevant evidence under this layer. Content-level truth is
beyond structural grounding; see the module docstring.
"""

from __future__ import annotations

from air.evidence.model import ClaimOutcome, Evidence

# Evidence effect -> claim effects it covers.
_EFFECT_COVERAGE: dict[str, set[str]] = {
    "observe": {"observe"},
    "create/modify": {"create", "modify"},
    "execute": {"execute"},
    "delete": {"delete"},
}


def check_relevance(outcome: ClaimOutcome,
                    cited: list[Evidence]) -> tuple[bool, list[str]]:
    """Decide whether cited (already validated) evidence grounds the outcome."""
    reasons: list[str] = []
    if not outcome.evidence_refs:
        return False, [f"claim {outcome.claim_id}: no evidence cited;"
                       " an uncited claim is never grounded"]
    if not outcome.artifacts and not outcome.effects:
        return False, [f"claim {outcome.claim_id}: declares no artifacts"
                       " or effects; structural relevance has nothing to"
                       " relate evidence to"]
    for artifact in outcome.artifacts:
        if not any(artifact in ev.verification_scope.targets
                   for ev in cited):
            reasons.append(
                f"claim {outcome.claim_id}: artifact {artifact!r} is not"
                " among any cited evidence's targets"
                f" ({[ev.verification_scope.targets for ev in cited]})")
    for effect in outcome.effects:
        covered = any(
            effect in _EFFECT_COVERAGE.get(ev.verification_scope.effect, set())
            for ev in cited)
        if not covered:
            reasons.append(
                f"claim {outcome.claim_id}: effect {effect!r} is not covered"
                " by any cited evidence's effect"
                f" ({[ev.verification_scope.effect for ev in cited]})")
    if reasons:
        return False, reasons
    return True, [f"claim {outcome.claim_id}: {len(cited)} cited evidence"
                  f" cover artifacts {outcome.artifacts} and effects"
                  f" {outcome.effects}"]
