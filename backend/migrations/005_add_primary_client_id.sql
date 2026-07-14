-- Migration: link secondary job positions back to the primary client
-- Run once: psql $DATABASE_URL -f migrations/005_add_primary_client_id.sql

ALTER TABLE clients
    ADD COLUMN IF NOT EXISTS primary_client_id uuid REFERENCES clients(id);

CREATE INDEX IF NOT EXISTS idx_clients_primary_client_id
    ON clients (primary_client_id);
