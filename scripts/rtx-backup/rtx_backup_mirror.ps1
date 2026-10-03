# TeleAutomation off-server backup mirror (RTX 4060 laptop). Windows PowerShell 5.1.
#
# Run by the scheduled task "TeleAutomation Backup Mirror" (every 2 hours, as
# SYSTEM, below-normal priority). Copies the production restic repository to
# <BackupRoot>\restic-mirror over an outbound SSH connection whose key can only
# run ta_offhost.py: no shell, no forwarding, nothing else.
#
# * Only new or changed files are fetched; a run with nothing new takes seconds.
# * Every restic file except `config` is named by the SHA-256 of its content, so
#   each file is verified as it arrives, and the whole copy is re-verified after
#   every run. The laptop never holds the repository password and cannot read
#   the backup; it can prove the copy is intact.
# * Deletions (the server pruning old snapshots) are decided by the server and
#   held back for 8 days, capped per run, and refused if the server's own
#   repository looks broken, so a damaged server cannot empty this copy.
# * Does not touch Ollama, the GPU, or anything outside <BackupRoot>.
$ErrorActionPreference = 'Stop'
# TA_RTX_BASE exists only so the script can be exercised outside the installed location.
$Base = if ($env:TA_RTX_BASE) { $env:TA_RTX_BASE } else { 'C:\ProgramData\TeleAutomation' }
$cfg = Get-Content (Join-Path $Base 'rtx-backup.json') -Raw | ConvertFrom-Json
$Repo = Join-Path $cfg.BackupRoot 'restic-mirror'
$StateFile = Join-Path $Base 'rtx-state.json'
$Log = Join-Path $Base 'logs\mirror.log'
New-Item -ItemType Directory -Force -Path $Repo, (Split-Path $Log) | Out-Null

function Write-Log([string]$msg) { "$(Get-Date -Format s) $msg" | Add-Content -Path $Log -Encoding UTF8 }
function Get-State { if (Test-Path $StateFile) { Get-Content $StateFile -Raw | ConvertFrom-Json } else { [pscustomobject]@{} } }
function Set-StateValue($state, [string]$name, $value) {
    if ($state.PSObject.Properties[$name]) { $state.$name = $value } else { $state | Add-Member -NotePropertyName $name -NotePropertyValue $value }
}
function Save-State($state) { $state | ConvertTo-Json -Depth 5 | Set-Content -Path $StateFile -Encoding UTF8 }
$SshArgs = @('-i', $cfg.KeyPath, '-o', 'BatchMode=yes', '-o', "UserKnownHostsFile=$($cfg.KnownHostsPath)",
             '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=30', '-o', 'ServerAliveInterval=30',
             "root@$($cfg.Host)")

function Test-NamedHash([string]$path, [string]$rel) {
    if ($rel -eq 'config') { return $true }
    $expected = Split-Path $rel -Leaf
    return ((Get-FileHash -Algorithm SHA256 -Path $path).Hash.ToLower() -eq $expected)
}

$state = Get-State
Set-StateValue $state 'mirror_last_attempt_at' ((Get-Date).ToUniversalTime().ToString('s') + 'Z')
try {
    # 1. what is here
    $inventory = Get-ChildItem -Path $Repo -Recurse -File | Where-Object { $_.Extension -ne '.partial' } | ForEach-Object {
        $rel = $_.FullName.Substring($Repo.Length + 1).Replace('\', '/')
        "$rel`t$($_.Length)"
    }
    # 2. what the server says to fetch and delete
    $planJson = ($inventory -join "`n") | & ssh @SshArgs 'repo-plan'
    if ($LASTEXITCODE -ne 0) { throw "repo-plan failed (ssh exit $LASTEXITCODE)" }
    $plan = ($planJson -join '') | ConvertFrom-Json
    Write-Log "plan: fetch $($plan.fetch.Count) file(s), $([math]::Round($plan.fetch_bytes / 1MB, 1)) MB; delete $($plan.delete.Count); held $($plan.held_deletions)"

    # 3. fetch, verifying each file against its name before it takes its place
    foreach ($rel in $plan.fetch) {
        $target = Join-Path $Repo ($rel.Replace('/', '\'))
        $tmp = "$target.partial"
        New-Item -ItemType Directory -Force -Path (Split-Path $target) | Out-Null
        # cmd's redirection is byte-exact; PowerShell 5.1's would re-encode the stream.
        $cmdLine = 'ssh ' + (($SshArgs | ForEach-Object { '"' + $_ + '"' }) -join ' ') + " repo-fetch $rel > `"$tmp`""
        cmd.exe /c $cmdLine
        if ($LASTEXITCODE -ne 0) { throw "fetch failed for $rel (exit $LASTEXITCODE)" }
        if (-not (Test-NamedHash $tmp $rel)) { Remove-Item $tmp -Force; throw "checksum mismatch on arrival: $rel" }
        Move-Item -Force -Path $tmp -Destination $target
    }
    # 4. deletions the server has released (held 8 days, capped)
    foreach ($rel in $plan.delete) {
        $target = Join-Path $Repo ($rel.Replace('/', '\'))
        if (Test-Path $target) { Remove-Item -Force $target }
    }
    Get-ChildItem -Path $Repo -Recurse -Filter '*.partial' | Remove-Item -Force

    # 5. verify the whole copy
    $bad = @(); $count = 0; $bytes = 0
    Get-ChildItem -Path $Repo -Recurse -File | ForEach-Object {
        $rel = $_.FullName.Substring($Repo.Length + 1).Replace('\', '/')
        $count++; $bytes += $_.Length
        if (-not (Test-NamedHash $_.FullName $rel)) { $bad += $rel }
    }
    if ($bad.Count -gt 0) {
        # A damaged file is removed so the next run fetches it again.
        foreach ($rel in $bad) { Remove-Item -Force (Join-Path $Repo ($rel.Replace('/', '\'))) }
        Set-StateValue $state 'mirror_verify_ok' $false
        Set-StateValue $state 'mirror_verify_problem' "$($bad.Count) file(s) failed their checksum and will be fetched again"
        throw "verification failed for $($bad.Count) file(s)"
    }
    $snapshots = @(Get-ChildItem -Path (Join-Path $Repo 'snapshots') -File -ErrorAction SilentlyContinue).Count
    Set-StateValue $state 'mirror_verify_ok' $true
    Set-StateValue $state 'mirror_verify_problem' ''
    Set-StateValue $state 'mirror_last_success_at' ((Get-Date).ToUniversalTime().ToString('s') + 'Z')
    Set-StateValue $state 'mirror_files' $count
    Set-StateValue $state 'mirror_bytes' $bytes
    Set-StateValue $state 'mirror_snapshots' $snapshots
    Set-StateValue $state 'mirror_held_deletions' $plan.held_deletions
    Set-StateValue $state 'mirror_last_error' ''
    Write-Log "ok: $count files, $([math]::Round($bytes / 1MB, 1)) MB, $snapshots snapshot(s), all verified"
} catch {
    Set-StateValue $state 'mirror_last_error' $_.Exception.Message
    Write-Log "FAILED: $($_.Exception.Message)"
} finally {
    $drive = (Get-Item $cfg.BackupRoot).PSDrive
    Set-StateValue $state 'disk_free_gb' ([math]::Round((Get-PSDrive $drive.Name).Free / 1GB, 1))
    Set-StateValue $state 'computer' $env:COMPUTERNAME
    Save-State $state
    # Tell the server; its monitor raises an alert if this copy goes stale or bad.
    ($state | ConvertTo-Json -Depth 5 -Compress) | & ssh @SshArgs 'laptop-report' | Out-Null
}
