# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

# RecruitFlow — Project Context for Claude Code

## What this is
A recruitment consultancy CRM portal. Manages client pipeline (lead → onboarded), maps candidates to clients, tracks interview progress, sends WhatsApp messages via Meta Cloud API, and triggers scraping APIs to enrich client contact data.

---

## Tech stack

### Backend
- Python 3.11+, FastAPI (async), asyncpg (PostgreSQL), httpx, pydantic v2, python-dotenv
- Local PostgreSQL 16 database
- Run with: `uvicorn main:app --reload --port 8000`

### Frontend
- React 18, Vite, TypeScript, Tailwind CSS
- TanStack Query v5, React Router v6, Axios, date-fns
- Run with: `npm run dev` (port 5173)

---

## Environment variables

### `backend/.env`
```
DATABASE_URL=postgresql://recruitflow:yourpassword@localhost:5432/recruitflow_db
WHATSAPP_API_TOKEN=
WHATSAPP_PHONE_NUMBER_ID=
WHATSAPP_VERIFY_TOKEN=recruitflow_verify_123
WHATSAPP_REACHOUT_TEMPLATE_NAME=client_reachout_v1
WHATSAPP_REACHOUT_TEMPLATE_LANG=en
SCRAPER_JOB_PORTAL_URL=http://localhost:9001/scrape
SCRAPER_APOLLO_URL=http://localhost:9002/scrape
SCRAPER_GOOGLE_MAPS_URL=http://localhost:9003/scrape
SCRAPER_CUSTOM_URL=http://localhost:9004/scrape
MATCHING_API_URL=http://localhost:9005/match
```

### `frontend/.env`
```
VITE_API_BASE_URL=http://localhost:8000
```

---

## File structure

```
recruitflow/
├── CLAUDE.md
├── backend/
│   ├── main.py
│   ├── database.py
│   ├── requirements.txt
│   ├── .env
│   ├── models/
│   │   ├── __init__.py
│   │   ├── client.py
│   │   ├── candidate.py
│   │   ├── mapping.py
│   │   └── whatsapp.py
│   ├── routers/
│   │   ├── __init__.py
│   │   ├── clients.py
│   │   ├── candidates.py
│   │   ├── mappings.py
│   │   ├── scraping.py
│   │   ├── whatsapp.py
│   │   └── pipeline.py
│   └── services/
│       ├── __init__.py
│       ├── whatsapp_service.py
│       ├── scraper_service.py
│       └── matching_service.py
│
└── frontend/
    ├── index.html
    ├── vite.config.ts
    ├── tailwind.config.ts
    ├── .env
    └── src/
        ├── main.tsx
        ├── App.tsx
        ├── api/
        │   ├── axios.ts
        │   ├── clients.ts
        │   ├── candidates.ts
        │   ├── mappings.ts
        │   └── whatsapp.ts
        ├── pages/
        │   ├── ClientsPage.tsx
        │   ├── ClientDetailPage.tsx
        │   ├── CandidatesPage.tsx
        │   └── WhatsAppPendingPage.tsx
        ├── components/
        │   ├── ClientCard.tsx
        │   ├── ClientForm.tsx
        │   ├── ScrapeButtons.tsx
        │   ├── StageTimeline.tsx
        │   ├── MappedCandidateRow.tsx
        │   ├── MappingActionButton.tsx
        │   ├── WhatsAppReviewModal.tsx
        │   ├── SlotsInputModal.tsx
        │   └── BulkWAModal.tsx
        └── types/
            └── index.ts
```

---

## Database

### PostgreSQL enums
```sql
client_stage:  lead | scraping | scraped | reachout_sent | interested | agreement_sent | onboarded | disqualified
mapping_stage: matched | wa_sent | intent_positive | intent_negative | resume_sent |
               slot_requested_client | slot_sent_candidate | interview_scheduled |
               interview_done | placed | rejected_by_client
```

### Tables (all columns)

**candidates** — READ-ONLY from portal, written by external automation
```
id uuid PK, name text, age_years int, gender text, religion text,
phone text UNIQUE, experience text, cv_url text, source text,
current_location text, location_lat numeric, location_lng numeric,
working_radius int, current_salary numeric, expected_salary numeric,
job_type text, industry text, evaluation_status text, total_score numeric,
voice_recording_url text, work_preference text,
other_details jsonb,  -- {"gst":"...","tds":"...","tools":"...","others":"..."}
final_evaluation_report_url text, interview_count int default 0,
created_at timestamptz, updated_at timestamptz
```

