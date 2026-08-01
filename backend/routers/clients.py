import uuid
import os
import re
import secrets
from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
import asyncpg
import httpx
from database import get_conn
from models.client import ClientCreate, ClientUpdate, StageUpdateRequest, _normalize_job_title
from pydantic import BaseModel
from typing import Optional, List
import constants.whatsapp_templates as WT


async def geocode(address: str) -> tuple[float | None, float | None, str | None, str | None]:
    key = os.environ.get("GOOGLE_MAPS_API_KEY", "")
    if not key:
        return None, None, None, None
    lat = lng = None
    try:
        async with httpx.AsyncClient(timeout=8.0) as http:
            # Places Text Search respects the full address string as a named-place
            # query, so landmark hints like "Nr. Kamod Circle" don't override the
            # primary place name the way Geocoding API partial-matching does.
            resp = await http.get(
                "https://maps.googleapis.com/maps/api/place/textsearch/json",
                params={"query": address, "key": key, "region": "in"},
            )
            results = resp.json().get("results", [])
            if results:
                loc = results[0]["geometry"]["location"]
                lat, lng = loc["lat"], loc["lng"]
    except Exception:
        pass
    if lat is None or lng is None:
        return None, None, None, None

    # Places Text Search results don't include address_components (only the
    # Geocoding API does) — one extra reverse-geocode call on the coordinates
    # we already have gets us structured city/state without touching the
    # landmark-aware lat/lng lookup above.
    from services.city_service import reverse_geocode
    city, state = await reverse_geocode(lat, lng)
    return lat, lng, city, state

class BulkScrapeRequest(BaseModel):
    client_ids: list[str]
    scraper_type: str


class BulkAIVoiceReachoutRequest(BaseModel):
    client_ids: list[str]


class BulkFunnelStuckReachoutRequest(BaseModel):
    client_ids: list[str]


# client_stage label sent to the "Funnel Stuck" Ringg agent, per its intro_message template
_FUNNEL_STUCK_STAGE_LABELS = {
    "interested": "Job Requirement Form",
    "agreement_sent": "Agreement Signing",
}


_SOURCE_TO_PORTAL_NAME = {
    "naukri": "Naukri",
    "indeed": "Indeed",
    "apna": "Apna",
    "workindia": "WorkIndia",
}

class RecruiterSlotsRequest(BaseModel):
    slots: List[dict]


class ParseJDRequest(BaseModel):
    jd_text: str


class AgreementFieldOverrides(BaseModel):
    client_name: Optional[str] = None
    poc_name: Optional[str] = None
    job_title: Optional[str] = None
    job_location: Optional[str] = None
    min_salary: Optional[float] = None
    max_salary: Optional[float] = None
    salary_deductions: Optional[str] = None
    key_skills: Optional[str] = None
    job_timings: Optional[str] = None
    tools_requirements: Optional[str] = None
    other_details: Optional[str] = None
    age_requirements: Optional[str] = None
    gender_requirements: Optional[str] = None
    fee_amount: Optional[str] = None
    payment_terms: Optional[str] = None
    replacement_period: Optional[str] = None
    payment_due_days: Optional[int] = None
    backdoor_period: Optional[str] = None

router = APIRouter(prefix="/clients", tags=["clients"])

_JSONB_LIST_FIELDS = {"phone_numbers", "available_slots", "recruiter_slots", "key_skills"}
_JSONB_DICT_FIELDS = {"gst_tds", "extracted_info"}

def _normalize(row) -> dict:
    """Parse any jsonb fields that got double-encoded as JSON strings."""
    import json as _j
    d = dict(row)
    for f in _JSONB_LIST_FIELDS | _JSONB_DICT_FIELDS:
        if isinstance(d.get(f), str):
            try:
                d[f] = _j.loads(d[f])
                if isinstance(d[f], str):  # double-encoded
                    d[f] = _j.loads(d[f])
            except Exception:
                d[f] = [] if f in _JSONB_LIST_FIELDS else None
    return d


async def _generate_and_send_agreement(client_id: str, raise_on_error: bool = False) -> str | None:
    """Generate agreement PDF, save URL, send frontend agreement page link via WhatsApp.

    Used both as a fire-and-forget background task (raise_on_error=False, errors are
    logged and swallowed) and as the core of the manual "Send Agreement" endpoint
    (raise_on_error=True, errors propagate so the caller can surface them).

    Returns the agreement_token once it has been generated/persisted (even if the
    WhatsApp send itself later fails), or None if the client/phone lookup failed."""
    import json as _json
    from database import get_pool
    from services import agreement_service as ag
    import services.msg91_service as msg91

    pool = get_pool()
    async with pool.acquire() as conn:
        client = await conn.fetchrow("SELECT * FROM clients WHERE id = $1", uuid.UUID(client_id))
        if not client:
            if raise_on_error:
                raise ValueError("Client not found")
            return
        if not client["poc_phone"]:
            if raise_on_error:
                raise ValueError("Client has no POC phone number")
            return

        client_dict = dict(client)

        # Fetch any linked secondary positions
        linked_rows = await conn.fetch(
            "SELECT * FROM clients WHERE primary_client_id = $1 ORDER BY created_at ASC",
            client["id"],
        )

        # Build positions list for multi-position agreements
        def _row_to_position(row) -> dict:
            skills = row.get("key_skills") or []
            if isinstance(skills, (list, tuple)):
                skills = ", ".join(skills)
            return {
                "job_title":          row.get("job_title", ""),
                "open_positions":     row.get("open_positions") or 1,
                "min_salary":         row.get("min_salary"),
                "max_salary":         row.get("max_salary"),
                "fee_amount":         row.get("fee_amount") or 7000,
                "key_skills":         skills,
                "job_timings":        row.get("job_timings", ""),
                "tools_requirements": row.get("tools_requirements"),
                "other_details":      row.get("other_details"),
                "age_requirements":   row.get("age_requirements"),
                "gender_requirements":row.get("gender_requirements"),
                "salary_deductions":  row.get("salary_deductions"),
            }

        positions = [_row_to_position(client_dict)]
        for lr in linked_rows:
            positions.append(_row_to_position(dict(lr)))

        # Generate agreement PDF (for reference download)
        fields = ag.client_to_fields(client_dict)
        html = ag.build_agreement_html(fields, positions=positions)
        safe_name = ag._safe(fields["client_name"])
        # Use client_id in path so re-uploads always overwrite the same blob
        pdf_blob  = f"agreements/{client_id}/{safe_name}.pdf"
        html_blob = f"agreements/{client_id}/{safe_name}.html"
        pdf_url = None
        try:
            pdf = ag.generate_pdf(html)
            pdf_url = ag.upload_to_gcp(pdf, fields["client_name"], ext="pdf", blob_path=pdf_blob)
        except Exception as pdf_err:
            print(f"[agreement] PDF generation failed for {client_id}: {pdf_err} — falling back to HTML")
            try:
                pdf_url = ag.upload_to_gcp(html.encode(), fields["client_name"], ext="html", blob_path=html_blob)
            except Exception as html_err:
                print(f"[agreement] HTML upload also failed for {client_id}: {html_err}")

        # Generate or reuse agreement token for the frontend page
        token = client_dict.get("agreement_token")
        if not token:
            token = secrets.token_urlsafe(32)
            await conn.execute(
                "UPDATE clients SET agreement_token = $1, updated_at = now() WHERE id = $2",
                token, client["id"],
            )

        if pdf_url:
            await conn.execute(
                "UPDATE clients SET agreement_url = $1, updated_at = now() WHERE id = $2",
                pdf_url, client["id"],
            )

        # Build the frontend agreement page URL (this is what we send to the client)
        frontend_url = os.environ.get("FRONTEND_URL", "http://localhost:5174").rstrip("/")
        agreement_page_url = f"{frontend_url}/agreement/{token}"

        # Send WhatsApp template
        template = WT.CLIENT_AGREEMENT
        poc_name = client["poc_name"] or client["company_name"] or "there"
        components = [
            {
                "type": "body",
                "parameters": [
                    {"type": "text", "text": poc_name},
                    {"type": "text", "text": agreement_page_url},
                ],
            }
           
        ]
        try:
            result = await msg91.send_template(client["poc_phone"], template, "en", components, sender="client")
        except Exception as e:
            print(f"[agreement] WhatsApp send failed for {client_id}: {e}")
            if raise_on_error:
                raise
            return token

        from routers.whatsapp import _extract_msg_id
        meta_msg_id = _extract_msg_id(result) if isinstance(result, dict) else None

        await conn.execute(
            """
            INSERT INTO whatsapp_messages
                (client_id, message_type, direction, phone, status, meta_msg_id, sent_at)
            VALUES ($1, 'client_agreement', 'outbound', $2, 'sent', $3, now())
            """,
            client["id"], client["poc_phone"], meta_msg_id,
        )
        await conn.execute(
            """UPDATE clients SET stage = 'agreement_sent'::client_stage,
               agreement_sent_at = COALESCE(agreement_sent_at, now()), updated_at = now() WHERE id = $1""",
            client["id"],
        )
        # Also advance any linked secondary positions to agreement_sent
        await conn.execute(
            """UPDATE clients SET stage = 'agreement_sent'::client_stage,
               agreement_sent_at = COALESCE(agreement_sent_at, now()), updated_at = now() WHERE primary_client_id = $1""",
            client["id"],
        )

        return token


# Client fields that feed into the agreement PDF — if any of these change on a
# client that already has an agreement, the PDF is regenerated in place.
AGREEMENT_FIELDS = {
    "company_name", "poc_name", "job_title", "job_location", "location",
    "min_salary", "max_salary", "salary_deductions", "key_skills", "job_timings",
    "tools_requirements", "other_details", "age_requirements", "gender_requirements",
    "fee_amount", "payment_terms", "replacement_period", "payment_due_days",
    "backdoor_period", "open_positions",
}


