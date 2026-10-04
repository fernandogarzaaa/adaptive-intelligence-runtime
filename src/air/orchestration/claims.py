"""Fencing-token task claims (adopted from Skein).

A claim on a task key is valid only while its lease is held, and each new
claim for the same key gets a strictly greater fencing token. A worker that
lost its lease (or never had one) presents a stale token and is rejected.
This is what makes task handoff safe when agents die mid-work.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel

from air.events.fabric import utcnow


class ClaimDenied(Exception):
    pass


class TaskClaim(BaseModel):
    task_key: str
    owner: str
    fencing_token: int
    lease_expires_at: str
    status: str = "HELD"


class ClaimManager:
    def __init__(self, conn: sqlite3.Connection,
                 default_lease_s: int = 300) -> None:
        self._conn = conn
        self._lease_s = default_lease_s

    def claim(self, task_key: str, owner: str,
              lease_s: int | None = None) -> TaskClaim:
        """Claim a task key. Raises ClaimDenied if a live lease is held."""
        now = datetime.now(timezone.utc)
        row = self._conn.execute(
            "SELECT owner, fencing_token, lease_expires_at, status"
            " FROM task_claims WHERE task_key=?", (task_key,)).fetchone()
        if row:
            prev_owner, prev_token, expires_at, status = row
            live = (status == "HELD" and
                    datetime.fromisoformat(expires_at) > now)
            if live and prev_owner != owner:
                raise ClaimDenied(
                    f"task {task_key} is claimed by {prev_owner}"
                    f" (token {prev_token}) until {expires_at}")
            token = prev_token + 1
        else:
            token = 1
        expires = now + timedelta(seconds=lease_s or self._lease_s)
        self._conn.execute(
            """INSERT INTO task_claims (task_key, owner, fencing_token,
               lease_expires_at, status, created_at)
               VALUES (?,?,?,?, 'HELD', ?)
               ON CONFLICT(task_key) DO UPDATE SET owner=excluded.owner,
               fencing_token=excluded.fencing_token,
               lease_expires_at=excluded.lease_expires_at,
               status='HELD'""",
            (task_key, owner, token, expires.isoformat(), utcnow()))
        self._conn.commit()
        return TaskClaim(task_key=task_key, owner=owner,
                         fencing_token=token,
                         lease_expires_at=expires.isoformat())

    def check(self, task_key: str, owner: str, fencing_token: int) -> bool:
        """True only if this owner still holds this exact token live."""
        now = datetime.now(timezone.utc)
        row = self._conn.execute(
            "SELECT owner, fencing_token, lease_expires_at, status"
            " FROM task_claims WHERE task_key=?", (task_key,)).fetchone()
        if not row:
            return False
        o, tok, expires_at, status = row
        return (o == owner and tok == fencing_token and status == "HELD"
                and datetime.fromisoformat(expires_at) > now)

    def renew(self, task_key: str, owner: str, fencing_token: int,
              lease_s: int | None = None) -> TaskClaim:
        if not self.check(task_key, owner, fencing_token):
            raise ClaimDenied(
                f"cannot renew: token {fencing_token} for {task_key}"
                " is stale or expired")
        expires = (datetime.now(timezone.utc)
                   + timedelta(seconds=lease_s or self._lease_s))
        self._conn.execute(
            "UPDATE task_claims SET lease_expires_at=? WHERE task_key=?",
            (expires.isoformat(), task_key))
        self._conn.commit()
        return TaskClaim(task_key=task_key, owner=owner,
                         fencing_token=fencing_token,
                         lease_expires_at=expires.isoformat())

    def release(self, task_key: str, owner: str,
                fencing_token: int) -> None:
        if not self.check(task_key, owner, fencing_token):
            raise ClaimDenied("cannot release a claim you do not hold")
        self._conn.execute(
            "UPDATE task_claims SET status='RELEASED' WHERE task_key=?",
            (task_key,))
        self._conn.commit()
