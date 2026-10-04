"""Scripted role behaviors for the baseline experiment.

One generic behavior serves every role in every condition. Competence
is therefore identical across conditions by construction: the ONLY
thing that varies is organization (which roles exist, which phases
each role executes). This is the documented test control for an
environment with no model provider.

The behavior signature is exactly ``(agent, runtime)``. It takes no
condition label and receives none: task lookup is by run_id through
the driver-maintained TASK_BY_RUN map, and the task payload contains
no condition information (asserted by test_experiments.py).

Role -> phase mapping (fixed, defined once, before the task set is
seen):
- specialist / planner: all phases (generalists; the planner is a
  coordinator that can also execute directly, which lets the
  ADAPTIVE_SPAWN strategy's lone planner function without mid-run
  spawning — the shakedown does not exercise spawning)
- researcher: prepare (read inputs)
- coder: produce (write outputs)
- synthesizer / verifier / critic: verify (re-read outputs)

Simulated agents (epistemic_kind in NON_EVIDENTIARY, e.g. the
SIMULATION_FIRST strategy's simulator) execute nothing and claim
nothing: a simulator's tool calls are not real-world evidence
(Invariant 12), and declaring claims would poison the run with
ungrounded outcomes.

Claim declaration: for each task claim whose phase the agent
executed AND for which it holds at least one successful tool call,
the agent declares the claim citing its own tool call ids
(``tc_...`` refs resolve run-scoped in build_evidence). A claim is
only as good as its citations; the evaluation boundary decides
grounding, never the behavior.
"""

from __future__ import annotations

from air.experience.provenance import NON_EVIDENTIARY, Provenance

# Fixed role -> phase mapping. Defined once; not tuned per task.
# The planner is a generalist coordinator: it can execute any phase
# directly (this is what lets the ADAPTIVE_SPAWN strategy's lone
# planner function without mid-run spawning, which the shakedown does
# not exercise). The pipeline roles keep a strict division of labor.
ROLE_PHASES: dict[str, tuple[str, ...]] = {
    "specialist": ("prepare", "produce", "verify"),
    "planner": ("prepare", "produce", "verify"),
    "researcher": ("prepare",),
    "coder": ("produce",),
    "synthesizer": ("verify",),
    "verifier": ("verify",),
    "critic": ("verify",),
}

# Populated by the experiment driver before start_run. Maps run_id ->
# {"task": <task dict>, }. Never carries a condition label.
TASK_BY_RUN: dict[str, dict] = {}


def _ns_path(run_id: str, template: str) -> str:
    """Namespace a task-relative path into the run's workspace area."""
    return f"{run_id}/{template}"


def _ns_args(run_id: str, tool: str, args: dict) -> dict:
    out = dict(args)
    if "path" in out and isinstance(out["path"], str):
        out["path"] = _ns_path(run_id, out["path"])
    if tool == "shell.exec":
        out["cwd"] = run_id
    return out


async def experiment_behavior(agent, rt) -> dict:
    """The single behavior for every role in every condition."""
    entry = TASK_BY_RUN.get(agent.root_run_id)
    if entry is None:
        return {"ok": False, "error": "no task registered for run"}
    task = entry["task"]
    run_id = agent.root_run_id

    # Simulators do not produce real-world evidence and must not claim
    # real-world outcomes (Invariant 12).
    kind = getattr(agent, "epistemic_kind", "OBSERVED")
    try:
        if Provenance(str(kind)) in NON_EVIDENTIARY:
            return {"ok": True, "simulated": True}
    except ValueError:
        pass

    phases = ROLE_PHASES.get(agent.role, ("prepare", "produce", "verify"))
    # Successful tool call ids per phase, for claim citation.
    evidenced: dict[str, list[str]] = {}
    for op in task.get("operations", []):
        if op.get("phase") not in phases:
            continue
        try:
            record = await rt.call_tool(
                agent.id, op["tool"], _ns_args(run_id, op["tool"], op["args"]))
        except Exception:
            # Budget exhaustion, denial, etc.: the failure is recorded
            # in the ledger by the runtime; the behavior simply has no
            # evidence for this operation.
            continue
        # verification_status is PASSED iff the tool's raw result had
        # ok=true (the record's `result` field is not populated on the
        # returned object; the event payload carries ok separately).
        # Only genuinely successful executions become evidence.
        ok = record.verification_status == "PASSED"
        if ok:
            evidenced.setdefault(op["phase"], []).append(record.id)

    outcomes = []
    for claim in task.get("claims", []):
        refs: list[str] = []
        for phase in phases:
            if claim.get("phase") == phase:
                refs.extend(evidenced.get(phase, []))
        if not refs:
            # No successful evidence for this claim's phase: do not
            # declare it. An uncited claim can never be grounded
            # (fail-closed), so declaring it would only add noise.
            continue
        outcomes.append({
            "id": claim["id"],
            "description": claim.get("description", ""),
            "artifacts": [_ns_path(run_id, a)
                          for a in claim.get("artifacts", [])],
            "effects": list(claim.get("effects", [])),
            "evidence_refs": refs,
        })
    result: dict = {"ok": True}
    if outcomes:
        result["outcomes"] = outcomes
    return result
