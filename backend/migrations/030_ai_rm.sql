-- AI Relationship Manager (candidate WhatsApp channel) — advisory only, never
-- writes to client_candidate_mappings.stage or any other workflow-driving table.
-- See services/ai_rm_service.py.

-- Per-candidate rollout / exclusion dial. Two-layer gate alongside the
-- CANDIDATE_AI_RM_ENABLED env flag: the env flag is the global kill switch,
-- this column is the per-person one — used both for cohort rollout and later
-- as a permanent per-candidate opt-out.
ALTER TABLE candidates
  ADD COLUMN IF NOT EXISTS ai_rm_enabled boolean NOT NULL DEFAULT false;

-- Append-only audit log — one row per free-text message the agent handled
-- (or declined to handle). Never read by the workflow engine; exists for the
-- turn-budget guardrail and for reviewing what the AI got right or wrong.
CREATE TABLE IF NOT EXISTS candidate_ai_turns (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    candidate_id  uuid REFERENCES candidates(id) ON DELETE SET NULL,
    mapping_id    uuid REFERENCES client_candidate_mappings(id) ON DELETE SET NULL,
    phone         text NOT NULL,
    inbound_text  text NOT NULL,
    intent        text,              -- prospective_contact | workflow_nudge | informational | off_topic | escalation | unparseable
    confidence    numeric,
    action_taken  text NOT NULL,     -- reply | escalate | no_reply
    reply_text    text,
    escalated     boolean NOT NULL DEFAULT false,
    escalate_reason text,
    tool_calls    jsonb DEFAULT '[]'::jsonb,   -- ordered list of read-tool names called this turn, for review
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ai_turns_candidate_created
    ON candidate_ai_turns(candidate_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ai_turns_phone_created
    ON candidate_ai_turns(phone, created_at DESC);

-- Static, evergreen, non-candidate-specific answers — editable by an RM
-- without a deploy. Never holds anything candidate- or client-specific
-- (salary, company name, stage) — that always comes fresh from a read tool.
CREATE TABLE IF NOT EXISTS candidate_faqs (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    topic             text NOT NULL,
    question_patterns text[] NOT NULL DEFAULT '{}',
    answer            text NOT NULL,
    applies_to_stage  mapping_stage,     -- NULL = applies at any stage
    is_active         boolean NOT NULL DEFAULT true,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now()
);

INSERT INTO candidate_faqs (topic, question_patterns, answer, applies_to_stage) VALUES
  ('ai_screening_call',
   ARRAY['why is an ai calling me', 'is this call real', 'is this a bot call', 'why not a real person'],
   'The first step in our process is a short 5-minute automated call that asks a few basic accounting questions — it''s how we screen efficiently so a human recruiter can focus time on candidates who are a strong fit. It''s genuinely from JustAccountants, not spam.',
   NULL),
  ('documents_required',
   ARRAY['what documents', 'which documents', 'why do you need my bank statement', 'why do you need my salary slip'],
   'Once a client confirms they want to hire you, we ask for your last 3 months'' salary slips and a bank statement — that''s all we need. These help the employer verify your salary history before finalising your offer.',
   'documents_requested'),
  ('joining_process',
   ARRAY['what happens after i join', 'do i need to resign first', 'when do i start'],
   'Once you accept an offer, your start date is agreed directly with the employer. There''s nothing further you need to submit to us at that point beyond the documents already requested.',
   NULL),
  ('general_process',
   ARRAY['how does this work', 'what is justaccountants', 'is this genuine', 'who is this'],
   'JustAccountants is a recruitment firm that matches accounting professionals with employers across India. If you''re currently job-hunting, we''ll walk you through a short screening and match you to roles that fit your experience and salary expectations.',
   NULL)
ON CONFLICT DO NOTHING;
