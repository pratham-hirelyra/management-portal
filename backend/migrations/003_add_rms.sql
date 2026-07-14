-- Migration 003: Add RMs table and link to clients

CREATE TABLE IF NOT EXISTS rms (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name        text NOT NULL,
    phone       text,
    email       text,
    location    text,
    max_clients int NOT NULL DEFAULT 20,
    is_active   boolean NOT NULL DEFAULT true,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE clients
    ADD COLUMN IF NOT EXISTS rm_id uuid REFERENCES rms(id) ON DELETE SET NULL;
