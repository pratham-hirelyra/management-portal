-- These columns/enum values were previously added by re-running
-- "ADD COLUMN IF NOT EXISTS" DDL on every single cron tick (every 30 min,
-- forever) from routers/cron.py, on the live candidates/clients/
-- client_candidate_mappings tables. Even a no-op ADD COLUMN IF NOT EXISTS
-- needs an ACCESS EXCLUSIVE lock to check the catalog, which conflicts with
-- ordinary reads/writes on a busy table — this caused a production
-- LockNotAvailableError (lock timeout) in _retry_dropped_calls. Moving these
-- to a one-time migration and deleting the runtime DDL calls removes the
-- risk entirely, since the columns only ever need to be created once.

ALTER TYPE mapping_stage ADD VALUE IF NOT EXISTS 'wa_sent';
ALTER TYPE mapping_stage ADD VALUE IF NOT EXISTS 'slot_requested_client';
ALTER TYPE mapping_stage ADD VALUE IF NOT EXISTS 'slot_sent_candidate';
ALTER TYPE mapping_stage ADD VALUE IF NOT EXISTS 'interview_scheduled';

ALTER TABLE candidates ADD COLUMN IF NOT EXISTS call_drop_retry_count int DEFAULT 0;
ALTER TABLE candidates ADD COLUMN IF NOT EXISTS last_call_drop_retry_at timestamptz;
ALTER TABLE candidates ADD COLUMN IF NOT EXISTS drop_final_sent boolean DEFAULT false;
ALTER TABLE candidates ADD COLUMN IF NOT EXISTS cv_phone_redacted boolean DEFAULT false;

ALTER TABLE clients ADD COLUMN IF NOT EXISTS agreement_sent_at timestamptz;
ALTER TABLE clients ADD COLUMN IF NOT EXISTS agreement_reminder_count int DEFAULT 0;
ALTER TABLE clients ADD COLUMN IF NOT EXISTS last_agreement_reminder_at timestamptz;
ALTER TABLE clients ADD COLUMN IF NOT EXISTS last_feedback_reminder_at timestamptz;
ALTER TABLE clients ADD COLUMN IF NOT EXISTS last_review_reminder_at timestamptz;
ALTER TABLE clients ADD COLUMN IF NOT EXISTS enrich_status text DEFAULT 'pending';

ALTER TABLE client_candidate_mappings ADD COLUMN IF NOT EXISTS slot_reminder_count int DEFAULT 0;
ALTER TABLE client_candidate_mappings ADD COLUMN IF NOT EXISTS last_slot_reminder_at timestamptz;
ALTER TABLE client_candidate_mappings ADD COLUMN IF NOT EXISTS slot_link_sent_at timestamptz;
ALTER TABLE client_candidate_mappings ADD COLUMN IF NOT EXISTS slot_ai_call_triggered boolean DEFAULT false;
ALTER TABLE client_candidate_mappings ADD COLUMN IF NOT EXISTS slot_selection_reminder_count int DEFAULT 0;
ALTER TABLE client_candidate_mappings ADD COLUMN IF NOT EXISTS last_slot_selection_reminder_at timestamptz;
ALTER TABLE client_candidate_mappings ADD COLUMN IF NOT EXISTS interview_day_reminder_sent bool DEFAULT false;
ALTER TABLE client_candidate_mappings ADD COLUMN IF NOT EXISTS interview_3h_reminder_sent bool DEFAULT false;
ALTER TABLE client_candidate_mappings ADD COLUMN IF NOT EXISTS interview_1h_reminder_sent bool DEFAULT false;
ALTER TABLE client_candidate_mappings ADD COLUMN IF NOT EXISTS post_interview_feedback_sent bool DEFAULT false;
ALTER TABLE client_candidate_mappings ADD COLUMN IF NOT EXISTS documents_requested_at timestamptz;
ALTER TABLE client_candidate_mappings ADD COLUMN IF NOT EXISTS passive_form_sent_at timestamptz;
ALTER TABLE client_candidate_mappings ADD COLUMN IF NOT EXISTS passive_form_reminder_count int DEFAULT 0;
ALTER TABLE client_candidate_mappings ADD COLUMN IF NOT EXISTS interested_form_sent_at timestamptz;
ALTER TABLE client_candidate_mappings ADD COLUMN IF NOT EXISTS interested_form_reminder_count int DEFAULT 0;
ALTER TABLE client_candidate_mappings ADD COLUMN IF NOT EXISTS join_intent_requested_at timestamptz;
ALTER TABLE client_candidate_mappings ADD COLUMN IF NOT EXISTS join_intent_responded_at timestamptz;
ALTER TABLE client_candidate_mappings ADD COLUMN IF NOT EXISTS join_decline_reason text;
ALTER TABLE client_candidate_mappings ADD COLUMN IF NOT EXISTS documents_reminder_count int DEFAULT 0;
ALTER TABLE client_candidate_mappings ADD COLUMN IF NOT EXISTS last_documents_reminder_at timestamptz;
