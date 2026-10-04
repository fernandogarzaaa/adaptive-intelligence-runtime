"""Security policy: capability-based permissions with default deny.

Dangerous capability classes are disabled by default and require explicit
operator approval. This module enforces checks in code, not in prompts.
"""

from __future__ import annotations

import ipaddress
import re
import shlex
from enum import Enum
from pathlib import Path
from urllib.parse import urlparse


class CapabilityClass(str, Enum):
    READ = "READ"
    WRITE = "WRITE"
    NETWORK = "NETWORK"
    EXECUTE = "EXECUTE"
    DESTRUCTIVE = "DESTRUCTIVE"
    PRIVILEGED = "PRIVILEGED"


# Default-deny: these classes are blocked unless the operator explicitly allows.
DENY_BY_DEFAULT = {CapabilityClass.DESTRUCTIVE, CapabilityClass.PRIVILEGED}


class PolicyDenied(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def check_capability(capability: CapabilityClass, allowed: set[CapabilityClass]) -> None:
    if capability in DENY_BY_DEFAULT and capability not in allowed:
        raise PolicyDenied(
            f"capability {capability.value} is deny-by-default and was not explicitly allowed"
        )
    if capability not in allowed:
        raise PolicyDenied(f"capability {capability.value} not in agent's allowed set")


_SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|secret|token|password|passwd|pwd)\s*[:=]\s*['\"]?([^\s'\"]{8,})"),
    re.compile(r"sk-[A-Za-z0-9]{16,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9\-._~+/=]{16,}"),
]


def redact_secrets(text: str) -> str:
    """Redact secret-looking values before persistence or display."""
    out = text
    for pat in _SECRET_PATTERNS:
        out = pat.sub(lambda m: m.group(0)[: m.start(2) - m.start(0)] + "***REDACTED***"
                      if m.lastindex == 2 else "***REDACTED***", out)
    return out


def assert_safe_path(path: str, root: Path) -> Path:
    """Prevent path traversal: resolved path must stay under root."""
    p = (root / path).resolve()
    try:
        p.relative_to(root.resolve())
    except ValueError:
        raise PolicyDenied(f"path traversal blocked: {path!r} escapes {root}")
    return p


def is_host_allowed(host: str, allowlist: set[str] | None) -> bool:
    """SSRF protection: allowlisted hosts only, and never private/loopback IPs
    unless explicitly allowlisted by exact hostname."""
    if allowlist is not None and host not in allowlist:
        return False
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast:
            return False
    except ValueError:
        pass  # hostname; allowlist already checked
    return True


def check_url(url: str, allowlist: set[str] | None) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise PolicyDenied(f"URL scheme not allowed: {parsed.scheme}")
    if not is_host_allowed(parsed.hostname or "", allowlist):
        raise PolicyDenied(f"host not allowed: {parsed.hostname}")


def safe_argv(args: list[str]) -> list[str]:
    """Shell-argument safety: return argv list; callers must use it without shell=True."""
    return [shlex.quote(a) if False else a for a in args]  # no shell interpolation by construction


class ApprovalGate:
    """Operator approval for dangerous operations. Default deny."""

    def __init__(self) -> None:
        self._decisions: dict[str, bool] = {}

    def request(self, key: str) -> str:
        return key  # the caller persists an approvals row; UI/CLI decides

    def decide(self, key: str, approved: bool) -> None:
        self._decisions[key] = approved

    def is_approved(self, key: str) -> bool:
        return self._decisions.get(key, False)
