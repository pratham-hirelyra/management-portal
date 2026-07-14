-- Client (company) portal auth columns — mirrors add_candidate_portal_auth.sql.
-- One company (grouped by poc_phone) can own multiple hiring-requirement rows;
-- OTP/session are written identically to every row sharing a phone, so a single
-- session token authenticates the whole company via one indexed lookup.

ALTER TABLE clients
  ADD COLUMN IF NOT EXISTS poc_otp                 text,
  ADD COLUMN IF NOT EXISTS poc_otp_expires_at       timestamptz,
  ADD COLUMN IF NOT EXISTS poc_session_token        text,
  ADD COLUMN IF NOT EXISTS poc_session_expires_at   timestamptz;

CREATE INDEX IF NOT EXISTS idx_clients_poc_session
  ON clients(poc_session_token) WHERE poc_session_token IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_clients_poc_phone
  ON clients(poc_phone) WHERE poc_phone IS NOT NULL;
