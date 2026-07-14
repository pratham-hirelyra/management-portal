ALTER TABLE client_candidate_mappings
  ADD COLUMN IF NOT EXISTS interview_round int DEFAULT 1,
  ADD COLUMN IF NOT EXISTS interview_history jsonb DEFAULT '[]',
  ADD COLUMN IF NOT EXISTS documents jsonb,
  ADD COLUMN IF NOT EXISTS intent_letter_url text,
  ADD COLUMN IF NOT EXISTS offered_salary numeric,
  ADD COLUMN IF NOT EXISTS offer_issued_at timestamptz,
  ADD COLUMN IF NOT EXISTS offer_expires_at timestamptz,
  ADD COLUMN IF NOT EXISTS interview_confirm_check_sent boolean DEFAULT false;
