"""Layer 1 — Evidence Validity: did this actually happen?

A ``tool.completed`` event counts as grounding evidence ONLY if ALL of
the following hold:

- the event exists in the ledger and is a ``tool.completed`` event in
  the run being evaluated (cross-run citations are refused);
- ``payload.ok`` is true;
- ``payload.call_id`` and ``payload.result_hash`` are present;
- the ``tool_calls`` row exists, holds a persisted ``result_redacted``,
  and is in COMMITTED state;
- ``sha256(result_redacted.encode()).hexdigest()`` equals
  ``payload.result_hash`` (the hash is recomputed, never trusted);
- the producing agent's ``epistemic_kind`` is not in
  ``NON_EVIDENTIARY`` (a simulator's successful tool call is not
  real-world evidence; Invariant 12).

A failed, interrupted, blocked, timed-out, or malformed tool execution
can NEVER constitute grounding evidence.

This layer is necessary but explicitly not sufficient: validity says
the execution genuinely happened, nothing about whether it
establishes any claim. See ``relevance.py``.
"""

from __future__ import annotations

import hashlib
import json

from air.evidence.model import Evidence, VerificationScope
from air.experience.provenance import NON_EVIDENTIARY, Provenance

# Mechanical effect per known tool. Unknown tools are opaque: effect
# "execute" with no targets, which can never ground an artifact claim.
_TOOL_EFFECTS: dict[str, str] = {
    "fs.read": "observe",
    "fs.write": "create/modify",
    "shell.exec": "execute",
}

# Which redacted-arg fields identify the artifacts a tool acted on.
_TOOL_TARGET_ARGS: dict[str, list[str]] = {
    "fs.read": ["path"],
    "fs.write": ["path"],
}


def _scope_for(tool_name: str, args_redacted: str | None) -> VerificationScope:
    effect = _TOOL_EFFECTS.get(tool_name, "execute")
    targets: list[str] = []
    if args_redacted:
        try:
            args = json.loads(args_redacted)
        except (json.JSONDecodeError, TypeError):
            args = {}
        for field in _TOOL_TARGET_ARGS.get(tool_name, []):
            value = args.get(field)
            if isinstance(value, str) and value:
                targets.append(value)
    return VerificationScope(tool=tool_name, targets=targets, effect=effect)


def _resolve_source_event_id(conn, run_id: str, ref: str,
                             reasons: list[str]) -> str | None:
    """Resolve an evidence reference to a ledger event id.

    Accepts ``ev_<event_id>``, a raw event id, or a tool call id
    (``tc_...``). Returns None (with a reason) when the reference
    resolves to nothing, or to an event from a different run.
    """
    candidate = ref[3:] if ref.startswith("ev_") else ref
    row = conn.execute(
        "SELECT event_id, run_id, type FROM events WHERE event_id=?",
        (candidate,)).fetchone()
    if row is None and ref.startswith("tc_"):
        # Tool call id: find this run's tool.completed event for it.
        for (eid, payload) in conn.execute(
                "SELECT event_id, payload FROM events"
                " WHERE run_id=? AND type='tool.completed'",
                (run_id,)).fetchall():
            try:
                if json.loads(payload).get("call_id") == ref:
                    row = conn.execute(
                        "SELECT event_id, run_id, type FROM events"
                        " WHERE event_id=?", (eid,)).fetchone()
                    break
            except (json.JSONDecodeError, TypeError):
                continue
    if row is None:
        reasons.append(f"evidence_ref {ref!r} resolves to no ledger event")
        return None
    event_id, event_run_id, event_type = row
    if event_run_id != run_id:
        reasons.append(
            f"evidence_ref {ref!r} points at run {event_run_id}, not the"
            f" evaluated run {run_id}: cross-run evidence cannot ground")
        return None
    if event_type != "tool.completed":
        reasons.append(
            f"evidence_ref {ref!r} points at a {event_type} event, not"
            " tool.completed: only completed tool executions are"
            " eligible evidence sources")
        return None
    return event_id


def build_evidence(conn, run_id: str,
                   ref: str) -> tuple[Evidence | None, list[str]]:
    """Project one evidence reference to an Evidence, or refuse it.

    Returns (Evidence, []) on success, (None, reasons) on any failure.
    Every failure mode is a loud refusal, never a silent downgrade.
    """
    reasons: list[str] = []
    event_id = _resolve_source_event_id(conn, run_id, ref, reasons)
    if event_id is None:
        return None, reasons
    row = conn.execute(
        "SELECT payload, agent_id, timestamp FROM events WHERE event_id=?",
        (event_id,)).fetchone()
    payload = json.loads(row[0])
    agent_id, observed_at = row[1], row[2]

    if payload.get("ok") is not True:
        reasons.append(
            f"tool.completed {event_id} has ok={payload.get('ok')!r}:"
            " failed executions are not evidence")
        return None, reasons
    call_id = payload.get("call_id")
    claimed_hash = payload.get("result_hash")
    if not call_id or not claimed_hash:
        reasons.append(
            f"tool.completed {event_id} is malformed: missing call_id or"
            " result_hash")
        return None, reasons
    tc = conn.execute(
        "SELECT result_redacted, state, agent_id, tool_name, args_redacted"
        " FROM tool_calls WHERE id=?", (call_id,)).fetchone()
    if tc is None:
        reasons.append(
            f"tool.completed {event_id} references unknown tool call"
            f" {call_id}")
        return None, reasons
    result_redacted, state, tc_agent_id, tool_name, args_redacted = tc
    if not result_redacted:
        reasons.append(
            f"tool call {call_id} has no persisted result: nothing to"
            " verify against")
        return None, reasons
    if state != "COMMITTED":
        reasons.append(
            f"tool call {call_id} is in state {state}, not COMMITTED")
        return None, reasons
    recomputed = hashlib.sha256(result_redacted.encode()).hexdigest()
    if recomputed != claimed_hash:
        reasons.append(
            f"tool call {call_id}: result hash mismatch (payload claims"
            f" {claimed_hash[:12]}..., recomputed {recomputed[:12]}...):"
            " the result was tampered with or misrecorded")
        return None, reasons

    producer = tc_agent_id or agent_id
    kind_row = (conn.execute(
        "SELECT epistemic_kind FROM agents WHERE id=?",
        (producer,)).fetchone() if producer else None)
    kind = kind_row[0] if kind_row else "OBSERVED"
    if Provenance(kind) in NON_EVIDENTIARY:
        reasons.append(
            f"tool call {call_id} was produced by a {kind} agent:"
            " simulated execution is not real-world evidence"
            " (Invariant 12)")
        return None, reasons

    return Evidence(
        evidence_id=f"ev_{event_id}",
        source_event_id=event_id,
        tool_call_id=call_id,
        result_hash=recomputed,
        provenance=kind,
        observed_at=observed_at,
        verification_scope=_scope_for(tool_name, args_redacted),
    ), []


def valid_evidence_for_run(conn, run_id: str) -> list[Evidence]:
    """All valid evidence sources in a run (validity layer only)."""
    out: list[Evidence] = []
    for (event_id,) in conn.execute(
            "SELECT event_id FROM events"
            " WHERE run_id=? AND type='tool.completed' ORDER BY rowid",
            (run_id,)).fetchall():
        ev, _ = build_evidence(conn, run_id, event_id)
        if ev is not None:
            out.append(ev)
    return out
