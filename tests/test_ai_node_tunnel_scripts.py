"""The reverse tunnel that puts an inference laptop's Ollama on the VPS.

On 21 Sep 2026 Praveen's node showed Offline with the laptop switched on. Its
scheduled task ran the tunnel script from a checkout folder that the 19 Sep
cleanup had deleted: the running tunnel carried on until the next sign-in, the
task then found no script, and nothing came back. Putting it right turned up
three more ways the tunnel could stay down; each is held here.

These read the scripts rather than run them: CI has no Windows Task Scheduler.
The behaviour itself was proven on the laptop -- a stopped worker came back on
the next re-check with nobody touching it, and production saw it return.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "scripts" / "ai-node"
KEEPALIVE = ROOT / "ollama_tunnel_keepalive.ps1"
INSTALLER = ROOT / "install_ollama_tunnel.ps1"


def text(path: Path) -> str:
    assert path.exists(), f"{path.name} is missing"
    return path.read_text(encoding="utf-8")


def test_neither_script_names_a_server():
    """The old script defaulted to an address that outlived its server."""
    for path in (KEEPALIVE, INSTALLER):
        body = text(path).replace("127.0.0.1", "").replace("0.0.0.0", "")
        assert not re.search(r"\b\d{1,3}(\.\d{1,3}){3}\b", body), f"{path.name} names an address"
    params = text(KEEPALIVE).split("param(", 1)[1].split(")\n", 1)[0]
    for name in ("VpsHostName", "VpsUser"):
        assert re.search(r"Mandatory = \$true\)\]\s*\[string\]\$" + name, params), f"-{name} must be required"


def test_the_task_runs_a_copy_outside_every_checkout():
    """Deleting or re-cloning a repository must not take the tunnel with it."""
    body = text(INSTALLER)
    assert 'TeleAutomation\\bin' in body
    assert "Copy-Item -LiteralPath $source -Destination $installed" in body
    action = body.split("New-ScheduledTaskAction", 1)[1].split("\n\n", 1)[0]
    assert '-File `"$installed`"' in action and "$PSScriptRoot" not in action


def test_a_stopped_worker_is_restarted_mid_session():
    """A repetition on the logon trigger only starts at the next logon, so a
    task installed mid-session never re-checked; a clock trigger does."""
    body = text(INSTALLER)
    assert "New-ScheduledTaskTrigger -Once" in body and "-RepetitionInterval" in body
    assert "$recheck.Repetition.Duration = \"\"" in body, "the re-check must repeat indefinitely"
    assert "-Trigger @($atLogon, $recheck)" in body
    # A logon trigger for any user needs an elevated shell; this user's does not.
    assert '-AtLogOn -User "$env:USERDOMAIN\\$env:USERNAME"' in body
    assert "-MultipleInstances IgnoreNew" in body
    assert "-ExecutionTimeLimit ([TimeSpan]::Zero)" in body


def test_reinstalling_can_never_lose_the_task():
    """Deleting before registering left no task at all when registering failed."""
    body = text(INSTALLER)
    assert "Unregister-ScheduledTask" not in body
    assert "-Force `\n    -TaskName $TaskName" in body


def test_a_restart_takes_over_from_the_supervisor_it_replaces():
    """ssh inherits the mutex handle, so a stopped supervisor's ssh kept it
    alive: "the mutex exists" read as "someone is running" and every restart
    exited, leaving the node unsupervised."""
    body = text(KEEPALIVE)
    assert "[ref]$createdNew" not in body
    assert "$mutex.WaitOne(" in body
    assert "catch [System.Threading.AbandonedMutexException]" in body
    handover = body.split("AbandonedMutexException", 1)[1].split("Write-TunnelLog \"Supervisor started", 1)[0]
    assert '-like "*-R 127.0.0.1:${VpsPort}:*"' in handover and "Stop-Process" in handover


def test_the_tunnel_stays_on_the_vps_loopback_and_fails_loudly():
    body = text(KEEPALIVE)
    assert '"-R", "127.0.0.1:${VpsPort}:127.0.0.1:${LocalOllamaPort}"' in body
    for option in ("BatchMode=yes", "StrictHostKeyChecking=yes", "ExitOnForwardFailure=yes",
                   "ServerAliveInterval=30"):
        assert option in body
    assert "/api/tags" in body, "the tunnel only opens once local Ollama answers"


def test_a_tunnel_authenticates_with_its_own_key_never_the_admin_key():
    """Praveen's tunnel ran on the full root key until 21 Sep 2026. The key is
    now a required argument, and neither script names the administrator's key
    to fall back on."""
    for path in (KEEPALIVE, INSTALLER):
        body = text(path)
        assert "teleautomation_vps_ed25519" not in body, f"{path.name} still names the admin key"
        params = body.split("param(", 1)[1].split("\n)\n", 1)[0]
        assert re.search(r"Mandatory = \$true\)\]\s*\[string\]\$SshKeyPath", params), f"{path.name}: -SshKeyPath must be required"
    assert '"-i", $SshKey,' in text(KEEPALIVE) and "$SshKey = $SshKeyPath" in text(KEEPALIVE)
    action = text(INSTALLER).split("New-ScheduledTaskAction", 1)[1].split("\n\n", 1)[0]
    assert '-SshKeyPath `"$SshKeyPath`"' in action
    # The installer records what the VPS side has to say about the key.
    assert 'restrict,port-forwarding,permitlisten="127.0.0.1:<VpsPort>"' in text(INSTALLER)
    assert 'command="/usr/sbin/nologin"' in text(INSTALLER)
