-- Multi-city (pan-India) expansion: single DB-backed source of truth for which
-- cities client sourcing/scraping is currently allowed in. Activating a new
-- city later is a data change only (INSERT a row) — no code deploy needed.
CREATE TABLE active_cities (
  city         text PRIMARY KEY,
  state        text NOT NULL,
  naukri_code  text,              -- Naukri's internal numeric city id; NULL = skip Naukri for this city
  is_active    boolean NOT NULL DEFAULT true,
  created_at   timestamptz DEFAULT now()
);
INSERT INTO active_cities (city, state, naukri_code) VALUES ('Ahmedabad', 'Gujarat', '51');

-- clients.city already exists (migration 004); candidates.state already exists
-- (base schema, external automation). Add the missing counterpart to each so
-- both tables are symmetric for city-wise analytics.
ALTER TABLE clients ADD COLUMN IF NOT EXISTS state text;
ALTER TABLE candidates ADD COLUMN IF NOT EXISTS city text;
