# One-time setup of the TeleAutomation off-server backup copy and watchdog on the
# RTX 4060 laptop. Run ONCE, in Windows PowerShell opened "as Administrator":
#
#   powershell -ExecutionPolicy Bypass -File install_rtx_backup.ps1
#   powershell -ExecutionPolicy Bypass -File install_rtx_backup.ps1 -BackupRoot E:\TeleAutomation\Backups
#
# What it does (and nothing else; Ollama and its tunnel are not touched):
#  1. checks the backup drive exists and has >= 20 GB free
#  2. creates <BackupRoot> and C:\ProgramData\TeleAutomation (scripts, key, logs),
#     readable only by SYSTEM and Administrators
#  3. installs restic with winget (only needed to RESTORE from this copy by hand
#     in a disaster; the nightly copy does not need it)
#  4. creates a dedicated SSH key for this job (not your admin key) and pins the
#     production server's host key
#  5. registers two scheduled tasks running as SYSTEM at below-normal priority:
#       "TeleAutomation Backup Mirror"  every 2 hours (+ at start-up), wakes the PC at 03:15
#       "TeleAutomation Watchdog"       every 15 minutes
#  6. prints the PUBLIC key. Send that line to whoever runs the server side; it is
#     not a secret. The tasks start working once the server has pinned it.
#
# No inbound port is opened and no SSH server is installed: the laptop only makes
# outbound SSH connections, with a key that can run a fixed list of commands.
[CmdletBinding()]
param(
    [string]$BackupRoot = 'D:\TeleAutomation\Backups',
    [string]$ServerHost = '187.127.164.90',
    [string]$ServerHostKey = 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIG04nEckFpWjWBV0bj1I0pNxLCSgAIf2Xe2x+2NsdEG6',
    [string]$PublicHealthUrl = 'https://operations.teleautomation.online/health',
    [int]$MinFreeGB = 20
)
$ErrorActionPreference = 'Stop'
$Base = 'C:\ProgramData\TeleAutomation'
$Bin = Join-Path $Base 'bin'
$Keys = Join-Path $Base 'keys'
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path

function Step([string]$m) { Write-Host "==> $m" -ForegroundColor Cyan }

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { throw 'Run this in PowerShell opened with "Run as administrator".' }
if (-not (Get-Command ssh.exe -ErrorAction SilentlyContinue)) {
    throw 'The Windows OpenSSH client (ssh.exe) is missing. Settings > Apps > Optional features > add "OpenSSH Client", then run again.'
}

Step "1. Backup drive"
$qualifier = Split-Path -Qualifier $BackupRoot
if (-not (Test-Path "$qualifier\")) { throw "Drive $qualifier does not exist. Run again with -BackupRoot <drive>:\TeleAutomation\Backups on a drive that does." }
$free = [math]::Round((Get-PSDrive $qualifier.TrimEnd(':')).Free / 1GB, 1)
if ($free -lt $MinFreeGB) { throw "Drive $qualifier has $free GB free; at least $MinFreeGB GB is needed. Choose another drive with -BackupRoot." }
Write-Host "    $qualifier has $free GB free"

Step "2. Folders (SYSTEM and Administrators only)"
foreach ($d in @($BackupRoot, $Base, $Bin, $Keys, (Join-Path $Base 'logs'))) { New-Item -ItemType Directory -Force -Path $d | Out-Null }
foreach ($d in @($BackupRoot, $Base)) {
    icacls $d /inheritance:r /grant:r 'SYSTEM:(OI)(CI)F' 'Administrators:(OI)(CI)F' | Out-Null
}
Copy-Item -Force (Join-Path $Here 'rtx_backup_mirror.ps1'), (Join-Path $Here 'rtx_watchdog.ps1') $Bin

Step "3. restic (for restoring by hand in a disaster)"
if (Get-Command restic.exe -ErrorAction SilentlyContinue) {
    Write-Host "    already installed: $((restic.exe version) -join ' ')"
} elseif (Get-Command winget.exe -ErrorAction SilentlyContinue) {
    winget install --id restic.restic --exact --silent --accept-package-agreements --accept-source-agreements | Out-Null
    Write-Host '    installed with winget'
} else {
    Write-Warning 'winget is not available; restic was not installed. The nightly copy works without it.'
}

Step "4. Dedicated SSH key and pinned server key"
$key = Join-Path $Keys 'rtx_backup_ed25519'
if (-not (Test-Path $key)) {
    & ssh-keygen.exe -q -t ed25519 -N '""' -C "teleautomation-rtx-backup@$env:COMPUTERNAME" -f $key
    if ($LASTEXITCODE -ne 0) { throw 'ssh-keygen failed' }
}
icacls $key /inheritance:r /grant:r 'SYSTEM:F' 'Administrators:F' | Out-Null
$knownHosts = Join-Path $Keys 'known_hosts'
"$ServerHost $ServerHostKey" | Set-Content -Path $knownHosts -Encoding ASCII
@{
    BackupRoot = $BackupRoot; Host = $ServerHost; KeyPath = $key; KnownHostsPath = $knownHosts
    PublicHealthUrl = $PublicHealthUrl
} | ConvertTo-Json | Set-Content -Path (Join-Path $Base 'rtx-backup.json') -Encoding UTF8
if (-not [System.Diagnostics.EventLog]::SourceExists('TeleAutomation')) { New-EventLog -LogName Application -Source 'TeleAutomation' }

Step "5. Scheduled tasks"
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 2) -Priority 7 -WakeToRun
$ps = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$mirrorAction = New-ScheduledTaskAction -Execute $ps -Argument "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$Bin\rtx_backup_mirror.ps1`""
$mirrorTriggers = @(
    (New-ScheduledTaskTrigger -Daily -At '03:15'),
    (New-ScheduledTaskTrigger -Once -At (Get-Date).Date.AddMinutes(5) -RepetitionInterval (New-TimeSpan -Hours 2) -RepetitionDuration (New-TimeSpan -Days 3650)),
    (New-ScheduledTaskTrigger -AtStartup)
)
Register-ScheduledTask -TaskName 'TeleAutomation Backup Mirror' -Action $mirrorAction -Trigger $mirrorTriggers `
    -Principal $principal -Settings $settings -Force | Out-Null
$watchSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -Priority 7
$watchAction = New-ScheduledTaskAction -Execute $ps -Argument "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$Bin\rtx_watchdog.ps1`""
$watchTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).Date.AddMinutes(2) -RepetitionInterval (New-TimeSpan -Minutes 15) -RepetitionDuration (New-TimeSpan -Days 3650)
Register-ScheduledTask -TaskName 'TeleAutomation Watchdog' -Action $watchAction -Trigger $watchTrigger `
    -Principal $principal -Settings $watchSettings -Force | Out-Null
Write-Host '    registered: TeleAutomation Backup Mirror, TeleAutomation Watchdog'

Step "6. Send this PUBLIC key line to the server administrator (not a secret):"
Write-Host ''
Write-Host (Get-Content "$key.pub") -ForegroundColor Yellow
Write-Host ''
Write-Host "Backups will be kept in: $BackupRoot\restic-mirror"
Write-Host "Logs: $Base\logs (mirror.log, watchdog.log)"
Write-Host 'Keep this laptop powered on and connected overnight (copy runs at 03:15 and every 2 hours).'
