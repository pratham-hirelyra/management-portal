-- ─────────────────────────────────────────────────────────────────────────────
-- RecruitFlow schema  (run once on a fresh DB)
-- ─────────────────────────────────────────────────────────────────────────────

-- Enums
CREATE TYPE client_stage AS ENUM (
  'lead', 'scraping', 'scraped', 'reachout_sent',
  'interested', 'agreement_sent', 'onboarded', 'disqualified'
);

CREATE TYPE mapping_stage AS ENUM (
  'matched', 'intent_ask', 'interested', 'not_interested',
  'slot_booked', 'interview_done', 'placed', 'rejected'
);

-- ── candidates ────────────────────────────────────────────────────────────────
-- READ-ONLY from portal; written by external voice-screening automation
CREATE TABLE candidates (
  id                           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name                         text,
  age_years                    int,
  gender                       text,
  religion                     text,
  phone                        text UNIQUE,
  email                        text,
  experience                   text,
  cv_url                       text,
  source                       text,
  mentioned_location           text,
  state                        text,
  current_location             text,
  location_lat                 numeric,
  location_lng                 numeric,
  working_radius               int,
  current_salary               numeric,
  expected_salary              numeric,
  job_type                     text,
  industry                     text,
  call_status                  text,
  category                     text,          -- e.g. Accountant | Data Entry Operator
  evaluation_status            text,          -- Pass | Fail
  technical_evaluation_score   numeric,
  total_score                  numeric,
  evaluation_pdf_url           text,
  evaluation_summary           jsonb,
  hiring_decision_reason       text,
  voice_recording_url          text,
  work_preference              text,
  other_details                jsonb,         -- {"gst":"...","tds":"...","tools":"...","others":"..."}
  final_evaluation_report_url  text,
  interview_count              int DEFAULT 0, -- incremented by trigger on each new mapping
  created_at                   timestamptz DEFAULT now(),
  updated_at                   timestamptz DEFAULT now()
);

-- ── clients ───────────────────────────────────────────────────────────────────
CREATE TABLE clients (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_name        text,
  location            text,
  job_location        text,
  location_lat        numeric,
  location_lng        numeric,
  poc_name            text,
  poc_position        text,
  poc_phone           text,
  stage               client_stage DEFAULT 'lead',
  job_title           text,
  industry            text,
  num_employees       int,
  min_salary          numeric,
  max_salary          numeric,
  key_skills          text[],
  job_timings         text,
  gst_tds             jsonb,         -- {"gst":"Filing","tds":"Filing"}
  other_details       text,
  age_requirements    text,
  gender_requirements text,
  tools_requirements  text,
  religion_requirement text,
  payment_terms       text,
  salary_deductions   text,
  job_type            text,
  fee_amount          numeric,
  replacement_period  text,
  agreement_url       text,
  source              text,
  phone_numbers       jsonb    DEFAULT '[]'::jsonb,
  agreed_phone        text,
  company_website     text,
  company_description text,
  source_job_id       text,
  female_employee_pct numeric,
  diwali_bonus        text,
  business_type       text,
  available_slots     jsonb    DEFAULT '[]'::jsonb,
  recruiter_slots     jsonb    DEFAULT '[]'::jsonb,
  created_at          timestamptz DEFAULT now(),
  updated_at          timestamptz DEFAULT now()
);

-- ── scraping_jobs ─────────────────────────────────────────────────────────────
CREATE TABLE scraping_jobs (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id    uuid REFERENCES clients(id) ON DELETE CASCADE,
  scraper_type text,   -- job_portal | apollo | google_maps | custom
  status       text DEFAULT 'pending',  -- pending | running | completed | failed
  result       jsonb,
  triggered_by text,
  triggered_at timestamptz DEFAULT now(),
  completed_at timestamptz,
  error_msg    text
);

-- ── client_candidate_mappings ─────────────────────────────────────────────────
CREATE TABLE client_candidate_mappings (
  id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id               uuid REFERENCES clients(id) ON DELETE CASCADE,
  candidate_id            uuid REFERENCES candidates(id) ON DELETE CASCADE,
  stage                   mapping_stage NOT NULL DEFAULT 'matched',
  match_score             int,           -- 0–100
  wa_sent_at              timestamptz,
  intent_received_at      timestamptz,
  slot_sent_at            timestamptz,
  interview_slot          timestamptz,
  interview_reminder_sent boolean DEFAULT false,
  interview_done          boolean DEFAULT false,
  interview_done_at       timestamptz,
  placement_confirmed     boolean DEFAULT false,
  available_slots         jsonb DEFAULT '[]'::jsonb,
  notes                   text,
  created_at              timestamptz DEFAULT now(),
  updated_at              timestamptz DEFAULT now(),
  UNIQUE(client_id, candidate_id)
);

-- ── whatsapp_messages ─────────────────────────────────────────────────────────
CREATE TABLE whatsapp_messages (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id     uuid REFERENCES clients(id) ON DELETE SET NULL,
  candidate_id  uuid REFERENCES candidates(id) ON DELETE SET NULL,
  mapping_id    uuid REFERENCES client_candidate_mappings(id) ON DELETE SET NULL,
  message_type  text,
  direction     text DEFAULT 'outbound',
  phone         text,
  message_text  text,
  status        text DEFAULT 'pending_review',
  approved_by   text,
  approved_at   timestamptz,
  meta_msg_id   text,
  sent_at       timestamptz,
  created_at    timestamptz DEFAULT now()
);

-- ── pipeline_events ───────────────────────────────────────────────────────────
-- APPEND-ONLY — DB trigger auto-inserts; optionally UPDATE note/created_by after
CREATE TABLE pipeline_events (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id   uuid REFERENCES clients(id) ON DELETE CASCADE,
  from_stage  client_stage,
  to_stage    client_stage NOT NULL,
  note        text,
  created_by  text DEFAULT 'system',
  created_at  timestamptz DEFAULT now()
);

-- ── Triggers ──────────────────────────────────────────────────────────────────

-- 1. Auto-log client stage changes into pipeline_events
CREATE OR REPLACE FUNCTION log_client_stage_change()
RETURNS TRIGGER AS $$
BEGIN
  IF OLD.stage IS DISTINCT FROM NEW.stage THEN
    INSERT INTO pipeline_events (client_id, from_stage, to_stage)
    VALUES (NEW.id, OLD.stage, NEW.stage);
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_client_stage_change
AFTER UPDATE OF stage ON clients
FOR EACH ROW EXECUTE FUNCTION log_client_stage_change();

-- 2. Increment candidate.interview_count each time they get mapped to a client
CREATE OR REPLACE FUNCTION inc_candidate_mapping_count()
RETURNS TRIGGER AS $$
BEGIN
  UPDATE candidates SET interview_count = interview_count + 1 WHERE id = NEW.candidate_id;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_mapping_count
AFTER INSERT ON client_candidate_mappings
FOR EACH ROW EXECUTE FUNCTION inc_candidate_mapping_count();
