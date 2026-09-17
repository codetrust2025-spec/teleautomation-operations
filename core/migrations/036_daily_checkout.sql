-- Daily check-out: the end of a working day, recorded beside its start.
--
-- One attendance row already exists per account per day, so check-out is a
-- column on it rather than a table of its own: that makes "one check-out per
-- day" a property of the schema instead of a rule the code has to keep.

ALTER TABLE operations_attendance_records
    ADD COLUMN IF NOT EXISTS checked_out_at TIMESTAMPTZ;

ALTER TABLE operations_attendance_records
    ADD COLUMN IF NOT EXISTS checkout_network_verification JSONB;

ALTER TABLE operations_attendance_records
    ADD COLUMN IF NOT EXISTS checkout_metadata JSONB;

-- Admin views ask "who is still working?" for one date; without this that is a
-- sequential scan of every attendance record ever written.
CREATE INDEX IF NOT EXISTS idx_operations_attendance_checkout_date
    ON operations_attendance_records (attendance_date)
    WHERE checked_out_at IS NULL;
