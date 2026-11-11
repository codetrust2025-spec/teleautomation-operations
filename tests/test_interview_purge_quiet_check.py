"""The Interview Data purge's quiet check: is anyone changing data right now?

It reads two sources (the nginx access logs and the application's own request log) and says GO only when no data-changing
request other than login / logout / the admin re-check happened in the last 30 minutes. A review found it had treated every
`/auth/...` write as harmless, although password changes and resets write the credentials file; that case is pinned here.
The script's Python is extracted and run with test settings, so these tests run anywhere.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "interview_purge_quiet_check.sh"
NOW = "2026-10-10T21:30:00+00:00"


def extracted_python() -> str:
    text = SCRIPT.read_text(encoding="utf-8")
    return re.search(r"python3 - <<'PY'\n(.*?)\nPY\n", text, re.S).group(1)


@pytest.fixture()
def run(tmp_path):
    program = tmp_path / "quiet_check.py"
    program.write_text(extracted_python(), encoding="utf-8")
    printer = tmp_path / "print_app_log.py"
    nginx = tmp_path / "access.log"
    app = tmp_path / "app.log"

    def execute(nginx_lines=(), app_lines=(), *, no_nginx=False, no_app=False, now=NOW):
        nginx.write_text("\n".join(nginx_lines) + ("\n" if nginx_lines else ""), encoding="utf-8")
        app.write_text("\n".join(app_lines) + ("\n" if app_lines else ""), encoding="utf-8")
        printer.write_text(f"import sys; sys.stdout.write(open(r'{app}', encoding='utf-8').read())", encoding="utf-8")
        env = dict(os.environ, QUIET_NOW=now,
                   QUIET_NGINX_LOGS=str(tmp_path / "missing.log") if no_nginx else str(nginx),
                   QUIET_APP_LOG_CMD=f'"{sys.executable}" "{printer}"' if not no_app else f'"{sys.executable}" -c "import sys; sys.exit(3)"')
        done = subprocess.run([sys.executable, str(program)], capture_output=True, text=True, env=env, timeout=60)
        return done.returncode, done.stdout

    return execute


def web(minute: str, method: str, path: str, status: int = 200, ip: str = "203.0.113.7", day: str = "10/Oct/2026", hour: str = "21") -> str:
    return f'{ip} - - [{day}:{hour}:{minute}:00 +0000] "{method} {path} HTTP/1.1" {status} 123 "-" "Mozilla/5.0"'


def app_log(method: str, path: str, status: int = 200, client: str = "172.18.0.1:40000") -> str:
    return f'INFO:     {client} - "{method} {path} HTTP/1.1" {status} OK'


class TestWhenNobodyIsWorking:
    def test_only_reads_is_go(self, run):
        code, out = run([web("20", "GET", "/candidates/roster"), web("25", "GET", "/health")])
        assert code == 0 and "VERDICT: GO." in out

    def test_login_logout_and_the_admin_recheck_do_not_block(self, run):
        code, out = run([web("20", "POST", "/auth/login"), web("21", "POST", "/auth/logout"), web("22", "POST", "/auth/verify-admin")])
        assert code == 0 and "VERDICT: GO." in out and "login/logout (does not block)" in out

    def test_a_refused_write_does_not_block(self, run):
        code, out = run([web("20", "POST", "/data-room/credentials/vault/prompts", 401), web("21", "PATCH", "/candidates/abcdef0123456789", 403)])
        assert code == 0 and "refused (does not block)" in out

    def test_an_old_write_is_outside_the_window(self, run):
        code, out = run([web("58", "POST", "/bookings/confirm", hour="20"), web("59", "POST", "/handler-expenses", hour="20"), web("25", "GET", "/health")])
        assert code == 0 and "VERDICT: GO." in out

    def test_internal_addresses_in_the_nginx_log_are_ignored(self, run):
        code, _ = run([web("20", "POST", "/bookings/confirm", ip="127.0.0.1"), web("21", "POST", "/bookings/confirm", ip="172.18.0.4"), web("25", "GET", "/health")])
        assert code == 0


class TestWhenSomeoneIsWorking:
    @pytest.mark.parametrize("method, path", [
        ("POST", "/bookings/confirm"), ("POST", "/handler-expenses"), ("PATCH", "/handler-expenses/0123456789abcdef"),
        ("DELETE", "/candidates/0123456789abcdef"), ("POST", "/candidates"), ("PATCH", "/candidates/interviews/slots/0123456789abcdef"),
        ("POST", "/referrers/referrer-x/lifecycle"),
    ])
    def test_a_data_changing_request_blocks(self, run, method, path):
        code, out = run([web("20", method, path)])
        assert code == 1 and "VERDICT: NO GO." in out and f"WRITE {method}" in out

    @pytest.mark.parametrize("path", [
        "/data-room/credentials/vault/prompts", "/data-room/credentials/handlers", "/data-room/credentials/handlers/someone",
        "/data-room/service-accounts/abc123def456/image", "/data-room/offer-letters/abc123def456/upload", "/data-room/offer-letters/upload-analyze",
    ])
    def test_every_data_room_write_route_blocks(self, run, path):
        assert run([web("20", "POST", path)])[0] == 1

    @pytest.mark.parametrize("path", ["/auth/change-password", "/auth/reset-password"])
    def test_password_changes_and_resets_block_because_they_write_the_credentials_file(self, run, path):
        """The review's finding: these are /auth/ writes, and they write credentials.json."""
        code, out = run([web("20", "POST", path)])
        assert code == 1 and f"WRITE POST {path}" in out

    def test_the_ids_are_masked_in_what_it_prints(self, run):
        _, out = run([web("20", "PATCH", "/candidates/0123456789abcdef0123")])
        assert "0123456789abcdef" not in out and "/candidates/<id>" in out

    def test_it_prints_no_addresses_or_agents(self, run):
        _, out = run([web("20", "POST", "/bookings/confirm", ip="203.0.113.99")])
        assert "203.0.113.99" not in out and "Mozilla" not in out


