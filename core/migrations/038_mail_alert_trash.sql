-- Mail Alerts Trash: an alert an operator removed keeps a complete copy here,
-- and the email it came from can never raise an Interview Related alert again.
--
-- Trashing copies the whole alert row into `row_data` and hides it from the
-- screen. Purging deletes the alert row itself and stamps `purged_at`; the
-- trash row stays, so the copy and the reimport block both outlive the purge.
-- Restoring re-inserts `row_data` and removes the trash row.
--
-- Only alert rows are removed. The email, its analyses, bookings, payments
-- and audit history are never touched, and nothing is deleted from Gmail.
CREATE TABLE IF NOT EXISTS mail_alert_trash (
    notification_id TEXT PRIMARY KEY,
    gmail_message_id TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    classification TEXT NOT NULL,
    row_data JSONB NOT NULL,
    reason TEXT NOT NULL,
    trashed_by TEXT NOT NULL,
    trashed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    purged_at TIMESTAMPTZ,
    backup_reference TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_mail_alert_trash_message
    ON mail_alert_trash (gmail_message_id);

-- Every path that writes an alert (live mail, reprocessing, backfills, the
-- deep scan) goes through this INSERT, so the block lives in the database.
-- It covers Interview Related classifications only: a Selection Related alert
-- from the same email is never blocked. Keep this list in step with
-- INTERVIEW_RELATED_CLASSIFICATIONS in core/recruitment_mail_store.py
-- (tests/test_mail_alert_trash.py checks it).
CREATE OR REPLACE FUNCTION mail_alert_trash_blocks_reinsert() RETURNS trigger AS $$
BEGIN
    IF NEW.classification IN (
        'final_round_cleared', 'interview_cancelled', 'interview_confirmed',
        'interview_needs_reading', 'interview_rescheduled', 'interview_shortlisted',
        'post_interview_shortlisted'
    ) AND EXISTS (
        SELECT 1 FROM mail_alert_trash t WHERE t.gmail_message_id = NEW.gmail_message_id
    ) THEN
        RETURN NULL;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS mail_alert_trash_blocks_reinsert ON mail_monitoring_notifications;
CREATE TRIGGER mail_alert_trash_blocks_reinsert
    BEFORE INSERT ON mail_monitoring_notifications
    FOR EACH ROW EXECUTE FUNCTION mail_alert_trash_blocks_reinsert();
