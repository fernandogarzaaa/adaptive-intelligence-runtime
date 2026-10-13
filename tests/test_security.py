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


@pytest.mark.parametrize(
    "url",
    [
        "http://2130706433/",          # decimal 127.0.0.1
        "http://127.1/",               # short form
        "http://0x7f.0.0.1/",          # hex octet
        "http://0177.0.0.1/",          # octal octet
        "http://0/",                   # 0.0.0.0
        "http://[::ffff:127.0.0.1]/",  # IPv4-mapped IPv6
        "http://[::ffff:a9fe:a9fe]/",  # mapped 169.254.169.254
    ],
)
def test_ssrf_blocks_noncanonical_ip_forms(url):
    with pytest.raises(PolicyDenied):
        check_url(url, allowlist=None)


def test_ssrf_blocks_hostname_resolving_to_private(monkeypatch):
    import socket

    from air.security import policy

    def fake_getaddrinfo(host, *a, **k):
        table = {"evil.example": "10.1.2.3", "ok.example": "93.184.215.14"}
        if host not in table:
            raise socket.gaierror("nope")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (table[host], 0))]

    monkeypatch.setattr(policy.socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(PolicyDenied):
        check_url("https://evil.example/x", allowlist=None)
    check_url("https://ok.example/x", allowlist=None)
    # Unresolvable names are left to fail at connect time.
    check_url("https://does-not-resolve.example/x", allowlist=None)
    # An explicit allowlist is trusted as-is (operator decision).
    check_url("http://evil.example/x", allowlist={"evil.example"})
