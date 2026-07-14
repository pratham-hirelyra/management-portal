ALTER TABLE client_candidate_mappings
  ADD COLUMN IF NOT EXISTS documents_submitted_at timestamptz;