async def _regenerate_agreement_pdf(client_id: str) -> None:
    """Background task: re-render the agreement PDF in place at its existing URL.
    No-op if the client (and any linked positions) never had an agreement generated."""
    from database import get_pool
    from services import agreement_service as ag

    def _row_to_position(row) -> dict:
        skills = row.get("key_skills") or []
        if isinstance(skills, (list, tuple)):
            skills = ", ".join(skills)
        return {
            "job_title":          row.get("job_title", ""),
            "open_positions":     row.get("open_positions") or 1,
            "min_salary":         row.get("min_salary"),
            "max_salary":         row.get("max_salary"),
            "fee_amount":         row.get("fee_amount") or 7000,
            "key_skills":         skills,
            "job_timings":        row.get("job_timings", ""),
            "tools_requirements": row.get("tools_requirements"),
            "other_details":      row.get("other_details"),
            "age_requirements":   row.get("age_requirements"),
            "gender_requirements":row.get("gender_requirements"),
            "salary_deductions":  row.get("salary_deductions"),
        }

    pool = get_pool()
    async with pool.acquire() as conn:
        client = await conn.fetchrow("SELECT * FROM clients WHERE id = $1", uuid.UUID(client_id))
        if not client:
            return
        client_dict = dict(client)

        # Multi-position agreements live on the primary row — use it as the base
        # for re-rendering, but keep the edited client's own agreement_url (they
        # all point at the same PDF) to know where to re-upload.
        agreement_url = client_dict.get("agreement_url")
        base = client_dict
        if client_dict.get("primary_client_id"):
            primary = await conn.fetchrow("SELECT * FROM clients WHERE id = $1", client_dict["primary_client_id"])
            if primary:
                base = dict(primary)
                agreement_url = agreement_url or base.get("agreement_url")

        if not agreement_url:
            return  # no agreement generated yet — nothing to update

        linked_rows = await conn.fetch(
            "SELECT * FROM clients WHERE primary_client_id = $1 ORDER BY created_at ASC",
            base["id"],
        )

        positions = [_row_to_position(base)]
        for lr in linked_rows:
            positions.append(_row_to_position(dict(lr)))

        fields = ag.client_to_fields(base)
        html = ag.build_agreement_html(fields, positions=positions)

        bucket_name = os.environ.get("GCP_AGREEMENT_BUCKET", "hirelyra-clients")
        blob_path = agreement_url.split(f"/{bucket_name}/", 1)[-1]

        try:
            pdf = ag.generate_pdf(html)
            ag.upload_to_gcp(pdf, fields["client_name"], ext="pdf", blob_path=blob_path)
        except Exception as e:
            print(f"[agreement] regeneration failed for {client_id}: {e}")


