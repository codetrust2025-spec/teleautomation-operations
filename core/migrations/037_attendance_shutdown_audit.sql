-- Attendance shutdown is an append-only operational control. Historical
-- attendance and eligibility facts are never deleted or rewritten.
CREATE TABLE IF NOT EXISTS operations_attendance_shutdown_events (
    id TEXT PRIMARY KEY,
    scope TEXT NOT NULL CHECK (scope IN ('GLOBAL', 'HANDLER')),
    target_account_id TEXT,
    action TEXT NOT NULL CHECK (action IN ('SHUTDOWN', 'REACTIVATE')),
    reason TEXT NOT NULL DEFAULT '',
    actor_account_id TEXT NOT NULL,
    actor_username TEXT NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    audit_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    CHECK ((scope = 'GLOBAL' AND target_account_id IS NULL)
        OR (scope = 'HANDLER' AND target_account_id IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS idx_attendance_shutdown_scope_time
    ON operations_attendance_shutdown_events (scope, target_account_id, occurred_at DESC);
