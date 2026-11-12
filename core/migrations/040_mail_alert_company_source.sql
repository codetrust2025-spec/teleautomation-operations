-- Interview Related: where a stored company came from.
--
-- An alert the model filed without a company may have it proved later from the
-- alert's own evidence (core/mail_alert_company.py). When
-- scripts/backfill_alert_companies.py stores that company, it records the
-- evidence here: booking, thread, sender, forwarded sender, job board relay,
-- subject, email body or sender name. NULL means the company is the one the
-- alert was filed with (or there is none). Only such NULL rows serve as
-- evidence for other alerts, so a resolved name never proves another.
ALTER TABLE mail_monitoring_notifications ADD COLUMN IF NOT EXISTS company_source text;
