"""DNS-rebinding guard: `air start` on loopback only accepts loopback Host headers."""
from fastapi.testclient import TestClient

from air.api.app import create_app, loopback_allowed_hosts


def test_loopback_bind_gets_loopback_allowlist():
    hosts = loopback_allowed_hosts("127.0.0.1")
    assert hosts is not None
    assert {"localhost", "127.0.0.1", "::1"} <= set(hosts)
    assert "evil.example" not in hosts


def test_extra_hosts_from_env_value():
    hosts = loopback_allowed_hosts("localhost", " air.internal , ")
    assert "air.internal" in hosts and "" not in hosts


def test_non_loopback_bind_has_no_check_unless_configured():
    assert loopback_allowed_hosts("0.0.0.0") is None
    assert loopback_allowed_hosts("0.0.0.0", "air.example") == ["air.example"]


def test_rebinding_host_rejected_and_loopback_accepted():
    app = create_app(allowed_hosts=loopback_allowed_hosts("127.0.0.1"))
    evil = TestClient(app, base_url="http://attacker.example:8765")
    assert evil.get("/openapi.json").status_code == 400
    ok = TestClient(app, base_url="http://127.0.0.1:8765")
    assert ok.get("/openapi.json").status_code == 200
    ok2 = TestClient(app, base_url="http://localhost:8765")
    assert ok2.get("/openapi.json").status_code == 200


def test_default_create_app_has_no_host_check():
    client = TestClient(create_app(), base_url="http://anything.example")
    assert client.get("/openapi.json").status_code == 200