**clients**
```
id uuid PK, company_name text, location text, job_location text,
location_lat numeric, location_lng numeric,
poc_name text, poc_position text, poc_phone text,
stage client_stage default 'lead',
job_title text, industry text, num_employees int,
min_salary numeric, max_salary numeric,
key_skills text[], job_timings text,
gst_tds jsonb,           -- {"gst":"Filing","tds":"Filing"}
other_details text, age_requirements text, gender_requirements text,
tools_requirements text, religion_requirement text,
payment_terms text, salary_deductions text, job_type text,
fee_amount numeric, replacement_period text,
agreement_url text, source text,
created_at timestamptz, updated_at timestamptz
```

**scraping_jobs**
```
id uuid PK, client_id uuid FK→clients,
scraper_type text,  -- 'job_portal' | 'apollo' | 'google_maps' | 'custom'
status text,        -- 'pending' | 'running' | 'completed' | 'failed'
result jsonb, triggered_by text,
triggered_at timestamptz, completed_at timestamptz, error_msg text
```

**client_candidate_mappings**
```
id uuid PK,
client_id uuid FK→clients, candidate_id uuid FK→candidates,
stage mapping_stage default 'matched',
match_score int,          -- 0–100
wa_sent_at timestamptz, intent_received_at timestamptz,
resume_sent_at timestamptz, slot_requested_at timestamptz,
available_slots jsonb,    -- [{"label":"Mon 12 May 3pm"}]
slot_sent_at timestamptz, interview_slot timestamptz,
interview_reminder_sent boolean default false,
interview_done boolean default false, interview_done_at timestamptz,
placement_confirmed boolean default false,
notes text,
created_at timestamptz, updated_at timestamptz,
UNIQUE(client_id, candidate_id)
```

**whatsapp_messages**
```
id uuid PK,
client_id uuid FK→clients nullable,
candidate_id uuid FK→candidates nullable,
mapping_id uuid FK→client_candidate_mappings nullable,
message_type text,   -- 'client_reachout'|'client_agreement'|'candidate_bulk'
                     -- |'candidate_slot'|'candidate_reminder'
direction text,      -- 'outbound' | 'inbound'
phone text,
message_text text,
status text default 'pending_review',
                     -- 'pending_review'→'approved'→'sent'→'delivered'→'read'
approved_by text, approved_at timestamptz,
meta_msg_id text, sent_at timestamptz,
created_at timestamptz
```

**pipeline_events** — APPEND-ONLY, never update or delete rows
```
id uuid PK, client_id uuid FK→clients,
from_stage client_stage nullable, to_stage client_stage,
note text, created_by text default 'system',
created_at timestamptz
```
> A DB trigger auto-inserts a row here on every `clients.stage` update.
> After the trigger fires, optionally UPDATE that row to attach a note and real actor.

---

## Backend patterns

### `database.py` — asyncpg pool
```python
import os, asyncpg
from dotenv import load_dotenv
load_dotenv()

_pool: asyncpg.Pool | None = None

async def init_db():
    global _pool
    _pool = await asyncpg.create_pool(dsn=os.environ["DATABASE_URL"], min_size=2, max_size=10)

async def close_db():
    if _pool:
        await _pool.close()

def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("DB pool not initialised")
    return _pool
```

### `main.py` — lifespan + routers
```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from database import init_db, close_db
from routers import clients, candidates, mappings, scraping, whatsapp, pipeline

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield
    await close_db()

app = FastAPI(title="RecruitFlow API", lifespan=lifespan)
app.add_middleware(CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

app.include_router(clients.router)
app.include_router(candidates.router)
app.include_router(mappings.router)
app.include_router(scraping.router)
app.include_router(whatsapp.router)
app.include_router(pipeline.router)
```

### Dependency injection in every router
```python
from fastapi import Depends
import asyncpg
from database import get_pool

async def get_conn():
    async with get_pool().acquire() as conn:
        yield conn

# Usage in endpoint:
async def my_endpoint(conn: asyncpg.Connection = Depends(get_conn)):
    ...
```

