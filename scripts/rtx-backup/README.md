# RTX 4060 laptop: off-server backup copy and watchdog

Production VPS → nightly encrypted restic backup with a full restore test (02:00 IST)
→ RTX 4060 laptop mirrors it and verifies every file (03:15 IST and every 2 h)
→ alerts as GitHub issues (email to the owner).

## One-time setup (on the RTX 4060 laptop, as Administrator)

1. Copy this folder (`scripts/rtx-backup`) to the laptop, e.g. `C:\Temp\rtx-backup`.
2. Open **Windows PowerShell → Run as administrator** and run:
   ```powershell
   cd C:\Temp\rtx-backup
   powershell -ExecutionPolicy Bypass -File .\install_rtx_backup.ps1
   ```
   If there is no `D:` drive, or it has less than 20 GB free, add
   `-BackupRoot E:\TeleAutomation\Backups` (any drive with enough space).
3. Send back the **yellow public key line** it prints and the last few lines of
   output. Do **not** send anything from `C:\ProgramData\TeleAutomation\keys`
   except the `.pub` line.
4. Keep the laptop powered on and connected overnight. Sleep is fine: the task
   wakes it at 03:15 if Windows allows wake timers.

## What it does not do

- Opens no inbound port and installs no SSH server. The laptop only connects out,
  with its own key that can run four fixed commands on the server.
- Does not hold the backup password: it cannot read the backups, only prove they
  are intact (every restic file is named by its SHA-256).
- Does not touch Ollama, its tunnel, or the GPU. Tasks run at below-normal priority.

## Restoring from this copy in a disaster

Needs the restic password from the password manager.
```powershell
restic -r D:\TeleAutomation\Backups\restic-mirror snapshots
restic -r D:\TeleAutomation\Backups\restic-mirror restore latest --target D:\restore
```
The restored `stage` folder holds `db\operations.sql`, `db\marketing.sql`, both
data volumes and `host-config.tar`.
