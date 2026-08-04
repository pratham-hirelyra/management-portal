-- HireLyra ticketing system (bottom_end CE tier). Replaces two undocumented
-- fire-and-forget escalation paths (AI RM escalate() -> notify_rm_alert /
-- Google Chat, and client_support_bot's CLIENT_STUCK_* -> the same) plus all
-- ~50 engineering google_chat_service.send_alert() calls with one claimable,
-- trackable entity: the ticket. See services/ticket_service.py.

-- ── Queues (admin-configurable, not hardcoded) ─────────────────────────────
CREATE TABLE IF NOT EXISTS ticket_queues (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name        text NOT NULL UNIQUE,
    code        text NOT NULL UNIQUE,       -- stable slug, referenced from code (e.g. 'candidate_queue')
    description text,
    is_active   boolean NOT NULL DEFAULT true,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

-- ── Categories, scoped to exactly one queue ────────────────────────────────
CREATE TABLE IF NOT EXISTS ticket_categories (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    queue_id    uuid NOT NULL REFERENCES ticket_queues(id) ON DELETE CASCADE,
    name        text NOT NULL,
    code        text NOT NULL,              -- e.g. 'ai_escalation', 'commercial_escalation'
    is_active   boolean NOT NULL DEFAULT true,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (queue_id, code)
);

-- ── executive <-> queue visibility, many-to-many ───────────────────────────
CREATE TABLE IF NOT EXISTS executive_queue_access (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    ce_id       uuid NOT NULL REFERENCES customer_executives(id) ON DELETE CASCADE,
    queue_id    uuid NOT NULL REFERENCES ticket_queues(id) ON DELETE CASCADE,
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (ce_id, queue_id)
);

-- ── tickets — the central entity ────────────────────────────────────────────
-- No ticket-number sequence, deliberately: no other entity in this codebase
-- (clients, candidates, mappings) has a friendly sequence — everything is
-- referenced by uuid. Search-by-id uses id::text ILIKE, matching that.
CREATE TABLE IF NOT EXISTS tickets (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    queue_id           uuid NOT NULL REFERENCES ticket_queues(id),
    category_id        uuid NOT NULL REFERENCES ticket_categories(id),
    status             text NOT NULL DEFAULT 'NEW',
        -- 'NEW' | 'CLAIMED' | 'IN_PROGRESS' | 'WAITING_FOR_CUSTOMER' |
        -- 'CUSTOMER_REPLIED' | 'RESOLVED' | 'REOPENED'
    priority           text NOT NULL DEFAULT 'MEDIUM',   -- 'LOW'|'MEDIUM'|'HIGH'|'CRITICAL'
    source             text NOT NULL,
        -- 'ai_escalation' | 'commercial_escalation' | 'support_bot_stuck' |
        -- 'system_alert' | 'manual' | 'stuck_in_pipeline' | 'ready_to_advance'
        -- (last two reserved — no detector wired this pass, no future migration needed)
    subject            text,
    phone              text,                -- null for channel='internal' (engineering tickets)
    channel            text NOT NULL,       -- 'client' | 'candidate' | 'internal'
    client_id          uuid REFERENCES clients(id) ON DELETE SET NULL,
    candidate_id       uuid REFERENCES candidates(id) ON DELETE SET NULL,
    mapping_id         uuid REFERENCES client_candidate_mappings(id) ON DELETE SET NULL,
    fingerprint        text,                -- engineering-alert dedupe key; null for all human-channel tickets
    occurrence_count   int NOT NULL DEFAULT 1,
    claimed_by         uuid REFERENCES customer_executives(id) ON DELETE SET NULL,
    claimed_at         timestamptz,
    first_response_at  timestamptz,
    resolved_at        timestamptz,
    reopened_at        timestamptz,
    reopen_count       int NOT NULL DEFAULT 0,
    last_activity_at   timestamptz NOT NULL DEFAULT now(),
    created_by         uuid REFERENCES customer_executives(id),  -- null for AI/system-created; set for manual creation
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_tickets_queue_status       ON tickets (queue_id, status);
CREATE INDEX IF NOT EXISTS idx_tickets_claimed_by         ON tickets (claimed_by);
CREATE INDEX IF NOT EXISTS idx_tickets_phone              ON tickets (phone);
CREATE INDEX IF NOT EXISTS idx_tickets_client_id           ON tickets (client_id);
CREATE INDEX IF NOT EXISTS idx_tickets_candidate_id        ON tickets (candidate_id);
CREATE INDEX IF NOT EXISTS idx_tickets_created_at           ON tickets (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tickets_last_activity_at     ON tickets (last_activity_at DESC);
-- "unclaimed-first per visible queue" — the hottest query in this feature
CREATE INDEX IF NOT EXISTS idx_tickets_unclaimed_by_queue
    ON tickets (queue_id, created_at) WHERE claimed_by IS NULL AND status <> 'RESOLVED';
-- reopen-window lookup ("most recently resolved ticket for this phone")
CREATE INDEX IF NOT EXISTS idx_tickets_phone_resolved
    ON tickets (phone, resolved_at DESC) WHERE status = 'RESOLVED';
-- "does an open ticket already exist for this phone" (dedup on repeat escalation)
CREATE INDEX IF NOT EXISTS idx_tickets_phone_open
    ON tickets (phone) WHERE status <> 'RESOLVED';
-- engineering-alert fingerprint dedupe
CREATE INDEX IF NOT EXISTS idx_tickets_fingerprint_open
    ON tickets (fingerprint) WHERE fingerprint IS NOT NULL AND status <> 'RESOLVED';

-- ── Activity/timeline log — notes, status changes, system events all share ─
-- this one table via a `type` discriminator, rather than a separate
-- internal_notes table: the center timeline is already a UNION of
-- whatsapp_messages + "everything else that happened on the ticket," and
-- notes/status-changes/reassignments have an identical shape (ticket_id,
-- actor, timestamp, text). Notes (type='note') are never read by any
-- customer-facing surface.
CREATE TABLE IF NOT EXISTS ticket_activities (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    ticket_id   uuid NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    type        text NOT NULL,
        -- 'created' | 'claimed' | 'note' | 'reassigned' | 'resolved' |
        -- 'reopened' | 'customer_replied' | 'system'
    actor_ce_id uuid REFERENCES customer_executives(id) ON DELETE SET NULL,  -- null = AI/system
    actor_label text,        -- 'AI Agent' / 'System' / 'Customer' / CE-name snapshot (survives CE deletion)
    body        text,
    meta        jsonb,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ticket_activities_ticket_created ON ticket_activities (ticket_id, created_at);

-- ── whatsapp_messages: link into ticket timeline, don't duplicate text ─────
ALTER TABLE whatsapp_messages ADD COLUMN IF NOT EXISTS ticket_id uuid REFERENCES tickets(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_whatsapp_messages_ticket_id ON whatsapp_messages (ticket_id);

-- ── Seed queues ─────────────────────────────────────────────────────────────
INSERT INTO ticket_queues (name, code, description) VALUES
  ('Candidate Queue',   'candidate_queue',   'Candidate-facing WhatsApp support — interview, offer, documents, joining.'),
  ('Client Queue',      'client_queue',      'Employer-facing WhatsApp support — hiring process, requirement changes, replacements.'),
  ('Closure Queue',     'closure_queue',     'Business-closure / cancellation / churn handling — restricted to the closure team.'),
  ('Payments Queue',    'payments_queue',    'Commercial and contractual matters — fee, invoices, payment terms — restricted to finance.'),
  ('Engineering Queue', 'engineering_queue', 'System/integration errors that used to page Google Chat — now a claimable ticket queue.')
ON CONFLICT (code) DO NOTHING;

-- ── Seed categories ──────────────────────────────────────────────────────────
-- Deliberate adaptation of the spec's illustrative flat list: Invoice/Payment
-- and Commercial/Contractual route to Payments Queue (finance-only
-- visibility) rather than Client Queue, matching the spec's own "Payments
-- Queue only to finance" example. AI Escalation and Commercial/Contractual
-- each get rows under multiple queues since both AI channels (client,
-- candidate) produce them. Stuck in Pipeline / Ready to Advance are seeded
-- but unused until a future detection pass wires them up (no migration
-- needed then). Engineering Queue's categories mirror the four
-- send_alert() category values.
INSERT INTO ticket_categories (queue_id, name, code)
SELECT q.id, c.name, c.code FROM (VALUES
    ('candidate_queue',   'Interview',              'interview'),
    ('candidate_queue',   'Salary',                 'salary'),
    ('candidate_queue',   'Documents',              'documents'),
    ('candidate_queue',   'Joining',                'joining'),
    ('candidate_queue',   'Offer',                  'offer'),
    ('candidate_queue',   'Technical Issue',        'technical_issue'),
    ('candidate_queue',   'Other',                  'other'),
    ('candidate_queue',   'AI Escalation',          'ai_escalation'),
    ('candidate_queue',   'Commercial/Contractual', 'commercial_escalation'),
    ('client_queue',      'Hiring Issue',           'hiring_issue'),
    ('client_queue',      'Candidate Replacement',  'candidate_replacement'),
    ('client_queue',      'Job Requirement Update', 'job_requirement_update'),
    ('client_queue',      'Technical Issue',        'technical_issue'),
    ('client_queue',      'AI Escalation',          'ai_escalation'),
    ('client_queue',      'Client Stuck',           'client_stuck'),
    ('client_queue',      'Stuck in Pipeline',      'stuck_in_pipeline'),   -- reserved, no detection built this pass
    ('client_queue',      'Ready to Advance',       'ready_to_advance'),    -- reserved, no detection built this pass
    ('payments_queue',    'Invoice',                'invoice'),
    ('payments_queue',    'Payment',                'payment'),
    ('payments_queue',    'Commercial/Contractual', 'commercial_escalation'),
    ('closure_queue',     'Closure',                'closure'),
    ('engineering_queue', 'Integration Failure',    'integration_failure'),
    ('engineering_queue', 'Cron Failure',           'cron_failure'),
    ('engineering_queue', 'Unhandled Exception',    'unhandled_exception'),
    ('engineering_queue', 'Other',                  'other')
) AS c(queue_code, name, code)
JOIN ticket_queues q ON q.code = c.queue_code
ON CONFLICT (queue_id, code) DO NOTHING;

-- ── Seed a developer CE so engineering tickets land somewhere visible ──────
INSERT INTO customer_executives (name, tier, is_active, max_clients_per_day)
SELECT 'Engineering', 'bottom_end', true, 999
WHERE NOT EXISTS (SELECT 1 FROM customer_executives WHERE name = 'Engineering' AND tier = 'bottom_end');

INSERT INTO executive_queue_access (ce_id, queue_id)
SELECT ce.id, q.id
FROM customer_executives ce, ticket_queues q
WHERE ce.name = 'Engineering' AND ce.tier = 'bottom_end' AND q.code = 'engineering_queue'
ON CONFLICT (ce_id, queue_id) DO NOTHING;
