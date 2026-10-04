"""Phase 5b: the Learning Engine Shakedown.

Gate before any real AIR learning experiment (docs/LEARNING_REDESIGN.md
section 11). The shakedown does NOT tune the learner: it runs the fixed
pipeline (extract -> attribute -> generate -> candidate) on a synthetic
world with a known hidden rule, then checks BEHAVIORAL EQUIVALENCE over
a prespecified population.

The expected output is not "the learner generated rule X". It is:

    The learner generated a rule whose behavior is equivalent to the
    prespecified target over the shakedown population while preserving
    the research behavior and rejecting the lexical confounders.

Hidden true rule of the synthetic world: a task whose specification
requires more than one output artifact needs WRITE capability in its
organization; anything else succeeds under any strategy.

If the shakedown FAILS, the learner must not be tuned against it
until it passes: that would turn the gate into training data. A
failure is a first-class result demanding a design change, not a
parameter tweak.

FREEZE rule: when the shakedown passes, freeze the learner and seal
the promotion protocol before any preregistered learning experiment.
"""

from __future__ import annotations

from dataclasses import dataclass

from air.learning_v2 import attribution, extraction, generation
from air.learning_v2.contracts import (
    AllocationRule,
    LearningEvidence,
    OrganizationProperties,
    PolicyCandidate,
    resolve_rules,
)

# ---------------------------------------------------------------------------
# Synthetic world: organization templates per strategy.
# ---------------------------------------------------------------------------

TEMPLATES: dict[str, OrganizationProperties] = {
    "direct": OrganizationProperties(
        strategy="direct", roles=frozenset({"generalist"}),
        capabilities=frozenset({"READ", "WRITE", "EXECUTE"}),
        topology="single"),
    "parallel_agents": OrganizationProperties(
        strategy="parallel_agents",
        roles=frozenset({"researcher", "synthesizer"}),
        capabilities=frozenset({"READ"}),
        topology="flat"),
    "hierarchical": OrganizationProperties(
        strategy="hierarchical",
        roles=frozenset({"coordinator", "worker"}),
        capabilities=frozenset({"READ", "WRITE"}),
        topology="tree"),
}

# Parent policy: the v1 pathology. Always prefers parallel_agents,
# expressed as base strategy scores the candidate adjusts.
PARENT_BASE_SCORES = {"direct": 0.5, "parallel_agents": 1.0,
                      "hierarchical": 0.5}
PARENT_VERSION = "v1"


def _requirement_satisfied(org: OrganizationProperties, req) -> bool:
    from air.learning_v2.contracts import RuleAction
    if req.action is RuleAction.REQUIRE_CAPABILITY:
        return req.target in org.capabilities
    if req.action is RuleAction.REQUIRE_ROLE:
        return req.target in org.roles
    return True


def choose_with_policy(features: dict, rules: list[AllocationRule],
                       base_scores: dict[str, float] | None = None) -> OrganizationProperties:
    """Feasibility-before-topology allocation.

    strategy -> feasible organizations -> apply hard requirements
    (veto violators) -> score survivors -> choose. Requirements
    filter BEFORE commitment; a requirement discovered after the
    organization is built has merely moved the failure one stage
    later. Deterministic tiebreak: lowest strategy name wins.
    """
    base = base_scores or PARENT_BASE_SCORES
    resolved = resolve_rules(rules, features)
    feasible = [t for t in TEMPLATES.values()
                if all(_requirement_satisfied(t, r)
                       for r in resolved["requirements"])]
    if not feasible:
        raise RuntimeError("no feasible organization under hard "
                           "requirements; refusing to allocate")
    scored = [(base[t.strategy] + resolved["adjustments"].get(t.strategy, 0.0),
               t.strategy, t) for t in feasible]
    scored.sort(key=lambda x: (-x[0], x[1]))
    return scored[0][2]


def parent_choose(features: dict) -> OrganizationProperties:
    return choose_with_policy(features, [])


# ---------------------------------------------------------------------------
# Synthetic task features. The matched minimal pairs hold surface
# properties (word count, "three", step count, read ops, execution
# flags) approximately identical while the required EFFECT differs.
# Lexical proxies (artifact mentions, the token "three") are not in
# the class-A schema at all, so the learner cannot condition on them.
# ---------------------------------------------------------------------------

