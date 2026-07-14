-- Run this against your PostgreSQL database

-- ── clients table additions ────────────────────────────────────────────────────
ALTER TABLE clients ADD COLUMN IF NOT EXISTS is_dnd        boolean DEFAULT false;
ALTER TABLE clients ADD COLUMN IF NOT EXISTS segment       text;
ALTER TABLE clients ADD COLUMN IF NOT EXISTS open_positions int DEFAULT 1;

-- ── template pool ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS template_pool (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    template_name text    NOT NULL,
    image_url     text,
    segment       text    NOT NULL,   -- 'ca_network' | 'active_job_post' | 'employer_lead' | 'all'
    is_active     boolean DEFAULT true,
    created_at    timestamptz DEFAULT now(),
    updated_at    timestamptz DEFAULT now()
);

-- ── per-segment cron schedule config ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS cron_schedules (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    segment     text UNIQUE NOT NULL,
    cadence     text DEFAULT 'weekly',   -- 'daily' | 'weekly'
    is_active   boolean DEFAULT false,
    last_run_at timestamptz,
    created_at  timestamptz DEFAULT now(),
    updated_at  timestamptz DEFAULT now()
);

INSERT INTO cron_schedules (segment, cadence, is_active)
VALUES
    ('ca_network',      'weekly', false),
    ('active_job_post', 'daily',  false),
    ('employer_lead',   'weekly', false)
ON CONFLICT (segment) DO NOTHING;

-- ── track which template was used per WA message ───────────────────────────────
ALTER TABLE whatsapp_messages ADD COLUMN IF NOT EXISTS used_template_name text;
