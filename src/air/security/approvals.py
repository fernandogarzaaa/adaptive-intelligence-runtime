"""Persistent operator approvals. Dangerous operations pause here until a
human (or an authorized policy) approves or denies. Default deny: nothing
is approved unless a row says so."""

from __future__ import annotations

import json
import sqlite3
import uuid

from air.events.fabric import utcnow
from air.security.policy import redact_secrets


class ApprovalRequired(Exception):
    def __init__(self, approval_id: str, reason: str) -> None:
        super().__init__(reason)
        self.approval_id = approval_id
        self.reason = reason


class ApprovalDenied(Exception):
    pass


class ApprovalStore:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def request(self, kind: str, subject: str, payload: dict,
                requested_by: str) -> str:
        ap_id = "appr_" + uuid.uuid4().hex[:12]
        self._conn.execute(
            """INSERT INTO approvals (id, kind, subject, payload,
               requested_by, status, created_at)
               VALUES (?,?,?,?,?,'PENDING',?)""",
            (ap_id, kind, subject,
             redact_secrets(json.dumps(payload or {})),
             requested_by, utcnow()))
        self._conn.commit()
        return ap_id

    def decide(self, approval_id: str, approved: bool,
               decided_by: str) -> str:
        row = self._conn.execute(
            "SELECT status FROM approvals WHERE id=?",
            (approval_id,)).fetchone()
        if not row:
            raise KeyError(f"approval not found: {approval_id}")
        if row[0] != "PENDING":
            raise ApprovalDenied(
                f"approval {approval_id} already {row[0]}")
        status = "APPROVED" if approved else "DENIED"
        self._conn.execute(
            "UPDATE approvals SET status=?, decided_by=?, decided_at=?"
            " WHERE id=?", (status, decided_by, utcnow(), approval_id))
        self._conn.commit()
        return status

    def is_approved(self, approval_id: str) -> bool:
        row = self._conn.execute(
            "SELECT status FROM approvals WHERE id=?",
            (approval_id,)).fetchone()
        return bool(row and row[0] == "APPROVED")

    def get(self, approval_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT id, kind, subject, payload, requested_by, status,"
            " decided_by, decided_at, created_at FROM approvals WHERE id=?",
            (approval_id,)).fetchone()
        if not row:
            return None
        return {"id": row[0], "kind": row[1], "subject": row[2],
                "payload": json.loads(row[3] or "{}"),
                "requested_by": row[4], "status": row[5],
                "decided_by": row[6], "decided_at": row[7],
                "created_at": row[8]}

    def pending(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id, kind, subject, requested_by, created_at"
            " FROM approvals WHERE status='PENDING'"
            " ORDER BY created_at").fetchall()
        return [{"id": r[0], "kind": r[1], "subject": r[2],
                 "requested_by": r[3], "created_at": r[4]} for r in rows]