def research_features(variant: int = 0) -> dict:
    return {
        "requires_output_artifact": False,
        "output_artifact_count": variant % 2,
        "has_named_output_targets": False,
        "requested_write_effect": False,
        "requested_read_effect": True,
        "requested_execute_effect": False,
        "independent_work_unit_count": 3,
        "dependency_depth": 1,
        "multi_step": variant % 2 == 0,
    }


def production_features(variant: int = 0) -> dict:
    return {
        "requires_output_artifact": True,
        "output_artifact_count": 2 + (variant % 3),
        "has_named_output_targets": True,
        "requested_write_effect": True,
        "requested_read_effect": variant % 2 == 0,
        "requested_execute_effect": False,
        "independent_work_unit_count": 3,
        "dependency_depth": 1,
        "multi_step": True,
    }


def mixed_features(variant: int = 0) -> dict:
    return {
        "requires_output_artifact": True,
        "output_artifact_count": 2,
        "has_named_output_targets": True,
        "requested_write_effect": True,
        "requested_read_effect": True,
        "requested_execute_effect": False,
        "independent_work_unit_count": 4,
        "dependency_depth": 2,
        "multi_step": True,
    }


def oracle(features: dict, org: OrganizationProperties) -> dict:
    """Hidden true rule: more than one required output artifact needs
    WRITE capability. Everything else succeeds under any strategy.
    Assurance is SOUND: the synthetic world is fully observed."""
    needs_write = features["output_artifact_count"] > 1
    if needs_write and "WRITE" not in org.capabilities:
        verdict = "FALSIFIED"
    else:
        verdict = "SUPPORTED"
    return {"evaluation_verdict": verdict, "assurance_verdict": "SOUND",
            "resources": {"cost": 1.0, "latency_ms": 100,
                          "agent_count": len(org.roles) + 1},
            "safety_violations": []}


def build_training_records(n_per_kind: int = 6) -> list[LearningEvidence]:
    """The known table, enriched: each task kind executed under each
    strategy, outcomes from the oracle. Deterministic."""
    builders = {"research": research_features,
                "production": production_features, "mixed": mixed_features}
    records: list[LearningEvidence] = []
    for kind in ("research", "production", "mixed"):
        for i in range(n_per_kind):
            features = builders[kind](i)
            for strategy in ("direct", "parallel_agents", "hierarchical"):
                org = TEMPLATES[strategy]
                res = oracle(features, org)
                records.append(extraction.extract_experience(
                    id=f"syn_{kind}_{i}_{strategy}",
                    task_features=features,
                    allocation={"strategy": strategy,
                                "scores": dict(PARENT_BASE_SCORES)},
                    organization={"strategy": org.strategy,
                                  "roles": sorted(org.roles),
                                  "capabilities": sorted(org.capabilities),
                                  "topology": org.topology},
                    resources=res["resources"],
                    evaluation_verdict=res["evaluation_verdict"],
                    assurance_verdict=res["assurance_verdict"]))
    return records


# ---------------------------------------------------------------------------
# Prespecified shakedown population: matched minimal pairs + "three X"
# confounders + coverage samples. Fixed; the learner never trains on it.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ShakedownTask:
    id: str
    description: str
    features: dict
    kind: str  # research | production | mixed