class TestTheApplicationsOwnLog:
    """A request that never went through nginx is still seen, because the application logs what it serves."""

    def test_a_write_only_the_application_saw_blocks(self, run):
        code, out = run([web("20", "GET", "/health")], [app_log("POST", "/bookings/confirm", client="172.18.0.5:41000")])
        assert code == 1 and "WRITE POST /bookings/confirm [application log]" in out

    def test_application_log_reads_and_logins_do_not_block(self, run):
        code, out = run([web("20", "GET", "/health")], [app_log("GET", "/candidates/roster"), app_log("POST", "/auth/login")])
        assert code == 0

    def test_a_refused_write_in_the_application_log_does_not_block(self, run):
        assert run([web("20", "GET", "/health")], [app_log("POST", "/bookings/confirm", 401)])[0] == 0

    def test_if_the_application_log_cannot_be_read_it_says_so_and_still_judges_nginx(self, run):
        code, out = run([web("20", "GET", "/health")], no_app=True)
        assert code == 0 and "the application log could not be read" in out
        code, out = run([web("20", "POST", "/handler-expenses")], no_app=True)
        assert code == 1 and "the application log could not be read" in out


class TestWhenItCannotJudge:
    def test_no_nginx_log_is_no_go(self, run):
        code, out = run(no_nginx=True)
        assert code == 1 and "VERDICT: NO GO." in out

    def test_a_stale_log_is_no_go(self, run):
        code, out = run([web("00", "GET", "/health", hour="20")])
        assert code == 1 and "The logs are not current" in out

    def test_an_empty_log_is_no_go(self, run):
        assert run([])[0] == 1


def test_the_check_is_read_only():
    """No write, delete or service-control call in the script: it only reads logs and asks docker for logs."""
    body = extracted_python()
    for forbidden in ("os.remove", "os.rename", "os.unlink", "shutil", "open(path, \"w\"", "'w'", "docker stop", "docker start", "docker restart", "rm -"):
        assert forbidden not in body, forbidden
    assert "docker logs" in body
