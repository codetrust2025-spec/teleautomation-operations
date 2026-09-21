<#
.SYNOPSIS
    Keeps a laptop's local Ollama reachable from the VPS over a reverse SSH tunnel.

.DESCRIPTION
    Every laptop in the inference pool runs this same script and differs only
    by -VpsPort. Install it with install_ollama_tunnel.ps1, which copies it out
    of the repository first: the scheduled task used to run it from a checkout
    folder, and when that folder was deleted the tunnel silently stopped at the
    next sign-in.

    Ollama itself is never exposed to the network: it stays on the laptop's own
    127.0.0.1:11434, and the tunnel publishes it on the VPS loopback only. The
    -R target is written as 127.0.0.1:<port> explicitly so the VPS listener can
    never land on 0.0.0.0 even if sshd is configured with GatewayPorts.

    Pool ports:
        11435  Jagadeesh
        11436  Praveen
        11437  RTX 4060

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File ollama_tunnel_keepalive.ps1 -VpsPort 11436 -NodeName praveen_kvm1 -VpsHostName <vps-host> -VpsUser <user>
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateRange(1024, 65535)]
    [int]$VpsPort,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[a-z0-9_]+$')]
    [string]$NodeName,

    # Required, never defaulted: the repository holds no environment
    # specifics, and the old built-in address outlived the server it named.
    [Parameter(Mandatory = $true)]
    [string]$VpsHostName,

    # The account the key logs in as. A dedicated tunnel account confined to
    # remote forwarding of one port is the stronger setup; Praveen's laptop
    # still connects as root and should move onto one.
    [Parameter(Mandatory = $true)]
    [string]$VpsUser,

    [int]$LocalOllamaPort = 11434,
    [int]$RetrySeconds = 10
)

$ErrorActionPreference = "Continue"

$SshKey = Join-Path $env:USERPROFILE ".ssh\teleautomation_vps_ed25519"

function Resolve-LogDirectory {
    <#
        Scheduled Task processes do not always inherit LOCALAPPDATA. When it is
        missing, Join-Path returned a *relative* path, New-Item created it
        wherever the task happened to start, and every Write-TunnelLog then
        failed silently — the tunnel ran perfectly while producing no log at
        all, which is precisely the case someone needs the log for.

        USERPROFILE is present in that context (ssh finds its key by it), so
        derive from it and only fall back to TEMP if even that is gone.
    #>
    foreach ($base in @(
        $env:LOCALAPPDATA,
        $(if ($env:USERPROFILE) { Join-Path $env:USERPROFILE "AppData\Local" }),
        $env:TEMP
    )) {
        if ([string]::IsNullOrWhiteSpace($base)) { continue }
        if (-not [System.IO.Path]::IsPathRooted($base)) { continue }
        return (Join-Path $base "TeleAutomation\logs")
    }
    throw "No usable location for the tunnel log: LOCALAPPDATA, USERPROFILE and TEMP are all unset."
}

$LogDir = Resolve-LogDirectory
$LogFile = Join-Path $LogDir "ollama-tunnel-$NodeName.log"

New-Item -ItemType Directory -Path $LogDir -Force | Out-Null

function Write-TunnelLog {
    param([string]$Message)
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    # Pinned, because Add-Content defaults to the system ANSI codepage.
    Add-Content -LiteralPath $LogFile -Value "[$timestamp] $Message" -Encoding utf8
}

