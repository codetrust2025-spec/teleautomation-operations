# Daybreak consent-aware onboarding — implementation review

This branch adds a **generic consent attestation** to Operations service-account forms. Only an authenticated administrator can attest, and the saved timestamp, identity and account email are set by the server. Changing the email invalidates a prior attestation; revocation clears it. The frontend checkbox is shown for Naukri accounts, not other service types.

## Safety boundary

This is **not yet automatic account creation**. The live 5-minute sync still delivers credentials only for existing matched Daybreak candidates. The existing signed Daybreak `prepare`/`commit` protocol already supports explicit candidate creation with a confirmed consent assertion, but the 5-minute worker has not been changed to invoke it.

**Do not merge or deploy this draft** as the full automation feature. To finish it, the server must re-check the stored consent against the current exact email and password, exclude duplicates/missing keys, call the existing encrypted delivery protocol with `create_ids` only for eligible accounts, and verify Daybreak roster and worker receipts. The routine must be idempotent and bounded. No person-specific names or exceptions. Never infer consent from an imported credential alone.

The Daybreak codebase already implements its own scheduled daily updates, primary/backup worker leasing, independently verified saves and uncertain-save fences. None of those write paths are changed in this branch. Releasing a live daily save workflow requires the existing owner approval, production backup, idle/health gates and rollback verification.

Current production must remain unchanged until both code and safety tests are complete and an owner approves the exact release.
