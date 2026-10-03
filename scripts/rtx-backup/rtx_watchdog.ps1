# TeleAutomation watchdog (RTX 4060 laptop). Windows PowerShell 5.1.
#
# Run by the scheduled task "TeleAutomation Watchdog" every 15 minutes, as SYSTEM.
# Independent of the production server: it checks the public site and the
# server's monitor from this office network, and its own backup copy and disk.
#
# Alerts are delivered as GitHub issues (email to the owner) by the server's
# monitor and by the GitHub watchdog; this script feeds them the laptop's view
# through `laptop-report`. When the server itself is unreachable it cannot be
# told, so the problem is written to this machine's Application event log
# (source "TeleAutomation") and log file, and the GitHub watchdog raises the
# outage independently.
$ErrorActionPreference = 'Stop'
# TA_RTX_BASE exists only so the script can be exercised outside the installed location.
$Base = if ($env:TA_RTX_BASE) { $env:TA_RTX_BASE } else { 'C:\ProgramData\TeleAutomation' }
$cfg = Get-Content (Join-Path $Base 'rtx-backup.json') -Raw | ConvertFrom-Json
$StateFile = Join-Path $Base 'rtx-state.json'
$Log = Join-Path $Base 'logs\watchdog.log'
New-Item -ItemType Directory -Force -Path (Split-Path $Log) | Out-Null
function Write-Log([string]$msg) { "$(Get-Date -Format s) $msg" | Add-Content -Path $Log -Encoding UTF8 }
function Set-StateValue($state, [string]$name, $value) {
    if ($state.PSObject.Properties[$name]) { $state.$name = $value } else { $state | Add-Member -NotePropertyName $name -NotePropertyValue $value }
}
$SshArgs = @('-i', $cfg.KeyPath, '-o', 'BatchMode=yes', '-o', "UserKnownHostsFile=$($cfg.KnownHostsPath)",
             '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=30', "root@$($cfg.Host)")
$state = if (Test-Path $StateFile) { Get-Content $StateFile -Raw | ConvertFrom-Json } else { [pscustomobject]@{} }
$problems = @()
$now = (Get-Date).ToUniversalTime()

# 1. public site, from outside the server
try {
    $r = Invoke-WebRequest -Uri $cfg.PublicHealthUrl -UseBasicParsing -TimeoutSec 20
    $publicOk = ($r.StatusCode -eq 200)
} catch { $publicOk = $false }
if (-not $publicOk) { $problems += 'Production site is not answering /health from the office network' }

# 2. the server's monitor
$statusJson = & ssh @SshArgs 'status' 2>$null
$serverReachable = ($LASTEXITCODE -eq 0)
if (-not $serverReachable) {
    $problems += 'Production server unreachable over SSH from the office network'
} else {
    try {
        $status = ($statusJson -join '') | ConvertFrom-Json
        $age = ($now - ([datetime]$status.generated_at).ToUniversalTime()).TotalMinutes
        if ($age -gt 20) { $problems += "Production monitor last reported $([int]$age) min ago" }
        foreach ($a in @($status.active_alerts)) { if ($a.key) { $problems += "Server alert: $($a.summary)" } }
    } catch { $problems += 'Production monitor status unreadable' }
}

# 3. this machine's copy and disk
if ($state.PSObject.Properties['mirror_last_success_at'] -and $state.mirror_last_success_at) {
    $mirrorAge = ($now - ([datetime]$state.mirror_last_success_at).ToUniversalTime()).TotalHours
    if ($mirrorAge -gt 36) { $problems += "Off-server backup copy is $([int]$mirrorAge) h old" }
} else { $problems += 'Off-server backup copy has never completed' }
$drive = (Get-Item $cfg.BackupRoot).PSDrive
$free = [math]::Round((Get-PSDrive $drive.Name).Free / 1GB, 1)
if ($free -lt 10) { $problems += "Backup drive has only $free GB free" }

Set-StateValue $state 'watchdog_at' ($now.ToString('s') + 'Z')
Set-StateValue $state 'watchdog_public_ok' $publicOk
Set-StateValue $state 'watchdog_server_reachable' $serverReachable
Set-StateValue $state 'disk_free_gb' $free
Set-StateValue $state 'computer' $env:COMPUTERNAME
$state | ConvertTo-Json -Depth 5 | Set-Content -Path $StateFile -Encoding UTF8

if ($serverReachable) {
    ($state | ConvertTo-Json -Depth 5 -Compress) | & ssh @SshArgs 'laptop-report' | Out-Null
}
if ($problems.Count -gt 0) {
    $text = 'TeleAutomation watchdog: ' + ($problems -join '; ')
    Write-Log "PROBLEM: $text"
    # One event per distinct problem set, not one every 15 minutes.
    $digest = [BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash([Text.Encoding]::UTF8.GetBytes($text)))
    if (-not ($state.PSObject.Properties['last_event_digest'] -and $state.last_event_digest -eq $digest)) {
        try { Write-EventLog -LogName Application -Source 'TeleAutomation' -EventId 4001 -EntryType Warning -Message $text } catch {}
        Set-StateValue $state 'last_event_digest' $digest
        $state | ConvertTo-Json -Depth 5 | Set-Content -Path $StateFile -Encoding UTF8
    }
} else {
    Write-Log 'ok'
    if ($state.PSObject.Properties['last_event_digest'] -and $state.last_event_digest) {
        try { Write-EventLog -LogName Application -Source 'TeleAutomation' -EventId 4000 -EntryType Information -Message 'TeleAutomation watchdog: all checks passing again' } catch {}
        Set-StateValue $state 'last_event_digest' ''
        $state | ConvertTo-Json -Depth 5 | Set-Content -Path $StateFile -Encoding UTF8
    }
}