async def _classify_office_type(client_id: str, location: str) -> None:
    """Background task: call OpenAI to classify job location as Corporate or Industrial.
    Only runs if office_type is not already set."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key or not location:
        return

    from database import get_pool

    pool = get_pool()
    async with pool.acquire() as conn:
        existing = await conn.fetchval(
            "SELECT office_type FROM clients WHERE id = $1",
            uuid.UUID(client_id),
        )
    if existing:
        return  # already classified, don't overwrite

    prompt = (
        "You are a location classifier for businesses in India. "
        "Given a job location address or area name, classify it into exactly one of these two categories:\n\n"
        "- Corporate: offices, business parks, IT parks, commercial complexes, shops, showrooms, "
        "markets, retail outlets, traditional business districts, schools, hospitals — anywhere that is NOT a factory or manufacturing unit\n"
        "- Industrial: GIDC, industrial estates, factories, manufacturing zones, warehouses, "
        "production units, industrial parks\n\n"
        "Respond with ONLY the category name — nothing else, no punctuation.\n\n"
        f"Location: {location}"
    )

    VALID = {"Corporate", "Industrial"}
    try:
        async with httpx.AsyncClient(timeout=15.0) as http:
            resp = await http.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "gpt-4o-mini",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 10,
                    "temperature": 0,
                },
            )

        if not resp.is_success:
            print(f"[classify_office] OpenAI error {resp.status_code} for {client_id}: {resp.text}")
            return

        content = resp.json()["choices"][0]["message"]["content"].strip()
        if content not in VALID:
            print(f"[classify_office] Unexpected response '{content}' for location '{location}'")
            return

        pool = get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE clients SET office_type = $1, updated_at = now() WHERE id = $2",
                content, uuid.UUID(client_id),
            )
        print(f"[classify_office] {client_id} → '{content}'")

    except Exception as e:
        print(f"[classify_office] Failed for {client_id}: {e}")


@router.post("/parse-jd")
async def parse_jd(body: ParseJDRequest):
    """Parse a raw job description using OpenAI and return pre-filled form fields."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise HTTPException(400, "OPENAI_API_KEY not configured")

    prompt = (
        "You are a job description parser for an Indian accounting recruitment platform.\n"
        "Extract fields from the job description and return ONLY valid JSON. Use null for any field not found.\n\n"
        "How to decide job_title (this platform only hires for these two roles — classify strictly by responsibilities, not by the title text in the JD):\n"
        '- "Senior Accountant": responsible for GST return filing (GSTR-1/3B/9), TDS deduction/challan payment/return filing (26Q/24Q), '
        "P&L and Balance Sheet preparation, bank reconciliation with ledger scrutiny, coordinating with a CA/auditor for statutory audit, "
        "or payroll processing and labour compliance (PF/ESIC). Expected to work independently with minimal supervision. "
        "Typical budget is ₹20,000+/month.\n"
        '- "Junior Accountant": limited to day-to-day data entry (purchase/sales/expense vouchers), bank reconciliation and cash book maintenance, '
        "petty cash and vendor payment tracking, basic MIS/Excel reporting, filing/document support, or general admin/office coordination. "
        "Tally proficiency expected but NOT GST or TDS return filing — this role supports a Senior Accountant. "
        "Typical budget is ₹12,000–25,000/month.\n"
        "- If the JD explicitly asks for GST/TDS filing, statutory audit, or payroll compliance, it is Senior — even if titled \"accountant\" generically or the budget looks low.\n"
        "- If the JD is purely data entry / bookkeeping / admin support with no filing responsibility, it is Junior — even if titled \"senior\" or \"accounts executive\".\n"
        "- If responsibilities are ambiguous, mixed, or the JD doesn't clearly match either profile, return null rather than guessing.\n\n"
        "Return this exact JSON:\n"
        '{\n'
        '  "company_name": <company/firm name or null>,\n'
        '  "poc_name": <hiring manager or contact person name or null>,\n'
        '  "industry": one of ["Manufacturing","Trading / Distribution","Retail","Construction / Real Estate","Healthcare / Pharmaceuticals","IT / Technology","Education / Training","Logistics / Transportation","Financial Services","Hospitality","Food & Beverage / FMCG","Agriculture","Export / Import","CA Firm / Tax Consultancy"] or null,\n'
        '  "num_employees": <integer or null>,\n'
        '  "job_title": "Senior Accountant" or "Junior Accountant" or null — classified per the rules above,\n'
        '  "open_positions": <integer or null>,\n'
        '  "min_salary": <monthly in-hand number; if annual CTC divide by 12; or null>,\n'
        '  "max_salary": <monthly in-hand number or null>,\n'
        '  "key_skills": ["skill1", "skill2"],\n'
        '  "shift_timing": one of ["9am – 6pm","10am – 7pm","10am – 6pm","9am – 7pm","Night Shift","Rotational","Flexible"] or null,\n'
        '  "working_days": one of ["Mon – Sat","Mon – Fri","Mon – Sun"] or a short custom string or null,\n'
        '  "gender_requirements": "Male" or "Female" or "Any" or null,\n'
        '  "job_type": "Full Time" or "Part Time" or null,\n'
        '  "tools_requirements": comma-separated subset of ["Tally Prime","Tally ERP 9","ZohoBooks","QuickBooks","MS Excel","Busy Accounting","Marg ERP","SAP","Oracle Financials","FreshBooks","Xero","Miracle Accounting","Wings Accounting"] or null,\n'
        '  "other_details": <a plain text string summarising any remaining requirements NOT covered by other fields — e.g. "Local candidates only. No WFH." — MUST be a string not an object, or null>\n'
        "}\n\n"
        f"Job description:\n{body.jd_text}"
    )

    try:
        async with httpx.AsyncClient(timeout=20.0) as http:
            resp = await http.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": "gpt-5.4-mini",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_completion_tokens": 500,
                    "temperature": 0,
                    "response_format": {"type": "json_object"},
                },
            )
        if not resp.is_success:
            raise HTTPException(502, "AI service error")
        import json as _json
        content = resp.json()["choices"][0]["message"]["content"]
        parsed = _json.loads(content)
        return {"data": parsed, "ok": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Parse failed: {e}")


# /stuck must be defined before /{client_id} to avoid route shadowing
@router.post("/bulk-scrape")
async def bulk_scrape(
    body: BulkScrapeRequest,
    background_tasks: BackgroundTasks,
    conn: asyncpg.Connection = Depends(get_conn),
):
    from routers.scraping import VALID_SCRAPER_TYPES, SCRAPER_ENV_KEYS, _run_scraper
    from fastapi.encoders import jsonable_encoder
    import os

    if body.scraper_type not in VALID_SCRAPER_TYPES:
        raise HTTPException(400, f"Invalid scraper type")

    results = []
    for cid_str in body.client_ids:
        try:
            cid = uuid.UUID(cid_str)
            client = await conn.fetchrow("SELECT * FROM clients WHERE id = $1", cid)
            if not client:
                results.append({"client_id": cid_str, "ok": False, "error": "Not found"})
                continue
            job = await conn.fetchrow(
                "INSERT INTO scraping_jobs (client_id, scraper_type, status, triggered_at) VALUES ($1,$2,'running',now()) RETURNING *",
                cid, body.scraper_type,
            )
            await conn.execute(
                "UPDATE clients SET stage='scraping'::client_stage, updated_at=now() WHERE id=$1", cid,
            )
            background_tasks.add_task(
                _run_scraper, str(job["id"]), str(cid), body.scraper_type, jsonable_encoder(dict(client)),
            )
            results.append({"client_id": cid_str, "job_id": str(job["id"]), "ok": True})
        except Exception as e:
            results.append({"client_id": cid_str, "ok": False, "error": str(e)})
    return {"data": results, "count": len(results), "ok": True}


async def _run_client_ai_call(call_id: str, client_id: str, phone: str, company_name: str, job_portal_name: str) -> None:
    """Background task: place the RinggAI outbound call and record the result."""
    from database import get_pool
    from services.ringg_service import trigger_client_ai_call

    pool = get_pool()
    async with pool.acquire() as conn:
        try:
            resp = await trigger_client_ai_call(client_id, phone, company_name, job_portal_name)
            ringg_call_id = str(resp.get("call_id") or resp.get("id") or "") or None
            await conn.execute(
                "UPDATE client_ai_calls SET status='calling', ringg_call_id=$1 WHERE id=$2",
                ringg_call_id, call_id,
            )
        except Exception as e:
            print(f"[ai-reachout] trigger failed for client {client_id}: {e}")
            await conn.execute(
                "UPDATE client_ai_calls SET status='failed', classification_reason=$1 WHERE id=$2",
                str(e)[:500], call_id,
            )


@router.post("/bulk-ai-voice-reachout")
async def bulk_ai_voice_reachout(
    body: BulkAIVoiceReachoutRequest,
    background_tasks: BackgroundTasks,
    conn: asyncpg.Connection = Depends(get_conn),
):
    """Schedule an outbound RinggAI voice reachout call for each selected client."""
    from routers.whatsapp import _phones_for_client

    results = []
    for cid_str in body.client_ids:
        try:
            cid = uuid.UUID(cid_str)
            client = await conn.fetchrow("SELECT * FROM clients WHERE id = $1", cid)
            if not client:
                results.append({"client_id": cid_str, "ok": False, "error": "Not found"})
                continue
            if client["is_dnd"]:
                results.append({"client_id": cid_str, "ok": False, "error": "Client is DND"})
                continue
            phones = _phones_for_client(client)
            if not phones:
                results.append({"client_id": cid_str, "ok": False, "error": "No phone number on file"})
                continue
            phone = phones[0]
            job_portal_name = _SOURCE_TO_PORTAL_NAME.get((client["source"] or "").lower(), "the job portal")

            call_row = await conn.fetchrow(
                "INSERT INTO client_ai_calls (client_id, phone, status) VALUES ($1, $2, 'queued') RETURNING *",
                cid, phone,
            )
            background_tasks.add_task(
                _run_client_ai_call, str(call_row["id"]), str(cid), phone,
                client["company_name"] or "", job_portal_name,
            )
            results.append({"client_id": cid_str, "call_id": str(call_row["id"]), "phone": phone, "ok": True})
        except Exception as e:
            results.append({"client_id": cid_str, "ok": False, "error": str(e)})
    return {"data": results, "count": len(results), "ok": True}


async def _run_funnel_stuck_call(call_id: str, client_id: str, phone: str, callee_name: str, client_stage_label: str) -> None:
    """Background task: place the RinggAI "Funnel Stuck" outbound call and record the result."""
    from database import get_pool
    from services.ringg_service import trigger_funnel_stuck_call

    pool = get_pool()
    async with pool.acquire() as conn:
        try:
            resp = await trigger_funnel_stuck_call(client_id, phone, callee_name, client_stage_label)
            ringg_call_id = str(resp.get("call_id") or resp.get("id") or "") or None
            await conn.execute(
                "UPDATE client_ai_calls SET status='calling', ringg_call_id=$1 WHERE id=$2",
                ringg_call_id, call_id,
            )
        except Exception as e:
            print(f"[funnel-stuck] trigger failed for client {client_id}: {e}")
            await conn.execute(
                "UPDATE client_ai_calls SET status='failed', classification_reason=$1 WHERE id=$2",
                str(e)[:500], call_id,
            )


@router.post("/bulk-funnel-stuck-reachout")
async def bulk_funnel_stuck_reachout(
    body: BulkFunnelStuckReachoutRequest,
    background_tasks: BackgroundTasks,
    conn: asyncpg.Connection = Depends(get_conn),
):
    """Schedule an outbound RinggAI 'Funnel Stuck' call for each selected client.

    Only clients in 'interested' or 'agreement_sent' stage are eligible.
    """
    from routers.whatsapp import _phones_for_client

    results = []
    for cid_str in body.client_ids:
        try:
            cid = uuid.UUID(cid_str)
            client = await conn.fetchrow("SELECT * FROM clients WHERE id = $1", cid)
            if not client:
                results.append({"client_id": cid_str, "ok": False, "error": "Not found"})
                continue
            stage_label = _FUNNEL_STUCK_STAGE_LABELS.get(client["stage"])
            if not stage_label:
                results.append({"client_id": cid_str, "ok": False, "error": "Client is not in 'interested' or 'agreement_sent' stage"})
                continue
            if client["is_dnd"]:
                results.append({"client_id": cid_str, "ok": False, "error": "Client is DND"})
                continue
            phones = _phones_for_client(client)
            if not phones:
                results.append({"client_id": cid_str, "ok": False, "error": "No phone number on file"})
                continue
            phone = phones[0]
            callee_name = client["poc_name"] or client["company_name"] or ""

            call_row = await conn.fetchrow(
                "INSERT INTO client_ai_calls (client_id, phone, status, call_type) VALUES ($1, $2, 'queued', 'funnel_stuck') RETURNING *",
                cid, phone,
            )
            background_tasks.add_task(
                _run_funnel_stuck_call, str(call_row["id"]), str(cid), phone,
                callee_name, stage_label,
            )
            results.append({"client_id": cid_str, "call_id": str(call_row["id"]), "phone": phone, "ok": True})
        except Exception as e:
            results.append({"client_id": cid_str, "ok": False, "error": str(e)})
    return {"data": results, "count": len(results), "ok": True}


@router.get("/funnel-stuck-reachout")
async def list_funnel_stuck_reachout(
    conn: asyncpg.Connection = Depends(get_conn),
):
    rows = await conn.fetch(
        """
        SELECT * FROM (
            SELECT DISTINCT ON (ac.client_id)
                ac.client_id, ac.id AS call_id, ac.status, ac.classification,
                ac.classification_reason, ac.extracted_info, ac.recording_url,
                ac.triggered_at, ac.completed_at, ac.phone,
                c.company_name, c.stage, c.poc_name, c.poc_phone, c.is_dnd
            FROM client_ai_calls ac
            JOIN clients c ON c.id = ac.client_id
            WHERE ac.call_type = 'funnel_stuck'
            ORDER BY ac.client_id, ac.triggered_at DESC
        ) t
        ORDER BY triggered_at DESC
        """
    )
    return {"data": [_normalize(r) for r in rows], "count": len(rows), "ok": True}


@router.get("/stuck")
async def get_stuck_clients(
    days: int = Query(7),
    conn: asyncpg.Connection = Depends(get_conn),
):
    rows = await conn.fetch(
        """
        SELECT c.* FROM clients c
        WHERE c.stage NOT IN ('onboarded', 'disqualified', 'churned')
        AND COALESCE(
            (SELECT MAX(pe.created_at) FROM pipeline_events pe WHERE pe.client_id = c.id),
            c.created_at
        ) < now() - make_interval(days => $1)
        ORDER BY c.updated_at ASC
        """,
        days,
    )
    return {"data": [_normalize(r) for r in rows], "count": len(rows), "ok": True}


@router.get("/ai-voice-reachout")
async def list_ai_voice_reachout(
    conn: asyncpg.Connection = Depends(get_conn),
):
    rows = await conn.fetch(
        """
        SELECT * FROM (
            SELECT DISTINCT ON (ac.client_id)
                ac.client_id, ac.id AS call_id, ac.status, ac.classification,
                ac.classification_reason, ac.extracted_info, ac.recording_url,
                ac.triggered_at, ac.completed_at, ac.phone,
                c.company_name, c.stage, c.poc_name, c.poc_phone, c.is_dnd
            FROM client_ai_calls ac
            JOIN clients c ON c.id = ac.client_id
            WHERE ac.call_type = 'reachout'
            ORDER BY ac.client_id, ac.triggered_at DESC
        ) t
        ORDER BY triggered_at DESC
        """
    )
    return {"data": [_normalize(r) for r in rows], "count": len(rows), "ok": True}


@router.get("")
async def list_clients(
    stage: str = None,
    search: str = None,
    segment: str = None,
    is_recruiter: bool = None,
    page: int = 1,
    page_size: int = 30,
    conn: asyncpg.Connection = Depends(get_conn),
):
    conditions = []
    params = []

    if stage:
        params.append(stage)
        conditions.append(f"stage = ${len(params)}::client_stage")

    if segment:
        params.append(segment)
        conditions.append(f"segment = ${len(params)}")

    if is_recruiter is not None:
        params.append(is_recruiter)
        conditions.append(f"is_recruiter = ${len(params)}")

    if search:
        params.append(f"%{search}%")
        idx = len(params)
        conditions.append(
            f"(company_name ILIKE ${idx} OR poc_name ILIKE ${idx} "
            f"OR job_title ILIKE ${idx} OR location ILIKE ${idx} "
            f"OR job_location ILIKE ${idx} OR poc_phone ILIKE ${idx})"
        )

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    total = await conn.fetchval(f"SELECT COUNT(*) FROM clients {where}", *params)
    offset = (page - 1) * page_size
    params.extend([page_size, offset])
    rows = await conn.fetch(
        f"SELECT * FROM clients {where} ORDER BY created_at DESC LIMIT ${len(params)-1} OFFSET ${len(params)}",
        *params,
    )
    return {"data": [_normalize(r) for r in rows], "count": len(rows), "total": total, "ok": True}


@router.post("/ca-leads-import")
async def ca_leads_import(
    body: dict,
    conn: asyncpg.Connection = Depends(get_conn),
):
    """
    Bulk import CA network leads from comma-separated text.
    Each line: source_id, poc_name, phone
    Inserts as source=ca_network, segment=ca_network, stage=scraped.
    Skips duplicates by source_job_id.
    """
    from pydantic import BaseModel as _BM
    raw: str = body.get("csv", "")
    inserted = skipped = errors = 0
    error_lines = []

    for i, line in enumerate(raw.strip().splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3:
            errors += 1
            error_lines.append({"line": i, "text": line, "error": "Need 3 columns: source_id, poc_name, phone"})
            continue

        source_id, poc_name, phone = parts[0], parts[1], parts[2]
        phone = re.sub(r'\D', '', phone)
        if phone.startswith('91') and len(phone) == 12:
            phone = phone[2:]
        if len(phone) != 10:
            errors += 1
            error_lines.append({"line": i, "text": line, "error": f"Invalid phone: {parts[2]}"})
            continue

        existing = await conn.fetchval(
            "SELECT id FROM clients WHERE source_job_id = $1", f"ca_{source_id}"
        )
        if existing:
            skipped += 1
            continue

        await conn.execute(
            """
            INSERT INTO clients
                (company_name, poc_name, poc_phone, source, segment, stage,
                 source_job_id, enrich_status, created_at, updated_at)
            VALUES ($1, $2, $3, 'ca_network', 'ca_network', 'scraped',
                    $4, 'enriched', now(), now())
            """,
            poc_name, poc_name, phone, f"ca_{source_id}",
        )
        inserted += 1

    return {"data": {"inserted": inserted, "skipped": skipped, "errors": errors, "error_lines": error_lines}, "ok": True}


@router.get("/onboard/check")
async def check_existing_client(
    phone: str = Query(...),
    conn: asyncpg.Connection = Depends(get_conn),
):
    """Pre-submit check: is there already an active deal for this phone number?"""
    import re as _re
    digits = _re.sub(r'\D', '', phone)
    if len(digits) == 12 and digits.startswith('91'):
        digits = digits[2:]
    phone_10 = digits if len(digits) == 10 else phone.strip()

    row = await conn.fetchrow(
        """
        SELECT id, company_name, stage::text AS stage
        FROM clients
        WHERE stage = 'onboarded'::client_stage
          AND (
            poc_phone = $1
            OR agreed_phone = $1
            OR EXISTS (
                SELECT 1 FROM jsonb_array_elements(
                    CASE WHEN jsonb_typeof(COALESCE(phone_numbers,'[]'::jsonb)) = 'array'
                         THEN COALESCE(phone_numbers,'[]'::jsonb)
                         ELSE '[]'::jsonb END
                ) p WHERE p->>'number' = $1
            )
          )
        ORDER BY created_at DESC LIMIT 1
        """,
        phone_10,
    )
    if row:
        return {"data": {"exists": True, "stage": row["stage"], "company_name": row["company_name"]}, "ok": True}
    return {"data": {"exists": False}, "ok": True}


class ReEnquiryBody(BaseModel):
    phone: str


@router.post("/onboard/re-enquiry", status_code=200)
async def record_re_enquiry(body: ReEnquiryBody, conn: asyncpg.Connection = Depends(get_conn)):
    """Called when a client tries to submit the onboarding form but already has an active deal."""
    import re as _re
    digits = _re.sub(r'\D', '', body.phone)
    if len(digits) == 12 and digits.startswith('91'):
        digits = digits[2:]
    phone_10 = digits if len(digits) == 10 else body.phone.strip()

    await conn.execute(
        "ALTER TABLE clients ADD COLUMN IF NOT EXISTS re_enquiry_at timestamptz"
    )

    result = await conn.execute(
        """
        UPDATE clients SET re_enquiry_at = now(), updated_at = now()
        WHERE stage = 'onboarded'::client_stage
          AND (
            poc_phone = $1
            OR agreed_phone = $1
            OR EXISTS (
                SELECT 1 FROM jsonb_array_elements(
                    CASE WHEN jsonb_typeof(COALESCE(phone_numbers,'[]'::jsonb)) = 'array'
                         THEN COALESCE(phone_numbers,'[]'::jsonb)
                         ELSE '[]'::jsonb END
                ) p WHERE p->>'number' = $1
            )
          )
        """,
        phone_10,
    )
    return {"data": {"recorded": result != "UPDATE 0"}, "ok": True}


@router.post("/onboard", status_code=200)
async def onboard_client(
    body: ClientCreate,
    background_tasks: BackgroundTasks,
    ref: Optional[str] = Query(None),
    force_create: bool = Query(False),
    conn: asyncpg.Connection = Depends(get_conn),
):
    """Upsert: if ref=client_id is given update that record directly; otherwise match by phone."""
    import json as _json
    import re as _re

    d = body.model_dump()

    # Normalise phone to 10-digit for lookup
    raw_phone = (d.get("poc_phone") or "").strip()
    digits = _re.sub(r'\D', '', raw_phone)
    if len(digits) == 12 and digits.startswith('91'):
        digits = digits[2:]
    phone_10 = digits if len(digits) == 10 else raw_phone

    if d.get("job_location") and not d.get("location_lat"):
        d["location_lat"], d["location_lng"], d["city"], d["state"] = await geocode(d["job_location"])

    # 1. Direct lookup by ref (client_id passed in the onboarding link)
    existing = None
    if force_create:
        existing = None  # always create a new record for additional jobs
    elif ref:
        try:
            existing = await conn.fetchrow(
                "SELECT * FROM clients WHERE id = $1", uuid.UUID(ref)
            )
        except Exception:
            existing = None

    # 2. Fallback: match by phone (skip if force_create — caller wants a fresh row)
    if not force_create and not existing and phone_10:
        try:
            existing = await conn.fetchrow(
                """
                SELECT * FROM clients
                WHERE poc_phone = $1
                   OR agreed_phone = $1
                   OR EXISTS (
                       SELECT 1
                       FROM jsonb_array_elements(
                           CASE WHEN jsonb_typeof(COALESCE(phone_numbers,'[]'::jsonb)) = 'array'
                                THEN COALESCE(phone_numbers,'[]'::jsonb)
                                ELSE '[]'::jsonb END
                       ) p
                       WHERE p->>'number' = $1
                   )
                ORDER BY created_at DESC LIMIT 1
                """,
                phone_10,
            )
        except Exception:
            existing = None

    if existing:
        # Update the existing client, advance stage only if still early in funnel
        current_stage = existing["stage"]
        EARLY_STAGES = ("lead", "scraping", "scraped", "reachout_sent")
        new_stage = "interested" if current_stage in EARLY_STAGES else current_stage

        row = await conn.fetchrow(
            """
            UPDATE clients SET
                company_name       = COALESCE($1, company_name),
                poc_name           = COALESCE($2, poc_name),
                poc_phone          = COALESCE($3, poc_phone),
                job_title          = COALESCE($4, job_title),
                job_location       = COALESCE($5, job_location),
                location           = COALESCE($6, location),
                location_lat       = COALESCE($7, location_lat),
                location_lng       = COALESCE($8, location_lng),
                industry           = COALESCE($9, industry),
                business_type      = COALESCE($10, business_type),
                num_employees      = COALESCE($11, num_employees),
                female_employee_pct= COALESCE($12, female_employee_pct),
                diwali_bonus       = COALESCE($13, diwali_bonus),
                min_salary         = COALESCE($14, min_salary),
                max_salary         = COALESCE($15, max_salary),
                key_skills         = COALESCE($16, key_skills),
                job_timings        = COALESCE($17, job_timings),
                gender_requirements= COALESCE($18, gender_requirements),
                tools_requirements = COALESCE($19, tools_requirements),
                job_type           = 'Full Time',
                other_details      = COALESCE($20, other_details),
                is_ca_firm         = COALESCE($22, is_ca_firm),
                district           = COALESCE($24, district),
                paid_leaves        = COALESCE($25, paid_leaves),
                city               = COALESCE($26, city),
                state              = COALESCE($27, state),
                stage              = $21::client_stage,
                updated_at         = now()
            WHERE id = $23
            RETURNING *
            """,
            d["company_name"], d["poc_name"], phone_10 or None,
            d["job_title"], d["job_location"], d["location"],
            d["location_lat"], d["location_lng"],
            d["industry"], d["business_type"],
            d["num_employees"], d["female_employee_pct"], d["diwali_bonus"],
            d["min_salary"], d["max_salary"], d["key_skills"],
            d["job_timings"], d["gender_requirements"], d["tools_requirements"],
            d["other_details"],
            new_stage, d.get("is_ca_firm"), existing["id"], d.get("district"),
            d.get("paid_leaves"), d.get("city"), d.get("state"),
        )
        if not d.get("skip_agreement"):
            background_tasks.add_task(_generate_and_send_agreement, str(existing["id"]))
        background_tasks.add_task(_classify_office_type, str(existing["id"]), d.get("job_location") or "")
        background_tasks.add_task(
            _trigger_rm_assignment,
            str(existing["id"]), d.get("location") or d.get("job_location") or "",
            row["poc_phone"] or "", row["poc_name"] or "",
        )
        return {"data": _normalize(row), "ok": True, "action": "updated"}

    # No existing client — insert at interested stage
    phone_numbers = d.get("phone_numbers") or []
    if phone_10:
        phone_numbers = [{"source": "onboarding_form", "number": phone_10}]

    row = await conn.fetchrow(
        """
        INSERT INTO clients (
            company_name, location, job_location, location_lat, location_lng,
            poc_name, poc_position, poc_phone, job_title, industry, num_employees,
            min_salary, max_salary, key_skills, job_timings, gst_tds, other_details,
            age_requirements, gender_requirements, tools_requirements, religion_requirement,
            payment_terms, salary_deductions, job_type, fee_amount, replacement_period,
            agreement_url, source, stage,
            phone_numbers, agreed_phone, company_website, company_description, source_job_id,
            female_employee_pct, diwali_bonus, business_type,
            segment, open_positions, is_ca_firm, district, paid_leaves, city, state
        ) VALUES (
            $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,
            $18,$19,$20,$21,$22,$23,'Full Time',$24,$25,$26,$27,'interested'::client_stage,
            $28::jsonb,$29,$30,$31,$32,
            $33,$34,$35,$36,$37,$38,$39,$40,$41,$42
        ) RETURNING *
        """,
        d["company_name"], d["location"], d["job_location"],
        d["location_lat"], d["location_lng"],
        d["poc_name"], d["poc_position"], phone_10 or d["poc_phone"],
        d["job_title"], d["industry"], d["num_employees"],
        d["min_salary"], d["max_salary"], d["key_skills"],
        d["job_timings"], d["gst_tds"], d["other_details"],
        d["age_requirements"], d["gender_requirements"], d["tools_requirements"],
        d["religion_requirement"], d["payment_terms"], d["salary_deductions"],
        d["fee_amount"], d["replacement_period"],
        d["agreement_url"], d["source"],
        _json.dumps(phone_numbers), d.get("agreed_phone"),
        d.get("company_website"), d.get("company_description"), d.get("source_job_id"),
        d.get("female_employee_pct"), d.get("diwali_bonus"), d.get("business_type"),
        d.get("segment"), d.get("open_positions") or 1, d.get("is_ca_firm") or False,
        d.get("district"), d.get("paid_leaves"), d.get("city"), d.get("state"),
    )
    if not d.get("skip_agreement"):
        background_tasks.add_task(_generate_and_send_agreement, str(row["id"]))
    background_tasks.add_task(_classify_office_type, str(row["id"]), d.get("job_location") or "")
    background_tasks.add_task(
        _trigger_rm_assignment,
        str(row["id"]), d.get("location") or d.get("job_location") or "",
        row["poc_phone"] or "", row["poc_name"] or "",
    )
    return {"data": _normalize(row), "ok": True, "action": "created"}


@router.post("", status_code=201)
async def create_client(
    body: ClientCreate,
    background_tasks: BackgroundTasks,
    conn: asyncpg.Connection = Depends(get_conn),
):
    d = body.model_dump()
    stage = d.get("initial_stage") or "lead"
    if d.get("job_location") and not d.get("location_lat"):
        d["location_lat"], d["location_lng"], d["city"], d["state"] = await geocode(d["job_location"])
    import json as _json
    phone_numbers = d.get("phone_numbers") or []
    row = await conn.fetchrow(
        """
        INSERT INTO clients (
            company_name, location, job_location, location_lat, location_lng,
            poc_name, poc_position, poc_phone, job_title, industry, num_employees,
            min_salary, max_salary, key_skills, job_timings, gst_tds, other_details,
            age_requirements, gender_requirements, tools_requirements, religion_requirement,
            payment_terms, salary_deductions, job_type, fee_amount, replacement_period,
            agreement_url, source, stage,
            phone_numbers, agreed_phone, company_website, company_description, source_job_id,
            female_employee_pct, diwali_bonus, business_type,
            segment, open_positions, travel_radius, city, state
        ) VALUES (
            $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,
            $18,$19,$20,$21,$22,$23,'Full Time',$24,$25,$26,$27,$28::client_stage,
            $29::jsonb,$30,$31,$32,$33,
            $34,$35,$36,$37,$38,$39,$40,$41
        ) RETURNING *
        """,
        d["company_name"], d["location"], d["job_location"],
        d["location_lat"], d["location_lng"],
        d["poc_name"], d["poc_position"], d["poc_phone"],
        d["job_title"], d["industry"], d["num_employees"],
        d["min_salary"], d["max_salary"], d["key_skills"],
        d["job_timings"], d["gst_tds"], d["other_details"],
        d["age_requirements"], d["gender_requirements"], d["tools_requirements"],
        d["religion_requirement"], d["payment_terms"], d["salary_deductions"],
        d["fee_amount"], d["replacement_period"],
        d["agreement_url"], d["source"], stage,
        _json.dumps(phone_numbers), d.get("agreed_phone"),
        d.get("company_website"), d.get("company_description"), d.get("source_job_id"),
        d.get("female_employee_pct"), d.get("diwali_bonus"), d.get("business_type"),
        d.get("segment"), d.get("open_positions") or 1, d.get("travel_radius"),
        d.get("city"), d.get("state"),
    )
    background_tasks.add_task(
        _trigger_rm_assignment,
        str(row["id"]), d.get("location") or d.get("job_location") or "",
        d.get("poc_phone") or "", d.get("poc_name") or "",
    )
    return {"data": _normalize(row), "ok": True}


class AgreePhoneRequest(BaseModel):
    phone: str

class SheetImportRequest(BaseModel):
    sheet_url: str

@router.patch("/{client_id}/agree-phone")
async def agree_phone(
    client_id: uuid.UUID,
    body: AgreePhoneRequest,
    conn: asyncpg.Connection = Depends(get_conn),
):
    import json as _json
    row = await conn.fetchrow("SELECT phone_numbers FROM clients WHERE id = $1", client_id)
    if not row:
        raise HTTPException(404, "Client not found")
    phones = row["phone_numbers"] or []
    updated = [
        {**p, "agreed": p.get("number") == body.phone}
        for p in phones
    ]
    await conn.execute(
        """UPDATE clients SET agreed_phone=$1, poc_phone=$1,
           phone_numbers=$2::jsonb, updated_at=now() WHERE id=$3""",
        body.phone, _json.dumps(updated), client_id,
    )
    row = await conn.fetchrow("SELECT * FROM clients WHERE id = $1", client_id)
    return {"data": _normalize(row), "ok": True}


import csv as _csv
import io as _io

def _clean_phone(v: str) -> str | None:
    v = v.strip()
    if not v:
        return None
    import re
    digits = re.sub(r'\D', '', v)
    if len(digits) == 12 and digits.startswith('91'):
        digits = digits[2:]
    return digits if len(digits) >= 10 else None

def _map_status(v: str) -> str:
    mapping = {
        'scraped': 'scraped', 'scraping': 'scraping', 'lead': 'lead',
        'reachout_sent': 'reachout_sent', 'interested': 'interested',
        'onboarded': 'onboarded', 'disqualified': 'disqualified',
    }
    return mapping.get(v.lower().strip(), 'scraped')

@router.post("/import")
async def import_from_sheet(
    body: SheetImportRequest,
    conn: asyncpg.Connection = Depends(get_conn),
):
    import re as _re, json as _json
    m = _re.search(r'/d/([a-zA-Z0-9-_]+)', body.sheet_url)
    if not m:
        raise HTTPException(400, "Could not extract sheet ID from URL")
    sheet_id = m.group(1)
    gid_m = _re.search(r'[#&?]gid=(\d+)', body.sheet_url)
    csv_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv"
    if gid_m:
        csv_url += f"&gid={gid_m.group(1)}"

    async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as http:
        resp = await http.get(csv_url)
    if not resp.is_success:
        raise HTTPException(400, f"Failed to fetch sheet (HTTP {resp.status_code}). Make sure it is shared publicly.")

    reader = _csv.DictReader(_io.StringIO(resp.text))
    inserted = updated = skipped = 0
    errors: list[dict] = []

    for line_num, raw in enumerate(reader, start=2):
        try:
            company_name = raw.get('Client Name', '').strip()
            if not company_name:
                skipped += 1
                continue

            phone_sources = [
                ('google',    raw.get('Client Number [ Google ]', '')),
                ('apollo',    raw.get('Client Number [ Apollo ]', '')),
                ('website',   raw.get('Client Number [ Website ]', '')),
                ('indiamart', raw.get('Client Number [ IndiaMart ]', '')),
            ]
            phone_numbers = [
                {"source": src, "number": _clean_phone(num)}
                for src, num in phone_sources
                if _clean_phone(num)
            ]
            first_phone = phone_numbers[0]["number"] if phone_numbers else None
            location = raw.get('Location', '').strip() or None
            stage = _map_status(raw.get('Status', 'scraped'))
            source_job_id = raw.get('Source Job Id', '').strip() or None

            lat, lng, city, state = None, None, None, None
            if location:
                lat, lng, city, state = await geocode(location)

            existing = None
            if source_job_id:
                existing = await conn.fetchrow(
                    "SELECT id FROM clients WHERE source_job_id = $1", source_job_id
                )

            if existing:
                await conn.execute(
                    """UPDATE clients SET
                        company_name=$1, location=$2, job_location=$2,
                        location_lat=$3, location_lng=$4,
                        poc_name=$5, poc_position=$6, poc_phone=$7,
                        job_title=$8, source=$9, company_website=$10,
                        company_description=$11, phone_numbers=$12::jsonb,
                        stage=$13::client_stage, city=COALESCE($15, city),
                        state=COALESCE($16, state), updated_at=now()
                    WHERE source_job_id=$14""",
                    company_name, location, lat, lng,
                    raw.get('Client POC Name', '').strip() or None,
                    raw.get('Client POC Position', '').strip() or None,
                    first_phone,
                    _normalize_job_title(raw.get('Job Title', '').strip() or None),
                    raw.get('Source', '').strip() or None,
                    raw.get('Company Website', '').strip() or None,
                    raw.get('Company Description', '').strip() or None,
                    _json.dumps(phone_numbers),
                    stage, source_job_id, city, state,
                )
                updated += 1
            else:
                await conn.execute(
                    """INSERT INTO clients (
                        company_name, location, job_location, location_lat, location_lng,
                        poc_name, poc_position, poc_phone, job_title, source,
                        company_website, company_description, phone_numbers,
                        stage, source_job_id, city, state
                    ) VALUES (
                        $1,$2,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12::jsonb,$13::client_stage,$14,$15,$16
                    )""",
                    company_name, location, lat, lng,
                    raw.get('Client POC Name', '').strip() or None,
                    raw.get('Client POC Position', '').strip() or None,
                    first_phone,
                    _normalize_job_title(raw.get('Job Title', '').strip() or None),
                    raw.get('Source', '').strip() or None,
                    raw.get('Company Website', '').strip() or None,
                    raw.get('Company Description', '').strip() or None,
                    _json.dumps(phone_numbers),
                    stage, source_job_id, city, state,
                )
                inserted += 1
        except Exception as e:
            errors.append({"row": line_num, "company": raw.get('Client Name', ''), "error": str(e)})

    return {"inserted": inserted, "updated": updated, "skipped": skipped, "errors": errors, "ok": True}


@router.patch("/{client_id}/recruiter-slots")
async def set_recruiter_slots(
    client_id: uuid.UUID,
    body: RecruiterSlotsRequest,
    background_tasks: BackgroundTasks,
    conn: asyncpg.Connection = Depends(get_conn),
):
    slots_with_ids = [
        {"id": s.get("id") or str(uuid.uuid4()), "label": s["label"], "iso": s.get("iso", "")}
        for s in body.slots
    ]
    result = await conn.execute(
        "UPDATE clients SET recruiter_slots = $1, updated_at = now() WHERE id = $2",
        slots_with_ids,
        client_id,
    )
    if result == "UPDATE 0":
        raise HTTPException(404, "Client not found")

    # Fire fan-out (slot-selection messages + JD to mms>70 candidates not yet contacted)
    if slots_with_ids:
        background_tasks.add_task(_run_slot_fanout, str(client_id))

    return {"data": slots_with_ids, "ok": True}


async def _run_slot_fanout(client_id: str) -> None:
    from database import get_pool
    from routers.matching import on_slots_submitted
    pool = get_pool()
    async with pool.acquire() as conn:
        try:
            await on_slots_submitted(uuid.UUID(client_id), conn)
        except Exception as e:
            print(f"[slot_fanout] failed for {client_id}: {e}")


@router.post("/{client_id}/generate-agreement")
async def generate_agreement(
    client_id: uuid.UUID,
    body: AgreementFieldOverrides,
    conn: asyncpg.Connection = Depends(get_conn),
):
    from services import agreement_service as ag
    client = await conn.fetchrow("SELECT * FROM clients WHERE id = $1", client_id)
    if not client:
        raise HTTPException(404, "Client not found")

    fields = ag.client_to_fields(dict(client))
    # Apply overrides from the request body (only non-None values)
    for k, v in body.model_dump().items():
        if v is not None:
            fields[k] = v

    html = ag.build_agreement_html(fields)
    try:
        pdf = ag.generate_pdf(html)
        url = ag.upload_to_gcp(pdf, fields["client_name"], ext="pdf")
    except Exception as e:
        raise HTTPException(500, f"PDF generation/upload failed: {e}")

    await conn.execute(
        "UPDATE clients SET agreement_url = $1, updated_at = now() WHERE id = $2",
        url, client_id,
    )
    # Persist payment/backdoor overrides so future regenerations reuse them
    if body.payment_due_days is not None or body.backdoor_period is not None:
        await conn.execute(
            """
            UPDATE clients
            SET payment_due_days = COALESCE($1, payment_due_days),
                backdoor_period  = COALESCE($2, backdoor_period),
                updated_at = now()
            WHERE id = $3
            """,
            body.payment_due_days, body.backdoor_period, client_id,
        )
    return {"data": {"url": url}, "ok": True}


@router.post("/{client_id}/send-agreement")
async def send_agreement(
    client_id: uuid.UUID,
    conn: asyncpg.Connection = Depends(get_conn),
):
    """Generate (or refresh) the agreement PDF and send the client the frontend
    agreement page link via the client_send_agreement WhatsApp template."""
    try:
        await _generate_and_send_agreement(str(client_id), raise_on_error=True)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"Failed to send agreement: {e}")

    row = await conn.fetchrow("SELECT * FROM clients WHERE id = $1", client_id)
    if not row:
        raise HTTPException(404, "Client not found")
    return {"data": _normalize(row), "ok": True}


@router.get("/{client_id}")
async def get_client(
    client_id: uuid.UUID,
    conn: asyncpg.Connection = Depends(get_conn),
):
    row = await conn.fetchrow("SELECT * FROM clients WHERE id = $1", client_id)
    if not row:
        raise HTTPException(404, "Client not found")
    return {"data": _normalize(row), "ok": True}


@router.patch("/{client_id}")
async def update_client(
    client_id: uuid.UUID,
    body: ClientUpdate,
    background_tasks: BackgroundTasks,
    conn: asyncpg.Connection = Depends(get_conn),
):
    updates = body.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(400, "No fields to update")

    if "job_location" in updates and "location_lat" not in updates:
        lat, lng, city, state = await geocode(updates["job_location"])
        updates["location_lat"], updates["location_lng"] = lat, lng
        if city:
            updates["city"] = city
        if state:
            updates["state"] = state

    keys = list(updates.keys())
    values = list(updates.values())
    set_clause = ", ".join(f"{k} = ${i + 1}" for i, k in enumerate(keys))
    values.append(client_id)

    row = await conn.fetchrow(
        f"UPDATE clients SET {set_clause}, updated_at = now() WHERE id = ${len(values)} RETURNING *",
        *values,
    )
    if not row:
        raise HTTPException(404, "Client not found")

    if "job_location" in updates:
        background_tasks.add_task(_classify_office_type, str(client_id), updates["job_location"])

    if AGREEMENT_FIELDS.intersection(updates.keys()):
        background_tasks.add_task(_regenerate_agreement_pdf, str(client_id))

    return {"data": _normalize(row), "ok": True}


@router.delete("/{client_id}")
async def delete_client(
    client_id: uuid.UUID,
    conn: asyncpg.Connection = Depends(get_conn),
):
    result = await conn.execute("DELETE FROM clients WHERE id = $1", client_id)
    if result == "DELETE 0":
        raise HTTPException(404, "Client not found")
    return {"ok": True}


@router.patch("/{client_id}/stage")
async def update_stage(
    client_id: uuid.UUID,
    body: StageUpdateRequest,
    background_tasks: BackgroundTasks,
    conn: asyncpg.Connection = Depends(get_conn),
):
    # Set first-send timestamps when entering follow-up stages (COALESCE = only on first entry)
    extra_sets = ""
    if body.to_stage.value == "agreement_sent":
        extra_sets = ", agreement_sent_at = COALESCE(agreement_sent_at, now())"
    elif body.to_stage.value == "interested":
        extra_sets = ", onboarding_form_sent_at = COALESCE(onboarding_form_sent_at, now())"
    elif body.to_stage.value == "churned":
        extra_sets = ", churned_at = COALESCE(churned_at, now())"
    elif body.to_stage.value == "deal_won":
        extra_sets = ", won_at = COALESCE(won_at, now())"

    row = await conn.fetchrow(
        f"UPDATE clients SET stage = $1::client_stage, updated_at = now(){extra_sets} WHERE id = $2 RETURNING *",
        body.to_stage.value,
        client_id,
    )
    if not row:
        raise HTTPException(404, "Client not found")

    # The DB trigger already inserted a pipeline_events row; optionally enrich it with note/actor
    if body.note or body.created_by != "system":
        await conn.execute(
            """
            UPDATE pipeline_events SET note = $1, created_by = $2
            WHERE id = (
                SELECT id FROM pipeline_events
                WHERE client_id = $3 AND to_stage = $4::client_stage
                ORDER BY created_at DESC LIMIT 1
            )
            """,
            body.note,
            body.created_by,
            client_id,
            body.to_stage.value,
        )

    # Auto-trigger matchmaking when client is onboarded
    if body.to_stage.value == "onboarded":
        background_tasks.add_task(_run_auto_match, str(client_id))

    # Send hl_client_search_started at onboarding (90s delay to let agreement + RM intro land first)
    if body.to_stage.value == "onboarded":
        background_tasks.add_task(_send_search_started, str(client_id))

    # Churning a client releases locks and closes all active mappings to client_closed
    if body.to_stage.value == "churned":
        await conn.execute(
            """
            UPDATE candidate_locks
            SET unlocked_at = now(), unlock_reason = 'client_churned'
            WHERE client_id = $1 AND unlocked_at IS NULL
            """,
            client_id,
        )
        affected = await conn.fetch(
            """
            UPDATE client_candidate_mappings
            SET stage = 'client_closed'::mapping_stage, updated_at = now()
            WHERE client_id = $1
              AND stage::text NOT IN (
                'placed','rejected','rejected_by_client',
                'not_interested','declined_for_interview','client_closed'
              )
            RETURNING (SELECT c.name FROM candidates c WHERE c.id = candidate_id) AS candidate_name,
                      (SELECT c.phone FROM candidates c WHERE c.id = candidate_id) AS candidate_phone
            """,
            client_id,
        )
        if affected:
            company_name = row["company_name"]
            background_tasks.add_task(_notify_churn_closed, list(affected), company_name)

    return {"data": _normalize(row), "ok": True}


async def _notify_churn_closed(rows: list, company_name: str) -> None:
    import os
    from routers.admin import _send_position_closed_msg
    for r in rows:
        phone = r["candidate_phone"] if hasattr(r, "__getitem__") else r.get("candidate_phone")
        name  = r["candidate_name"]  if hasattr(r, "__getitem__") else r.get("candidate_name")
        if not phone:
            continue
        try:
            await _send_position_closed_msg(phone, name or "", company_name)
        except Exception as e:
            print(f"[churn/notify] {name}: {e}")


async def _send_search_started(client_id: str) -> None:
    import asyncio, os
    import services.msg91_service as msg91
    from database import get_pool

    await asyncio.sleep(90)
    async with get_pool().acquire() as conn:
        client = await conn.fetchrow("SELECT poc_phone, poc_name FROM clients WHERE id = $1", uuid.UUID(client_id))
        if not client:
            return
        poc_phone = client["poc_phone"] or ""
        poc_name  = client["poc_name"] or "there"
        if not poc_phone:
            return
        template = WT.CLIENT_SEARCH_STARTED
        try:
            if template:
                await msg91.send_template(
                    poc_phone, template, "en",
                    [{"type": "body", "parameters": [{"type": "text", "text": poc_name}]}],
                    sender="client",
                )
            else:
                await msg91.send_text(
                    poc_phone,
                    f"Hi {poc_name}! 👋\n\n"
                    "Thank you for onboarding with JustAccountants.\n\n"
                    "We have started shortlisting accountants that match your exact requirements "
                    "and will share the candidate profiles with you within 48 hours.\n\n"
                    "Stay tuned!\nJustAccountants Team",
                    sender="client",
                )
        except Exception as e:
            print(f"[onboard] search_started send failed for {client_id}: {e}")


async def _trigger_rm_assignment(client_id: str, location: str, poc_phone: str, poc_name: str) -> None:
    """Background: extract city from location string then auto-assign an RM (silently — intro is sent post-agree)."""
    from database import get_pool
    from services.rm_service import assign_rm

    city = _extract_city(location)
    pool = get_pool()
    async with pool.acquire() as conn:
        existing = await conn.fetchval("SELECT rm_id FROM clients WHERE id = $1::uuid", client_id)
        if existing:
            return
        await assign_rm(client_id, city, conn)


def _extract_city(location: str) -> str | None:
    """Best-effort city extraction: last comma-separated segment before a known city name."""
    if not location:
        return None
    KNOWN_CITIES = {"ahmedabad", "surat", "vadodara", "rajkot", "gandhinagar", "anand",
                    "nadiad", "bharuch", "mehsana", "bhavnagar", "jamnagar", "morbi"}
    lower = location.lower()
    for city in KNOWN_CITIES:
        if city in lower:
            return city.title()
    parts = [p.strip() for p in location.split(",")]
    for part in reversed(parts):
        if 3 < len(part) < 30 and part.isalpha():
            return part.title()
    return None


async def _run_auto_match(client_id: str) -> None:
    """Background task: run Step 0 decision-point when a client moves to onboarded."""
    from database import get_pool
    from routers.matching import decision_point
    pool = get_pool()
    async with pool.acquire() as conn:
        try:
            await decision_point(uuid.UUID(client_id), conn)
        except Exception as e:
            print(f"[auto_match] Decision-point failed for {client_id}: {e}")


@router.get("/agreement/{token}")
async def get_agreement_page(
    token: str,
    conn: asyncpg.Connection = Depends(get_conn),
):
    """Public endpoint — returns everything the agreement web page needs."""
    import json as _j

    client = await conn.fetchrow(
        "SELECT * FROM clients WHERE agreement_token = $1", token
    )
    if not client:
        raise HTTPException(404, "Agreement not found")

    c = dict(client)

    # Build 5-step funnel status
    stage = c["stage"]
    STAGE_ORDER = ["lead", "scraped", "reachout_sent", "interested", "agreement_sent", "onboarded"]
    stage_idx = STAGE_ORDER.index(stage) if stage in STAGE_ORDER else -1

    def _step_status(key: str) -> str:
        if key == "form_submitted":
            return "done" if stage_idx >= 3 else "active" if stage_idx >= 0 else "pending"
        if key == "agreement":
            return "done" if stage in ("onboarded",) else "active" if stage_idx >= 4 else "pending"
        if key == "profile_sharing":
            return "active" if stage == "onboarded" else "pending"
        return "pending"

    funnel = [
        {"key": "form_submitted",  "label": "Form Submitted",  "status": _step_status("form_submitted")},
        {"key": "agreement",       "label": "Review Agreement", "status": _step_status("agreement")},
        {"key": "profile_sharing", "label": "Profile Sharing",  "status": _step_status("profile_sharing")},
        {"key": "interviews",      "label": "Interviews",        "status": "pending"},
        {"key": "hired",           "label": "Hired",             "status": "pending"},
    ]

    def _parse_skills(raw):
        if isinstance(raw, str):
            try: return _j.loads(raw)
            except Exception: return [raw]
        return raw or []

    def _parse_gst_tds(raw):
        if isinstance(raw, str):
            try: return _j.loads(raw)
            except Exception: return {}
        return raw or {}

    def _position_from_row(row) -> dict:
        return {
            "job_title":          row.get("job_title"),
            "open_positions":     row.get("open_positions"),
            "min_salary":         row.get("min_salary"),
            "max_salary":         row.get("max_salary"),
            "fee_amount":         row.get("fee_amount"),
            "key_skills":         _parse_skills(row.get("key_skills")),
            "job_timings":        row.get("job_timings"),
            "job_type":           row.get("job_type"),
            "tools_requirements": row.get("tools_requirements"),
            "age_requirements":   row.get("age_requirements"),
            "gender_requirements":row.get("gender_requirements"),
            "gst_tds":            _parse_gst_tds(row.get("gst_tds")),
            "salary_deductions":  row.get("salary_deductions"),
        }

    # Collect all positions: primary + any linked secondaries
    positions = [_position_from_row(c)]
    linked_rows = await conn.fetch(
        "SELECT * FROM clients WHERE primary_client_id = $1 ORDER BY created_at ASC",
        c["id"],
    )
    for lr in linked_rows:
        positions.append(_position_from_row(dict(lr)))

    return {
        "data": {
            "client_id":          str(c["id"]),
            "company_name":       c.get("company_name"),
            "poc_name":           c.get("poc_name"),
            "job_location":       c.get("job_location"),
            "payment_terms":      c.get("payment_terms"),
            "payment_due_days":   c.get("payment_due_days") or 10,
            "replacement_period": c.get("replacement_period") or "3 months",
            "agreement_pdf_url":  c.get("agreement_url"),
            "modification_notes": c.get("modification_notes"),
            "stage":              stage,
            "already_decided":    stage in ("onboarded", "disqualified"),
            "funnel":             funnel,
            "positions":          positions,
        },
        "ok": True,
    }


@router.post("/agreement/{token}/agree")
async def agree_to_agreement(
    token: str,
    conn: asyncpg.Connection = Depends(get_conn),
):
    """Client confirms the agreement from the web page."""
    client = await conn.fetchrow(
        "SELECT id, stage, poc_phone, poc_name, rm_id FROM clients WHERE agreement_token = $1", token
    )
    if not client:
        raise HTTPException(404, "Agreement not found")
    if client["stage"] == "onboarded":
        return {"data": {"message": "Already confirmed"}, "ok": True}
    if client["stage"] not in ("agreement_sent", "interested"):
        raise HTTPException(400, f"Cannot confirm agreement in stage '{client['stage']}'")

    await conn.execute(
        "UPDATE clients SET stage = 'onboarded'::client_stage, updated_at = now() WHERE id = $1",
        client["id"],
    )
    # Also onboard any linked secondary positions
    secondary_rows = await conn.fetch(
        "UPDATE clients SET stage = 'onboarded'::client_stage, updated_at = now() WHERE primary_client_id = $1 RETURNING id",
        client["id"],
    )

    # Post-agree: RM intro
    from routers.whatsapp import _post_agree_actions
    try:
        await _post_agree_actions(conn, client["id"])
    except Exception as e:
        print(f"[agreement/agree] post-agree actions failed for {client['id']}: {e}")

    # Secondary positions are separate client rows/roles — they each need their
    # own matchmaking run, but not a second copy of the primary's agreement
    # PDF/portal-link/RM messages (those are company-level, already sent once
    # above), so trigger matching directly instead of the full _post_agree_actions.
    import asyncio
    for row in secondary_rows:
        asyncio.create_task(_run_auto_match(str(row["id"])))
        asyncio.create_task(_send_search_started(str(row["id"])))

    return {"data": {"confirmed": True}, "ok": True}


class CombinedAgreementRequest(BaseModel):
    primary_client_id: str
    linked_client_ids: list[str]  # secondary client IDs (job 2, 3, …)


@router.post("/combined-agreement", status_code=200)
async def create_combined_agreement(
    body: CombinedAgreementRequest,
    conn: asyncpg.Connection = Depends(get_conn),
):
    """Link secondary clients to the primary and send one agreement for all positions."""
    try:
        primary_uuid = uuid.UUID(body.primary_client_id)
    except ValueError:
        raise HTTPException(400, "Invalid primary_client_id")

    primary = await conn.fetchrow("SELECT id FROM clients WHERE id = $1", primary_uuid)
    if not primary:
        raise HTTPException(404, "Primary client not found")

    for lid in body.linked_client_ids:
        try:
            linked_uuid = uuid.UUID(lid)
        except ValueError:
            continue
        await conn.execute(
            "UPDATE clients SET primary_client_id = $1, updated_at = now() WHERE id = $2",
            primary_uuid, linked_uuid,
        )

    token = await _generate_and_send_agreement(body.primary_client_id)
    return {"data": {"primary_client_id": body.primary_client_id, "agreement_token": token}, "ok": True}


class ModificationRequest(BaseModel):
    modification_text: str


@router.post("/agreement/{token}/modify")
async def request_modification(
    token: str,
    body: ModificationRequest,
    conn: asyncpg.Connection = Depends(get_conn),
):
    """Client requests modifications to the agreement from the web page."""
    import services.msg91_service as msg91

    client = await conn.fetchrow(
        "SELECT id, company_name, poc_name, rm_id FROM clients WHERE agreement_token = $1", token
    )
    if not client:
        raise HTTPException(404, "Agreement not found")

    await conn.execute(
        "UPDATE clients SET modification_notes = $1, updated_at = now() WHERE id = $2",
        body.modification_text, client["id"],
    )

    # Notify RM (or fallback to a configured recruiter number)
    notify_phone = None
    if client["rm_id"]:
        rm = await conn.fetchrow("SELECT phone FROM rms WHERE id = $1", client["rm_id"])
        if rm:
            notify_phone = rm["phone"]
    if not notify_phone:
        notify_phone = os.environ.get("RECRUITER_NOTIFY_PHONE", "").strip()

    if notify_phone:
        try:
            company = client["company_name"] or "Unknown Client"
            poc = client["poc_name"] or ""
            await msg91.send_text(
                notify_phone,
                f"Agreement modification requested by {company}"
                + (f" ({poc})" if poc else "")
                + f":\n\n{body.modification_text}",
                sender="client",
            )
        except Exception as e:
            print(f"[agreement/modify] notify failed: {e}")

    return {"data": {"submitted": True}, "ok": True}


class ClientCommentCreate(BaseModel):
    comment_text: Optional[str] = None
    experience_criteria: Optional[str] = None
    created_by: str = "RM"


@router.get("/{client_id}/comments")
async def list_client_comments(
    client_id: uuid.UUID,
    conn: asyncpg.Connection = Depends(get_conn),
):
    rows = await conn.fetch(
        "SELECT * FROM client_comments WHERE client_id = $1 ORDER BY created_at DESC",
        client_id,
    )
    return {"data": [dict(r) for r in rows], "count": len(rows), "ok": True}


@router.post("/{client_id}/comments", status_code=201)
async def add_client_comment(
    client_id: uuid.UUID,
    body: ClientCommentCreate,
    conn: asyncpg.Connection = Depends(get_conn),
):
    if not (body.comment_text or "").strip() and not (body.experience_criteria or "").strip():
        raise HTTPException(400, "Comment text or sourcing experience criteria is required")

    row = await conn.fetchrow(
        """
        INSERT INTO client_comments (client_id, comment_text, experience_criteria, created_by)
        VALUES ($1, $2, $3, $4)
        RETURNING *
        """,
        client_id, body.comment_text, body.experience_criteria, body.created_by,
    )
    return {"data": dict(row), "ok": True}


@router.delete("/{client_id}/comments/{comment_id}")
async def delete_client_comment(
    client_id: uuid.UUID,
    comment_id: uuid.UUID,
    conn: asyncpg.Connection = Depends(get_conn),
):
    await conn.execute(
        "DELETE FROM client_comments WHERE id = $1 AND client_id = $2",
        comment_id, client_id,
    )
    return {"ok": True}


@router.get("/{client_id}/timeline")
async def get_timeline(
    client_id: uuid.UUID,
    conn: asyncpg.Connection = Depends(get_conn),
):
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS client_activity_log (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            client_id uuid REFERENCES clients(id) ON DELETE CASCADE,
            event_type text NOT NULL,
            details jsonb,
            created_by text DEFAULT 'system',
            created_at timestamptz DEFAULT now()
        )
    """)

    stage_rows = await conn.fetch(
        "SELECT * FROM pipeline_events WHERE client_id = $1",
        client_id,
    )
    activity_rows = await conn.fetch(
        "SELECT * FROM client_activity_log WHERE client_id = $1",
        client_id,
    )

    events = []
    for r in stage_rows:
        e = _normalize(r)
        e["event_type"] = "stage_change"
        # Match the client_activity_log branch below — created_at must be a
        # string here too, or sorting the merged list crashes ("'<' not
        # supported between instances of 'str' and 'datetime.datetime'")
        # the moment a client has both event types.
        if e.get("created_at"):
            e["created_at"] = e["created_at"].isoformat()
        events.append(e)
    for r in activity_rows:
        events.append({
            "id": str(r["id"]),
            "client_id": str(r["client_id"]),
            "event_type": r["event_type"],
            "details": r["details"],
            "created_by": r["created_by"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        })

    events.sort(key=lambda e: e["created_at"] or "")
    return {"data": events, "count": len(events), "ok": True}


@router.get("/{client_id}/locked-candidates")
async def get_locked_candidates(
    client_id: uuid.UUID,
    conn: asyncpg.Connection = Depends(get_conn),
):
    rows = await conn.fetch(
        """
        SELECT c.id, c.name
        FROM candidate_locks cl
        JOIN candidates c ON c.id = cl.candidate_id
        WHERE cl.client_id = $1 AND cl.unlocked_at IS NULL
        ORDER BY cl.locked_at ASC
        """,
        client_id,
    )
    return {"data": [dict(r) for r in rows], "count": len(rows), "ok": True}


@router.get("/{client_id}/ai-calls")
async def get_client_ai_calls(
    client_id: uuid.UUID,
    conn: asyncpg.Connection = Depends(get_conn),
):
    rows = await conn.fetch(
        "SELECT * FROM client_ai_calls WHERE client_id = $1 ORDER BY triggered_at DESC",
        client_id,
    )
    return {"data": [_normalize(r) for r in rows], "count": len(rows), "ok": True}


@router.patch("/{client_id}/bot")
async def set_client_bot_flag(
    client_id: uuid.UUID,
    conn: asyncpg.Connection = Depends(get_conn),
):
    """Mark or unmark a client as a bot. Toggles is_bot flag."""
    client = await conn.fetchrow("SELECT id, is_bot FROM clients WHERE id = $1", client_id)
    if not client:
        raise HTTPException(404, "Client not found")
    new_val = not bool(client["is_bot"])
    await conn.execute(
        "UPDATE clients SET is_bot = $1, updated_at = now() WHERE id = $2",
        new_val, client_id,
    )
    return {"data": {"is_bot": new_val}, "ok": True}


@router.get("/{client_id}/status")
async def get_client_status(
    client_id: uuid.UUID,
    conn: asyncpg.Connection = Depends(get_conn),
):
    """Public endpoint — returns only what the client-facing status page needs."""
    client = await conn.fetchrow(
        "SELECT id, company_name, poc_name, job_title, stage, matching_state, feedback_token FROM clients WHERE id = $1",
        client_id,
    )
    if not client:
        raise HTTPException(404, "Not found")

    stage = client["stage"]
    STAGE_ORDER = ["lead", "scraping", "scraped", "reachout_sent", "interested", "agreement_sent", "onboarded"]
    stage_idx = STAGE_ORDER.index(stage) if stage in STAGE_ORDER else -1

    mapping_rows = await conn.fetch(
        "SELECT stage FROM client_candidate_mappings WHERE client_id = $1",
        client_id,
    )
    mstages = {r["stage"] for r in mapping_rows}

    has_profile_sharing = bool(mstages - {"matched"})
    has_interviews       = bool(mstages & {"slot_booked", "interview_done", "placed"})
    has_hired            = "placed" in mstages

    def _status(key: str) -> str:
        if key == "form_submitted":
            # Done as soon as client reaches 'interested' (form submitted)
            return "done" if stage_idx >= 4 else "active" if stage_idx >= 0 else "pending"
        if key == "agreement":
            if stage == "onboarded": return "done"
            if stage_idx >= 4:       return "active"   # interested or agreement_sent
            return "pending"
        if key == "profile_sharing":
            # Only relevant once agreement is signed (onboarded)
            if stage != "onboarded": return "pending"
            if has_interviews:       return "done"
            if has_profile_sharing:  return "active"
            return "active"   # onboarded but no mappings yet — profiles being sourced
        if key == "interviews":
            if stage != "onboarded": return "pending"
            if has_hired:            return "done"
            if has_interviews:       return "active"
            return "pending"
        if key == "hired":
            return "done" if has_hired else "pending"
        return "pending"

    funnel = [
        {"key": "form_submitted",  "label": "Form Submitted",  "status": _status("form_submitted")},
        {"key": "agreement",       "label": "Agreement",        "status": _status("agreement")},
        {"key": "profile_sharing", "label": "Profile Sharing",  "status": _status("profile_sharing")},
        {"key": "interviews",      "label": "Interviews",       "status": _status("interviews")},
        {"key": "hired",           "label": "Hired",            "status": _status("hired")},
    ]

    # 7-stage timeline for the post-agreement Success Page — reads matching_state
    # (set only by the existing matching flow) so it never claims outreach has
    # started before it actually has. Never sets matching_state — read-only.
    matching_state = client["matching_state"]
    matchmaking_started = matching_state is not None

    def _success_step(key: str) -> str:
        if key == "requirement_submitted":
            return "done"
        if key == "matchmaking_started":
            return "done" if matchmaking_started else "active"
        if key == "candidate_review":
            if has_interviews: return "done"
            return "active" if matchmaking_started else "pending"
        if key == "interview_scheduling":
            if has_hired: return "done"
            return "active" if has_interviews else "pending"
        if key == "hiring":
            return "done" if has_hired else "pending"
        return "pending"

    success_funnel = [
        {"key": "requirement_submitted", "label": "Requirement Submitted", "status": _success_step("requirement_submitted")},
        {"key": "matchmaking_started",   "label": "Matchmaking Started",   "status": _success_step("matchmaking_started")},
        {"key": "candidate_review",      "label": "Review Candidates",     "status": _success_step("candidate_review")},
        {"key": "interview_scheduling",  "label": "Schedule Interviews",   "status": _success_step("interview_scheduling")},
        {"key": "hiring",                "label": "Hire",                 "status": _success_step("hiring")},
    ]

    return {
        "data": {
            "company_name":   client["company_name"],
            "poc_name":       client["poc_name"],
            "job_title":      client["job_title"],
            "funnel":         funnel,
            "success_funnel": success_funnel,
            "matching_state": matching_state,
            "feedback_token": client["feedback_token"],
        },
        "ok": True,
    }
