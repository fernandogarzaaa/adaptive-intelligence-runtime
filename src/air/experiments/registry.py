"""Append-only, hash-chained experiment registry.

``<exp_dir>/registry.jsonl`` holds every experiment record in write
order: experiment creation, protocol verification, pre-registration,
one record per run, and the final report pointer. Each record carries
``prev_hash`` and ``hash``::

    hash = sha256(canonical({seq, type, payload, prev_hash, written_at}))

The chain makes tampering and reordering detectable: verify_chain
recomputes every link. Records are never mutated in place; the file
is only ever opened in append mode. Pre-registration must precede
the first run record: the run gate checks chain positions.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone

REGISTRY_NAME = "registry.jsonl"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()


def _record_path(exp_dir: str) -> str:
    return os.path.join(exp_dir, REGISTRY_NAME)


def append(exp_dir: str, record_type: str, payload: dict) -> dict:
    """Append one record to the registry chain. Returns the record."""
    path = _record_path(exp_dir)
    prev_hash = "GENESIS"
    seq = 0
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            lines = [ln for ln in f if ln.strip()]
        if lines:
            last = json.loads(lines[-1])
            prev_hash = last["hash"]
            seq = last["seq"] + 1
    record = {
        "seq": seq,
        "type": record_type,
        "payload": payload,
        "prev_hash": prev_hash,
        "written_at": _utcnow(),
    }
    record["hash"] = hashlib.sha256(
        _canonical({k: record[k] for k in
                    ("seq", "type", "payload", "prev_hash", "written_at")})
    ).hexdigest()
    os.makedirs(exp_dir, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")
    return record


def load(exp_dir: str, record_type: str | None = None) -> list[dict]:
    """Load registry records in chain order, optionally filtered."""
    path = _record_path(exp_dir)
    if not os.path.exists(path):
        return []
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if record_type is None or rec["type"] == record_type:
                out.append(rec)
    return out


def verify_chain(exp_dir: str) -> tuple[bool, str]:
    """Recompute the hash chain. Returns (ok, detail)."""
    records = load(exp_dir)
    prev = "GENESIS"
    for rec in records:
        if rec["prev_hash"] != prev:
            return False, (
                f"chain break at seq {rec['seq']}: prev_hash mismatch")
        recomputed = hashlib.sha256(_canonical({
            "seq": rec["seq"], "type": rec["type"],
            "payload": rec["payload"], "prev_hash": rec["prev_hash"],
            "written_at": rec["written_at"]})).hexdigest()
        if recomputed != rec["hash"]:
            return False, (
                f"chain break at seq {rec['seq']}: hash mismatch "
                f"(record tampered)")
        prev = rec["hash"]
    return True, f"chain intact: {len(records)} records"


def first_seq_of(exp_dir: str, record_type: str) -> int | None:
    """Chain position of the first record of a type (None if absent)."""
    for rec in load(exp_dir):
        if rec["type"] == record_type:
            return rec["seq"]
    return None
