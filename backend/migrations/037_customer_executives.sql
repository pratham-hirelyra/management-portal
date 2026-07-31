-- Customer Executive calling system. Deliberately a separate table from `rms`
-- (not reusing RM infra) — RM assignment is permanent/post-onboarding, CE
-- assignment is a daily call queue with shift/reschedule semantics.

CREATE TABLE IF NOT EXISTS customer_executives (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name                 text NOT NULL,
    phone                text,
    pin                  text,
    tier                 text NOT NULL DEFAULT 'top_end',  -- 'top_end' | 'bottom_end' (bottom_end queue logic deferred)
    max_clients_per_day  int  NOT NULL DEFAULT 20,
    is_active            boolean NOT NULL DEFAULT true,
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now()
);

-- One row per (ce, client) — permanent 1:1 attribution, so the "onboarded"
-- metric works no matter how long after the call it happens. "Shift to
-- another date" is just moving call_date forward; carry-over of an uncalled
-- client happens for free since "today's list" is call_date <= today.
CREATE TABLE IF NOT EXISTS ce_assignments (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    ce_id        uuid NOT NULL REFERENCES customer_executives(id) ON DELETE CASCADE,
    client_id    uuid NOT NULL UNIQUE REFERENCES clients(id) ON DELETE CASCADE,
    call_date    date NOT NULL DEFAULT CURRENT_DATE,
    status       text NOT NULL DEFAULT 'pending',  -- 'pending' | 'called'
    sentiment    text,                              -- 'positive' | 'negative' — set when status='called'
    comment      text,
    called_at    timestamptz,
    assigned_at  timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ce_assignments_ce_status_date ON ce_assignments (ce_id, status, call_date);
