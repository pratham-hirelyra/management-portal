-- Number of paid leaves offered, collected on the client onboarding form.
ALTER TABLE clients
  ADD COLUMN IF NOT EXISTS paid_leaves int;
