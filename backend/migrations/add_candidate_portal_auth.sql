-- Candidate portal auth columns
ALTER TABLE candidates
  ADD COLUMN IF NOT EXISTS portal_otp                  text,
  ADD COLUMN IF NOT EXISTS portal_otp_expires_at       timestamptz,
  ADD COLUMN IF NOT EXISTS portal_session_token        text,
  ADD COLUMN IF NOT EXISTS portal_session_expires_at   timestamptz;

CREATE INDEX IF NOT EXISTS idx_candidates_portal_session
  ON candidates(portal_session_token)
  WHERE portal_session_token IS NOT NULL;
