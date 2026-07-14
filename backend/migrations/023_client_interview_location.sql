-- Interview location shown/editable in the client-portal invite confirmation popup.
-- Defaults to NULL — frontend falls back to displaying clients.job_location until
-- the client explicitly sets a distinct interview location.
ALTER TABLE clients
  ADD COLUMN IF NOT EXISTS interview_location text;