### Query patterns — use these everywhere, never supabase-py
```python
# Fetch all (returns list of Record)
rows = await conn.fetch("SELECT * FROM clients WHERE stage = $1", stage)
return [dict(r) for r in rows]

# Fetch one
row = await conn.fetchrow("SELECT * FROM clients WHERE id = $1", id)
if not row:
    raise HTTPException(404, "Client not found")
return dict(row)

# Fetch single scalar
count = await conn.fetchval("SELECT COUNT(*) FROM clients")

# Insert and return
row = await conn.fetchrow(
    "INSERT INTO clients (company_name, stage) VALUES ($1, $2) RETURNING *",
    company_name, "lead"
)
return dict(row)

# Update
await conn.execute(
    "UPDATE clients SET stage=$1, updated_at=now() WHERE id=$2",
    new_stage, client_id
)

# Delete
await conn.execute("DELETE FROM client_candidate_mappings WHERE id=$1", id)

# Always use $1, $2... placeholders — never f-strings or .format() in SQL
```

### Response convention
```python
# All endpoints return:
{"data": {...}, "ok": True}           # single object
{"data": [...], "count": N, "ok": True}  # list
# FastAPI raises HTTPException for errors — it returns {"detail": "..."} automatically
```

### `requirements.txt`
```
fastapi
uvicorn[standard]
asyncpg
python-dotenv
pydantic[email]
httpx
```

---

## API endpoints

### Clients — `routers/clients.py`, prefix `/clients`
```
GET    /                        list, ?stage= filter
GET    /{id}                    single client
POST   /                        create
PATCH  /{id}                    update fields
DELETE /{id}                    delete
PATCH  /{id}/stage              update stage + update pipeline_events note
GET    /{id}/timeline           pipeline_events ordered ASC
GET    /stuck?days=7            clients stuck in stage > N days
```

### Candidates — `routers/candidates.py`, prefix `/candidates` — READ-ONLY
```
GET    /                        list, ?evaluation_status=&industry=&search=
GET    /{id}                    single candidate
```

### Mappings — `routers/mappings.py`, prefix `/clients/{client_id}/mappings`
```
GET    /                        all mapped candidates, JOINed with candidates table
POST   /                        map a candidate {candidate_id, match_score}
DELETE /{id}                    remove mapping
PATCH  /{id}/stage              update stage + set timestamp fields (see table below)
POST   /auto                    call MATCHING_API_URL, return ranked candidates (no DB write)
```

**Stage transition side-effects** (set these timestamp fields when stage changes):
| to_stage | set field |
|---|---|
| wa_sent | wa_sent_at = now() |
| intent_positive / intent_negative | intent_received_at = now() |
| resume_sent | resume_sent_at = now() |
| slot_requested_client | slot_requested_at = now() |
| slot_sent_candidate | slot_sent_at = now(), available_slots from request body |
| interview_scheduled | interview_slot from request body |
| interview_done | interview_done = true, interview_done_at = now() |
| placed | placement_confirmed = true |

### Scraping — `routers/scraping.py`, prefix `/clients/{client_id}/scrape`
```
POST   /{scraper_type}          trigger scraper (job_portal|apollo|google_maps|custom)
                                creates scraping_jobs row, runs in BackgroundTask
                                sets client stage → 'scraping', then → 'scraped' on complete
                                updates poc_phone if result contains it
GET    /                        all scraping_job rows for client (latest per type)
```

### WhatsApp — `routers/whatsapp.py`, prefix `/whatsapp`
```
POST   /send/client-reachout/{client_id}   send template, stage → reachout_sent
POST   /send/client-agreement/{client_id}  send text {message_text}, stage → agreement_sent
POST   /send/candidate-bulk                {mapping_ids[], message_text} → pending_review rows
POST   /approve/{message_id}               send via Meta API, status → sent
GET    /pending                            list status=pending_review messages
GET    /webhook                            Meta verification (return hub.challenge)
POST   /webhook                            inbound messages + status updates (return 200 fast)
```

### Pipeline — `routers/pipeline.py`, prefix `/pipeline`
```
GET    /funnel                  conversion rates per stage
GET    /stuck?days=7            clients stuck > N days
```

---

## WhatsApp Meta Cloud API

### Send template (client reachout)
```python
url = f"https://graph.facebook.com/v19.0/{os.environ['WHATSAPP_PHONE_NUMBER_ID']}/messages"
headers = {"Authorization": f"Bearer {os.environ['WHATSAPP_API_TOKEN']}", "Content-Type": "application/json"}
payload = {
    "messaging_product": "whatsapp", "to": phone, "type": "template",
    "template": {
        "name": os.environ["WHATSAPP_REACHOUT_TEMPLATE_NAME"],
        "language": {"code": os.environ["WHATSAPP_REACHOUT_TEMPLATE_LANG"]}
    }
}
async with httpx.AsyncClient() as http:
    resp = await http.post(url, headers=headers, json=payload)
```