# One tunnel per laptop. Two processes racing for the same VPS port means one
# of them dies on every reconnect and the logs stop meaning anything.
#
# Ownership is taken, not inferred from whether the mutex already existed.
# ssh inherits this process's handles, so when a supervisor is stopped its ssh
# child outlives it holding the mutex open: "it exists" then meant "someone is
# running" for as long as that orphan lived, and every restart exited at once
# with no supervisor left. A mutex whose owner has died is abandoned, and that
# is the signal this process is the one to carry on.
$mutexName = "Global\TeleAutomationOllamaTunnel_$NodeName"
$mutex = New-Object System.Threading.Mutex($false, $mutexName)
try {
    # Stop-ScheduledTask returns before the stopped supervisor has exited, so a
    # restart can arrive while it still holds the mutex. Wait for it to go; a
    # supervisor that is genuinely running never lets go, and a duplicate still
    # exits -- only later.
    $owned = $mutex.WaitOne([TimeSpan]::FromSeconds(30))
}
catch [System.Threading.AbandonedMutexException] {
    $owned = $true
}
if (-not $owned) {
    Write-TunnelLog "Another tunnel process for $NodeName is already running; exiting."
    exit 0
}

# Holding the mutex makes this the only supervisor for the node, so any ssh
# still forwarding this node's VPS port was left by one that has gone. Left
# alone it keeps the port on the VPS, and every ssh started here would fail on
# ExitOnForwardFailure until it happened to die.
Get-CimInstance Win32_Process -Filter "Name='ssh.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*-R 127.0.0.1:${VpsPort}:*" } |
    ForEach-Object {
        Write-TunnelLog "Stopping ssh $($_.ProcessId) left by a previous supervisor for port $VpsPort."
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }

if (-not (Test-Path -LiteralPath $SshKey)) {
    Write-TunnelLog "Dedicated SSH key not found at $SshKey. Create it and install the public half on the VPS."
    exit 1
}

Write-TunnelLog "Supervisor started for $NodeName -> VPS 127.0.0.1:$VpsPort"

try {
    while ($true) {
        try {
            $ollamaReady = Invoke-WebRequest `
                -Uri "http://127.0.0.1:$LocalOllamaPort/api/tags" `
                -TimeoutSec 5 `
                -UseBasicParsing
            if ($ollamaReady.StatusCode -ne 200) {
                throw "Local Ollama health check failed."
            }

            Write-TunnelLog "Starting reverse tunnel: VPS 127.0.0.1:$VpsPort -> laptop 127.0.0.1:$LocalOllamaPort"

            # `2>> $LogFile` writes UTF-16LE on Windows PowerShell 5.1, so
            # ssh's own errors landed in a different encoding from every other
            # line in the file and the log became unreadable — exactly when
            # someone is trying to diagnose a dead tunnel. Capture stderr to
            # its own file and fold it back through the normal logger, so the
            # whole log is one encoding.
            $sshArgs = @(
                "-N", "-T",
                "-i", $SshKey,
                "-o", "BatchMode=yes",
                "-o", "IdentitiesOnly=yes",
                "-o", "StrictHostKeyChecking=yes",
                "-o", "ExitOnForwardFailure=yes",
                "-o", "ServerAliveInterval=30",
                "-o", "ServerAliveCountMax=3",
                "-o", "ConnectTimeout=10",
                "-R", "127.0.0.1:${VpsPort}:127.0.0.1:${LocalOllamaPort}",
                "$VpsUser@$VpsHostName"
            )
            $stderrFile = Join-Path $env:TEMP "ollama-tunnel-$NodeName.stderr"
            $process = Start-Process -FilePath "ssh.exe" `
                -ArgumentList $sshArgs `
                -NoNewWindow -Wait -PassThru `
                -RedirectStandardError $stderrFile

            if (Test-Path -LiteralPath $stderrFile) {
                Get-Content -LiteralPath $stderrFile |
                    Where-Object { $_ -and $_.Trim() } |
                    ForEach-Object { Write-TunnelLog "ssh: $_" }
                Remove-Item -LiteralPath $stderrFile -Force -ErrorAction SilentlyContinue
            }

            Write-TunnelLog "SSH exited with code $($process.ExitCode); reconnecting in $RetrySeconds seconds."
        }
        catch {
            Write-TunnelLog "Prerequisite failed ($($_.Exception.Message)); retrying in $RetrySeconds seconds."
        }

        Start-Sleep -Seconds $RetrySeconds
    }
}
finally {
    $mutex.ReleaseMutex()
    $mutex.Dispose()
}
