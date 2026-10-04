"""A poisoned Host header must not get an unauthenticated caller past the
dashboard login gate.

Starlette (through 0.38.6, the pinned version) rebuilds ``request.url`` by
concatenating ``{scheme}://{host}{path}`` and re-parsing it, so a Host header
carrying ``?`` or ``#`` -- which the Host grammar forbids but the parser
accepts -- moves the path boundary: ``request.url.path`` reads ``/`` (which the
auth middleware treats as the public SPA shell) while the router still
dispatches the real, protected route (CVE-2026-48710).

``DashboardAuthMiddleware`` now authorises on the ASGI scope path, the exact
path the router matches on, so the check and the dispatch can no longer
disagree. These tests drive the real app and would have returned candidate and
accounting data to an anonymous caller before the fix.
"""

import pytest
from fastapi.testclient import TestClient

from core import dashboard_auth_vps as auth

# Protected API reads that returned 200 under a poisoned Host header before the
# fix -- one per sensitive surface, not an exhaustive list.
PROTECTED_READS = [
    "/candidates",
    "/candidates/roster",
    "/candidates/stats",
    "/handler-salaries",
    "/handler-expenses",
    "/data-room",
    "/ai/ocr-policy",
]

# Host values that are forbidden by the Host grammar but shift the path during
# URL re-parsing. A bare valid host is the control.
POISONED_HOSTS = ["ops.example?", "ops.example#", "ops.example?x=/", "ops.example/a?b"]


@pytest.fixture
def client(monkeypatch):
    import main

    # The real helper re-reads .env on every call, which would overwrite the
    # fixture credentials with whatever is on the developer's machine.
    monkeypatch.setattr(auth, "_refresh_dashboard_env_from_file", lambda: None)
    monkeypatch.setenv("DASHBOARD_PASSWORD", "host-bypass-fixture")
    monkeypatch.setenv("DASHBOARD_AUTH_SECRET", "host-bypass-secret")
    assert auth.auth_enabled(), "auth must be on or this proves nothing"
    return TestClient(main.app, raise_server_exceptions=False)


@pytest.mark.parametrize("path", PROTECTED_READS)
def test_a_valid_host_still_demands_a_session(client, path):
    assert client.get(path, headers={"host": "ops.example"}).status_code == 401


@pytest.mark.parametrize("path", PROTECTED_READS)
@pytest.mark.parametrize("host", POISONED_HOSTS)
def test_a_poisoned_host_header_does_not_bypass_auth(client, path, host):
    assert client.get(path, headers={"host": host}).status_code == 401, (
        f"{path} was reachable unauthenticated with Host: {host!r}"
    )


def test_the_middleware_reads_the_scope_path_not_the_reconstructed_url():
    """Nail the mechanism: authorisation keys off the scope path, so a lie in
    request.url cannot reach it."""
    import inspect

    from core import dashboard_auth_api

    source = inspect.getsource(dashboard_auth_api.install_dashboard_auth)
    assert 'request.scope.get("path")' in source
    assert "path = request.url.path" not in source
