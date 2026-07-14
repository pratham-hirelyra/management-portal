-- Day-before "going ahead?" interview confirmation check (see
-- cron._followup_interview_confirm_check, company_portal.confirm_interview_day).
-- interview_confirm_check_sent already exists (migration 025).
ALTER TABLE client_candidate_mappings
  ADD COLUMN IF NOT EXISTS interview_confirm_responded_at timestamptz,
  ADD COLUMN IF NOT EXISTS client_cancel_reason text;
