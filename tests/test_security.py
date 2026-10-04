"""Security: default-deny, secret redaction, traversal, SSRF."""

import pytest

from air.security.policy import (
    CapabilityClass,
    PolicyDenied,
    assert_safe_path,
    check_capability,
    check_url,
    redact_secrets,
)
from pathlib import Path


def test_deny_by_default_destructive():
    with pytest.raises(PolicyDenied):
        check_capability(CapabilityClass.DESTRUCTIVE, {CapabilityClass.READ})


def test_explicit_allow_permits():
    check_capability(CapabilityClass.READ, {CapabilityClass.READ, CapabilityClass.WRITE})


def test_redact_secrets():
    text = "key=sk-abcdef1234567890 and password: hunter2secret"
    out = redact_secrets(text)
    assert "sk-abcdef1234567890" not in out
    assert "REDACTED" in out


def test_path_traversal_blocked(tmp_path):
    with pytest.raises(PolicyDenied):
        assert_safe_path("../../etc/passwd", tmp_path)


def test_safe_path_allowed(tmp_path):
    p = assert_safe_path("work/output.txt", tmp_path)
    assert str(p).startswith(str(tmp_path.resolve()))


def test_ssrf_private_ip_blocked():
    with pytest.raises(PolicyDenied):
        check_url("http://169.254.169.254/latest/meta-data", allowlist=None)


def test_ssrf_allowlist_enforced():
    with pytest.raises(PolicyDenied):
        check_url("http://example.com/api", allowlist={"api.internal"})
    check_url("http://api.internal/v1", allowlist={"api.internal"})


def test_non_http_scheme_blocked():
    with pytest.raises(PolicyDenied):
        check_url("file:///etc/passwd", allowlist=None)
