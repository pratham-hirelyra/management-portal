-- Migration 012: AI voice reachout funnel for clients (RinggAI outbound calls)
--
-- Tracks outbound AI voice calls placed to client POCs ("AI Voice Reachout"),
-- the resulting transcript, and the GPT classification of how the call went
-- (interested / not interested / wrong number / wrong POC / position closed /
-- callback requested / no pickup / dropped).
--
-- Run once: psql $DATABASE_URL -f migrations/012_client_ai_voice_reachout.sql

CREATE TABLE IF NOT EXISTS client_ai_calls (
    id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id              uuid NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    phone                  text,
    ringg_call_id          text,
    status                 text NOT NULL DEFAULT 'queued',
        -- queued | calling | completed | no_pickup | dropped | failed
    transcript             text,
    recording_url          text,
    classification         text,
        -- interested | not_interested | wrong_number | wrong_poc |
        -- position_closed | callback_requested
    classification_reason  text,
    extracted_info         jsonb,
        -- e.g. {"alt_poc_name": "...", "alt_poc_phone": "...", "callback_note": "..."}
    triggered_at           timestamptz NOT NULL DEFAULT now(),
    completed_at           timestamptz
);

CREATE INDEX IF NOT EXISTS idx_client_ai_calls_client_id ON client_ai_calls(client_id);
CREATE INDEX IF NOT EXISTS idx_client_ai_calls_status ON client_ai_calls(status);
