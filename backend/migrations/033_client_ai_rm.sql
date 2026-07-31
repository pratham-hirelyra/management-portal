-- AI Relationship Manager (client WhatsApp channel) — mirrors 030/031_ai_rm.sql
-- on the candidate side. Advisory only: no tool here ever writes clients.stage
-- or any other column that drives the deterministic workflow in
-- routers/whatsapp.py / flow_engine.py — it only explains, and escalates to a
-- human customer executive (rms table) for anything that needs one.
-- See services/ai_client_rm_service.py.

-- Per-client rollout / exclusion dial — same two-layer gate as the candidate
-- side (CLIENT_AI_RM_ENABLED env is the global kill switch, this column is
-- the per-client one).
ALTER TABLE clients
  ADD COLUMN IF NOT EXISTS ai_rm_enabled boolean NOT NULL DEFAULT false;

-- Append-only audit log — one row per free-text message the agent handled (or
-- declined to handle). Mirrors candidate_ai_turns.
CREATE TABLE IF NOT EXISTS client_ai_turns (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id       uuid REFERENCES clients(id) ON DELETE SET NULL,
    phone           text NOT NULL,
    inbound_text    text NOT NULL,
    action_taken    text NOT NULL,     -- reply | escalate | no_reply
    reply_text      text,
    escalated       boolean NOT NULL DEFAULT false,
    escalate_reason text,
    tool_calls      jsonb DEFAULT '[]'::jsonb,
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_client_ai_turns_client_created
    ON client_ai_turns(client_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_client_ai_turns_phone_created
    ON client_ai_turns(phone, created_at DESC);

-- Static, evergreen, non-client-specific answers — editable without a deploy.
-- Never holds anything client-specific (salary range, company name, stage) —
-- that always comes fresh from a read tool. Mirrors candidate_faqs.
CREATE TABLE IF NOT EXISTS client_faqs (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    topic             text NOT NULL,
    question_patterns text[] NOT NULL DEFAULT '{}',
    answer            text NOT NULL,
    applies_to_stage  client_stage,     -- NULL = applies at any stage
    is_active         boolean NOT NULL DEFAULT true,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now()
);

INSERT INTO client_faqs (topic, question_patterns, answer, applies_to_stage) VALUES
  ('who_we_are',
   ARRAY['what do you guys do', 'what is justaccountants', 'who is this', 'is this genuine', 'how does this work'],
   'JustAccountants is a recruitment firm that specialises in placing accounting professionals — we source, screen, and match candidates to employers across India, and handle the process end-to-end from shortlisting to offer.',
   NULL),
  ('positions_we_fill',
   ARRAY['what roles do you fill', 'what positions', 'do you hire cashiers', 'what kind of accountant', 'junior or senior', 'do you place cas', 'chartered accountant', 'fresher ca'],
   'Our hiring scope is strictly Accountants — Junior and Senior Accountant roles, covering bookkeeping, GST/TDS filing, reconciliation, and ledger work. We do not place Chartered Accountants (CAs) as a qualification-based hire, and we do not recruit for cashier roles; if that is what you are looking for, we are not the right fit today, but we will reach out if that changes in future.',
   NULL),
  ('how_matching_works',
   ARRAY['how do you find candidates', 'how does matching work', 'when will i get candidates', 'how long does it take'],
   'Once your requirement is confirmed, we match candidates from our screened pool against your role and share their profiles for your review — most clients start seeing relevant profiles within a couple of days.',
   NULL),
  ('replacement_policy',
   ARRAY['what if it does not work out', 'replacement policy', 'what if the candidate leaves'],
   'Replacement terms are part of your agreement with us — your customer executive can confirm the specifics that apply to you.',
   NULL)
ON CONFLICT DO NOTHING;

-- Debounce buffer for clients who send a question across several rapid
-- WhatsApp messages instead of one. Mirrors candidate_ai_pending.
CREATE TABLE IF NOT EXISTS client_ai_pending (
    phone         text PRIMARY KEY,
    buffered_text text NOT NULL DEFAULT '',
    updated_at    timestamptz NOT NULL DEFAULT now()
);
