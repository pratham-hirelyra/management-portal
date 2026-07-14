-- Migration 018: add 'churned' client stage + churned_at timestamp
ALTER TYPE client_stage ADD VALUE IF NOT EXISTS 'churned';
ALTER TABLE clients ADD COLUMN IF NOT EXISTS churned_at timestamptz;
