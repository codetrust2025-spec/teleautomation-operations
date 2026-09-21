<#
.SYNOPSIS
    Installs the Ollama reverse-SSH tunnel on an inference laptop and keeps it running.

.DESCRIPTION
    Copies ollama_tunnel_keepalive.ps1 out of the repository into
    %LOCALAPPDATA%\TeleAutomation\bin and points a Scheduled Task at that copy.

    The task used to run the script straight from a checkout folder. When that
    folder was deleted in the 19 Sep 2026 cleanup nothing failed loudly: the
    running tunnels carried on until the next sign-in, the task then found no
    script, PowerShell exited, and the node sat Offline on the dashboard with
    the laptop switched on. A copy outside every checkout survives a checkout
    being deleted, moved or re-cloned.

    The task starts at logon and again every few minutes. The script holds a
    per-node mutex, so a start while the supervisor is alive exits at once, and
    a start after it has died brings the tunnel back without anyone signing in
    again. The supervisor itself reconnects ssh on its own loop.

    Re-running with the same -TaskName updates that task in place. No password
    is stored: ssh authenticates with -SshKeyPath, a key that exists for this
    tunnel alone. On the VPS its public half sits in the teleautomation-tunnel
    account's authorized_keys, restricted to this node's port:

        restrict,port-forwarding,permitlisten="127.0.0.1:<VpsPort>",
        permitopen="127.0.0.1:<VpsPort>",command="/usr/sbin/nologin" ssh-ed25519 ...

    and sshd's "Match User teleautomation-tunnel" block allows that account
    remote TCP forwarding only -- no commands, no shell, no local or unix-socket
    forwarding.

.EXAMPLE
    .\install_ollama_tunnel.ps1 -TaskName "TeleAutomation Ollama Secondary Tunnel" `
        -VpsPort 11436 -NodeName praveen_kvm1 -VpsHostName <vps-host> `
        -VpsUser teleautomation-tunnel `
        -SshKeyPath "$env:USERPROFILE\.ssh\teleautomation_tunnel_praveen_ed25519"
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][ValidateRange(1024, 65535)][int]$VpsPort,
    [Parameter(Mandatory = $true)][ValidatePattern('^[a-z0-9_]+$')][string]$NodeName,
    [Parameter(Mandatory = $true)][string]$VpsHostName,
    [Parameter(Mandatory = $true)][string]$VpsUser,
    [Parameter(Mandatory = $true)][string]$SshKeyPath,
    [string]$TaskName = "",
    [ValidateRange(1, 60)][int]$RecheckMinutes = 5,
    [switch]$NoStart
)

$ErrorActionPreference = "Stop"
if (-not $TaskName) { $TaskName = "TeleAutomation Ollama Tunnel ($NodeName)" }

$source = Join-Path $PSScriptRoot "ollama_tunnel_keepalive.ps1"
if (-not (Test-Path -LiteralPath $source)) {
    throw "ollama_tunnel_keepalive.ps1 not found next to this installer."
}

$base = if ($env:LOCALAPPDATA) { $env:LOCALAPPDATA } else { Join-Path $env:USERPROFILE "AppData\Local" }
$installDir = Join-Path $base "TeleAutomation\bin"
New-Item -ItemType Directory -Path $installDir -Force | Out-Null
$installed = Join-Path $installDir "ollama_tunnel_keepalive.ps1"
Copy-Item -LiteralPath $source -Destination $installed -Force

if (-not (Test-Path -LiteralPath $SshKeyPath)) {
    throw "SSH key not found at $SshKeyPath; create this tunnel's own key first."
}
$SshKeyPath = (Resolve-Path -LiteralPath $SshKeyPath).Path

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument ("-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass " +
               "-File `"$installed`" -VpsPort $VpsPort -NodeName $NodeName " +
               "-VpsHostName $VpsHostName -VpsUser $VpsUser -SshKeyPath `"$SshKeyPath`"")

# At this user's logon, and on a clock every few minutes from now on. Scoped
# to this user because a logon trigger for any user needs an elevated shell,
# and the task runs in this user's session anyway (Ollama does too).
#
# The clock is what brings a stopped or crashed supervisor back mid-session. A
# repetition attached to the logon trigger only starts at the next logon, so a
# task installed during a session never re-checked until the user signed in
# again; that was measured, not assumed. An empty duration means indefinitely.
$atLogon = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$recheck = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes $RecheckMinutes)
$recheck.Repetition.Duration = ""

# The supervisor loops forever by design, so no execution limit; one instance
# at a time, and Windows restarts it if the process itself fails.
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew

# Replaced in place with -Force, never unregistered first: if registering the
# new definition fails, the old task must still be there.
Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue

Register-ScheduledTask `
    -Force `
    -TaskName $TaskName `
    -Action $action `
    -Trigger @($atLogon, $recheck) `
    -Settings $settings `
    -RunLevel Limited `
    -Description "Publishes this laptop's local Ollama on VPS 127.0.0.1:$VpsPort over reverse SSH ($NodeName)." | Out-Null

if (-not $NoStart) { Start-ScheduledTask -TaskName $TaskName }

Write-Host "Installed: $installed"
Write-Host "Task: $TaskName (at logon, re-checked every $RecheckMinutes min)"
Write-Host "Log: $(Join-Path $base "TeleAutomation\logs\ollama-tunnel-$NodeName.log")"