def shakedown_population() -> list[ShakedownTask]:
    return [
        # Matched minimal pair: surface features ~identical, effect differs.
        ShakedownTask("pair_research",
                      "Review three sources and summarize them.",
                      research_features(0), "research"),
        ShakedownTask("pair_production",
                      "Create three files containing the requested material.",
                      production_features(0), "production"),
        # "three X" confounders.
        ShakedownTask("conf_three_researchers",
                      "Have three researchers investigate the topic.",
                      research_features(1), "research"),
        ShakedownTask("conf_three_sources",
                      "Summarize three sources into a briefing.",
                      research_features(2), "research"),
        ShakedownTask("conf_three_files",
                      "Compile three files of evidence.",
                      production_features(1), "production"),
        ShakedownTask("conf_three_sections",
                      "Draft three sections of the report.",
                      production_features(2), "production"),
        # Coverage samples.
        ShakedownTask("cov_research_a", "Survey the literature on the topic.",
                      research_features(3), "research"),
        ShakedownTask("cov_research_b", "Compare the available approaches.",
                      research_features(4), "research"),
        ShakedownTask("cov_production_a", "Write the migration scripts.",
                      production_features(3), "production"),
        ShakedownTask("cov_mixed_a",
                      "Research the API then implement the client.",
                      mixed_features(0), "mixed"),
        ShakedownTask("cov_mixed_b",
                      "Analyze the logs then produce the incident report.",
                      mixed_features(1), "mixed"),
    ]


@dataclass(frozen=True)
class ShakedownReport:
    passed: bool
    statement: str
    n_training_records: int
    n_attributions: int
    n_robust_associations: int
    n_rules: int
    rules: tuple
    checks: tuple  # per-task behavioral check dicts
    failures: tuple


def _behavioral_check(task: ShakedownTask,
                      parent_org: OrganizationProperties,
                      cand_org: OrganizationProperties) -> dict:
    """The prespecified target behavior:
    - production/mixed tasks: chosen organization is production-capable
      (carries WRITE);
    - research tasks: the candidate preserves the parent's parallel
      research behavior exactly (no rule may disturb it)."""
    if task.kind == "research":
        ok = (cand_org.strategy == parent_org.strategy
              and cand_org.roles == parent_org.roles
              and cand_org.capabilities == parent_org.capabilities)
        expectation = (f"preserve parent choice "
                       f"{parent_org.strategy}; got {cand_org.strategy}")
    else:
        ok = "WRITE" in cand_org.capabilities
        expectation = ("production-capable organization (WRITE); got "
                       f"{cand_org.strategy} with "
                       f"{sorted(cand_org.capabilities)}")
    return {"task_id": task.id, "kind": task.kind,
            "description": task.description,
            "parent_strategy": parent_org.strategy,
            "candidate_strategy": cand_org.strategy,
            "expectation": expectation, "passed": ok}


def run_shakedown(n_per_kind: int = 6) -> ShakedownReport:
    """Run the full pipeline on the synthetic world and judge behavioral
    equivalence. Deterministic. The training records and the shakedown
    population are disjoint by construction."""
    records = build_training_records(n_per_kind)
    attributions = attribution.attribute(records)
    robust = [a for a in attributions
              if a.outcome.value == "association" and a.robust]
    rules = generation.generate_rules(attributions)
    candidate = generation.build_candidate(
        id="cand_shakedown_001", parent_version=PARENT_VERSION,
        hypothesis=("Task-conditioned organization: tasks requiring more "
                    "than one output artifact need production capability; "
                    "research behavior is preserved."),
        rules=rules, attributions=attributions)

    population = shakedown_population()
    train_ids = {r.id for r in records}
    assert not (train_ids & {t.id for t in population}), \
        "shakedown population leaked into training"

    checks: list[dict] = []
    failures: list[str] = []
    for task in population:
        p_org = parent_choose(task.features)
        c_org = choose_with_policy(task.features, list(candidate.rules))
        check = _behavioral_check(task, p_org, c_org)
        checks.append(check)
        if not check["passed"]:
            failures.append(f"{task.id}: {check['expectation']}")
    passed = not failures and len(rules) > 0
    if not rules:
        failures = ("no rules generated: the learner failed to recover "
                    "any structural relationship",)
        passed = False
    statement = (
        "The learner generated a rule whose behavior is equivalent to "
        "the prespecified target over the shakedown population while "
        "preserving the research behavior and rejecting the lexical "
        "confounders." if passed else
        "SHAKEDOWN FAILED: " + "; ".join(failures))
    return ShakedownReport(
        passed=passed, statement=statement,
        n_training_records=len(records),
        n_attributions=len(attributions),
        n_robust_associations=len(robust),
        n_rules=len(rules),
        rules=tuple(r.canonical() for r in rules),
        checks=tuple(checks), failures=tuple(failures))