### Send text message
```python
payload = {
    "messaging_product": "whatsapp", "to": phone,
    "type": "text", "text": {"body": message_text}
}
```

### Webhook handler rules
- GET /webhook: check `hub.verify_token` == env var, return `hub.challenge`
- POST /webhook: return 200 immediately, process payload in BackgroundTask
- Status updates: update `whatsapp_messages.status` where `meta_msg_id` matches
- Inbound text: insert new row with `direction='inbound'`

---

## Frontend patterns

### `src/api/axios.ts`
```typescript
import axios from 'axios'
export const api = axios.create({ baseURL: import.meta.env.VITE_API_BASE_URL })
```

### TanStack Query usage
```typescript
// Query
const { data, isLoading } = useQuery({
  queryKey: ['clients', stage],
  queryFn: () => api.get('/clients', { params: { stage } }).then(r => r.data.data)
})

// Mutation with cache invalidation
const mutation = useMutation({
  mutationFn: (body) => api.patch(`/clients/${id}/stage`, body).then(r => r.data),
  onSuccess: () => queryClient.invalidateQueries({ queryKey: ['clients'] })
})
```

### Stage badge colours (Tailwind classes)
```
lead                → bg-gray-100 text-gray-600
scraping            → bg-blue-100 text-blue-700 animate-pulse
scraped             → bg-blue-100 text-blue-700
reachout_sent       → bg-amber-100 text-amber-700
interested          → bg-orange-100 text-orange-700
agreement_sent      → bg-purple-100 text-purple-700
onboarded           → bg-green-100 text-green-700
disqualified        → bg-red-100 text-red-600

matched             → bg-gray-100 text-gray-600
wa_sent             → bg-blue-100 text-blue-700
intent_positive     → bg-green-100 text-green-700
intent_negative     → bg-red-100 text-red-600
resume_sent         → bg-purple-100 text-purple-700
slot_requested_client → bg-amber-100 text-amber-700
slot_sent_candidate → bg-amber-100 text-amber-700
interview_scheduled → bg-teal-100 text-teal-700
interview_done      → bg-gray-100 text-gray-600
placed              → bg-green-200 text-green-800 font-medium
rejected_by_client  → bg-red-100 text-red-600
```

### Scrape button states
```typescript
// Per scraper_type, derive state from scraping_jobs data:
// no row found          → 'idle'    → grey button, clickable
// status === 'running'  → 'running' → spinner, disabled, poll every 3s
// status === 'completed'→ 'done'    → green tick, disabled, show "Last: Xh ago"
// status === 'failed'   → 'failed'  → red button, clickable to retry
```

---

## Business rules

1. **candidates table is read-only** from this portal — no POST/PATCH/DELETE on candidates
2. **WhatsApp candidate messages require human approval** — always create `pending_review` rows first, never send directly to Meta
3. **pipeline_events is append-only** — DB trigger handles inserts; after trigger fires optionally UPDATE that row to add note/actor
4. **Scraping runs in BackgroundTask** — respond immediately with `{job_id, status: "running"}`, timeout httpx calls at 25s
5. **Webhook must return 200 in < 5s** — store payload and return immediately, process async
6. **Stage can only move forward** — validate in PATCH /stage that the new stage is a valid progression (or allow any change for admin flexibility, your call)
7. **Match score is 0–100** — display as a coloured percentage badge: <50 red, 50–74 amber, 75+ green
8. **Bulk WA flow**: POST /send/candidate-bulk creates pending_review rows → user reviews in /whatsapp/pending → POST /approve/{id} sends each one

---

## Do not build

- Candidate create / edit / delete endpoints or forms
- Ringg AI integration or voice call triggers
- ~~Automated WhatsApp funnels (the WA funnel runs in a separate system)~~ — outdated: the WA funnel now runs in this codebase (see `services/flow_engine.py`, `routers/whatsapp.py`), with automated sends on both candidate and client sides and no human-approval step outside the explicit `pending_review` bulk-WA flow (rule 8 above)
- Evaluation report generation
- Any direct Supabase client usage — all DB access is via asyncpg pool
