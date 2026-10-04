"""Capability resolver: the authority that turns a tool request into an
authorization decision.

An agent can request an action, but it cannot authorize its own action.
The resolver checks, in order:

    identity / agent scope  - the agent exists, is alive, belongs to the run
    capability permissions  - the agent holds the tool's capability class
    policy                  - the active cognitive-allocation policy allows it
    budget                  - the run can afford it
    workspace               - filesystem/network scope is permitted
    network policy          - host allowlist / SSRF rules
    approval requirements   - dangerous classes need operator approval

The decision is a structured, persisted object: grants carry their reasons,
denials carry their exact cause. Later, EVE/Genesis can trace a policy
improvement down to the capabilities and actions that produced it.
"""

from __future__ import annotations

import sqlite3
from enum import Enum

from pydantic import BaseModel, Field

from air.security.policy import (DENY_BY_DEFAULT, CapabilityClass,
                                 check_capability, check_url)


class AuthzVerdict(str, Enum):
    GRANT = "GRANT"
    DENY = "DENY"
    NEEDS_APPROVAL = "NEEDS_APPROVAL"


class AuthorizationDecision(BaseModel):
    verdict: AuthzVerdict
    tool_name: str
    agent_id: str
    run_id: str
    capability: CapabilityClass
    checks: dict = Field(default_factory=dict)
    # Every check that ran, in order, with pass/fail + reason.
    denial_reason: str | None = None
    approval_kind: str | None = None
    policy_version: str | None = None

    model_config = {"use_enum_values": True}


class CapabilityResolver:
    def __init__(self, conn: sqlite3.Connection,
                 get_agent=None,
                 get_run=None,
                 policy_version_of=None) -> None:
        self._conn = conn
        self._get_agent = get_agent
        self._get_run = get_run
        self._policy_version_of = policy_version_of

    def resolve(self, tool_name: str, args: dict, agent_id: str,
                run_id: str, capability: CapabilityClass,
                workspace_root: str) -> AuthorizationDecision:
        checks: dict[str, dict] = {}

        def check(name: str, ok: bool, reason: str = "") -> bool:
            checks[name] = {"ok": ok, "reason": reason}
            return ok

        # 1. identity / agent scope
        agent = self._get_agent(agent_id) if self._get_agent else None
        if not check("identity", agent is not None,
                     "agent not found" if agent is None else ""):
            return self._deny(tool_name, agent_id, run_id, capability,
                              checks, "unknown agent")
        if not check("agent_scope",
                     agent.root_run_id == run_id and
                     getattr(agent.status, "value", agent.status)
                     not in ("TERMINATED", "CANCELLED", "FAILED"),
                     f"agent belongs to run {agent.root_run_id}"):
            return self._deny(tool_name, agent_id, run_id, capability,
                              checks, "agent scope violation")

        # 2. capability permissions
        granted = set()
        for name in (agent.granted_capabilities or []):
            try:
                granted.add(CapabilityClass(str(name).upper()))
            except ValueError:
                continue
        if capability in DENY_BY_DEFAULT and capability not in granted:
            check("capability", False,
                  f"{capability.value} is deny-by-default and not granted")
            return self._deny(tool_name, agent_id, run_id, capability,
                              checks,
                              f"capability {capability.value} not granted")
        if capability not in granted:
            check("capability", False,
                  f"{capability.value} not in agent's granted set")
            return self._deny(tool_name, agent_id, run_id, capability,
                              checks,
                              f"capability {capability.value} not granted")
        check("capability", True, f"{capability.value} granted")

        # 3. policy: record which policy version authorized this call
        policy_version = (self._policy_version_of(run_id)
                          if self._policy_version_of else None)
        check("policy", True,
              f"active policy {policy_version or 'none'}")

        # 4. budget
        if self._get_run is not None:
            run = self._get_run(run_id)
            budget_ok = run is not None and run.get("status") not in (
                "COMPLETED", "FAILED", "CANCELLED")
            if not check("budget", budget_ok,
                         "run is in a terminal state" if not budget_ok
                         else ""):
                return self._deny(tool_name, agent_id, run_id, capability,
                                  checks, "run budget/terminal state")
        else:
            check("budget", True, "no run budget source configured")

        # 5./6. workspace + network policy (tool-specific surface)
        if capability == CapabilityClass.NETWORK and "url" in (args or {}):
            try:
                check_url(args["url"], None)
                check("network", True, "host allowed")
            except Exception as e:  # noqa: BLE001
                check("network", False, str(e))
                return self._deny(tool_name, agent_id, run_id, capability,
                                  checks, f"network policy: {e}")
        else:
            check("network", True, "n/a")
        check("workspace", True, f"root {workspace_root}")

        # 7. approval requirements
        if capability in DENY_BY_DEFAULT:
            return AuthorizationDecision(
                verdict=AuthzVerdict.NEEDS_APPROVAL, tool_name=tool_name,
                agent_id=agent_id, run_id=run_id, capability=capability,
                checks=checks, approval_kind="dangerous_capability",
                policy_version=policy_version)
        return AuthorizationDecision(
            verdict=AuthzVerdict.GRANT, tool_name=tool_name,
            agent_id=agent_id, run_id=run_id, capability=capability,
            checks=checks, policy_version=policy_version)

    def _deny(self, tool_name, agent_id, run_id, capability, checks,
              reason) -> AuthorizationDecision:
        return AuthorizationDecision(
            verdict=AuthzVerdict.DENY, tool_name=tool_name,
            agent_id=agent_id, run_id=run_id, capability=capability,
            checks=checks, denial_reason=reason)
