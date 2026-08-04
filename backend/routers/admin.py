import uuid
import random
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
import asyncpg
from pydantic import BaseModel
from typing import Optional
from database import get_conn
import constants.whatsapp_templates as WT

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/test-alert")
async def test_google_chat_alert():
    """Fire a test engineering ticket to verify the ops_alert_service -> ticket pipeline is working."""
    from services.ops_alert_service import send_alert
    await send_alert(
        title="Test Alert — Notification Channel Live",
        detail="This is a test message from the Hire Lyra backend to verify the engineering ticket pipeline is working correctly.",
        severity="INFO",
        context={"triggered_by": "manual test", "source": "Cloud Run"},
    )
    return {"data": {"sent": True}, "ok": True}

VALID_SEGMENTS = {"ca_network", "active_job_post", "employer_lead", "all"}


# ── Request models ─────────────────────────────────────────────────────────────

class RMCreate(BaseModel):
    name: str
    phone: Optional[str] = None
    email: Optional[str] = None
    location: Optional[str] = None
    city: Optional[str] = None
    max_clients: int = 20
    is_active: bool = True


class RMUpdate(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    location: Optional[str] = None
    city: Optional[str] = None
    max_clients: Optional[int] = None
    is_active: Optional[bool] = None
    pin: Optional[str] = None


class RMVerifyPin(BaseModel):
    pin: str


# ── Relationship Managers ──────────────────────────────────────────────────────

@router.get("/rms")
async def list_rms(conn: asyncpg.Connection = Depends(get_conn)):
    rows = await conn.fetch(
        """
        SELECT
            r.*,
            COUNT(c.id) FILTER (WHERE c.stage::text != 'disqualified') AS active_clients
        FROM rms r
        LEFT JOIN clients c ON c.rm_id = r.id
        GROUP BY r.id
        ORDER BY r.name
        """
    )
    return {"data": [dict(row) for row in rows], "count": len(rows), "ok": True}


@router.post("/rms", status_code=201)
async def create_rm(body: RMCreate, conn: asyncpg.Connection = Depends(get_conn)):
    row = await conn.fetchrow(
        """
        INSERT INTO rms (name, phone, email, location, city, max_clients, is_active)
        VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING *
        """,
        body.name, body.phone, body.email, body.location, body.city, body.max_clients, body.is_active,
    )
    return {"data": {**dict(row), "active_clients": 0}, "ok": True}


@router.patch("/rms/{rm_id}")
async def update_rm(
    rm_id: uuid.UUID,
    body: RMUpdate,
    conn: asyncpg.Connection = Depends(get_conn),
):
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(400, "No fields to update")
    keys = list(updates.keys())
    vals = list(updates.values())
    set_clause = ", ".join(f"{k} = ${i+1}" for i, k in enumerate(keys))
    vals.append(rm_id)
    row = await conn.fetchrow(
        f"UPDATE rms SET {set_clause}, updated_at = now() WHERE id = ${len(vals)} RETURNING *",
        *vals,
    )
    if not row:
        raise HTTPException(404, "RM not found")
    active_clients = await conn.fetchval(
        "SELECT COUNT(*) FROM clients WHERE rm_id = $1 AND stage::text != 'disqualified'", rm_id
    )
    return {"data": {**dict(row), "active_clients": active_clients}, "ok": True}


@router.get("/rms/{rm_id}/clients")
async def list_rm_clients(rm_id: uuid.UUID, conn: asyncpg.Connection = Depends(get_conn)):
    rows = await conn.fetch(
        """
        SELECT id, company_name, stage::text AS stage, job_title, city, location
        FROM clients
        WHERE rm_id = $1 AND stage::text != 'disqualified'
        ORDER BY created_at DESC
        """,
        rm_id,
    )
    return {"data": [dict(r) for r in rows], "count": len(rows), "ok": True}


@router.delete("/rms/{rm_id}")
async def delete_rm(rm_id: uuid.UUID, conn: asyncpg.Connection = Depends(get_conn)):
    result = await conn.execute("DELETE FROM rms WHERE id = $1", rm_id)
    if result == "DELETE 0":
        raise HTTPException(404, "RM not found")
    return {"ok": True}


@router.post("/rms/{rm_id}/verify-pin")
async def verify_rm_pin(
    rm_id: uuid.UUID,
    body: RMVerifyPin,
    conn: asyncpg.Connection = Depends(get_conn),
):
    row = await conn.fetchrow("SELECT pin FROM rms WHERE id = $1", rm_id)
    if not row:
        raise HTTPException(404, "RM not found")
    if not row["pin"]:
        raise HTTPException(403, "No PIN set for this RM")
    if row["pin"] != body.pin:
        raise HTTPException(401, "Incorrect PIN")
    return {"ok": True}


@router.get("/rms/{rm_id}/actions")
async def get_rm_actions(rm_id: uuid.UUID, conn: asyncpg.Connection = Depends(get_conn)):
    """Public-safe: returns all pending actions for a specific RM."""
    rm = await conn.fetchrow(
        "SELECT id, name, phone, email, city, location FROM rms WHERE id = $1", rm_id
    )
    if not rm:
        raise HTTPException(404, "RM not found")

    # 0. Re-enquiry: client tried to fill the form again while already having an active deal
    await conn.execute(
        "ALTER TABLE clients ADD COLUMN IF NOT EXISTS re_enquiry_at timestamptz"
    )
    re_enquiry_rows = await conn.fetch(
        """
        SELECT id, company_name, poc_name, poc_phone, city, location, job_title,
               stage::text AS stage, re_enquiry_at,
               EXTRACT(EPOCH FROM (now() - re_enquiry_at))/3600 AS hours_since
        FROM clients
        WHERE rm_id = $1
          AND re_enquiry_at IS NOT NULL
          AND re_enquiry_at > now() - interval '7 days'
        ORDER BY re_enquiry_at DESC
        """,
        rm_id,
    )

    # 1. Form collection needed: client expressed WhatsApp interest but agreement not yet sent
    form_rows = await conn.fetch(
        """
        SELECT id, company_name, poc_name, poc_phone, city, location, job_title,
               EXTRACT(EPOCH FROM (now() - updated_at))/3600 AS hours_waiting
        FROM clients
        WHERE rm_id = $1
          AND stage::text = 'interested'
        ORDER BY updated_at ASC
        """,
        rm_id,
    )

    # 2. Agreement follow-up: agreement sent, waiting for signature
    await conn.execute(
        "ALTER TABLE clients ADD COLUMN IF NOT EXISTS agreement_sent_at timestamptz"
    )
    agreement_rows = await conn.fetch(
        """
        SELECT id, company_name, poc_name, poc_phone, city, location, job_title,
               EXTRACT(EPOCH FROM (now() - COALESCE(agreement_sent_at, updated_at)))/3600 AS hours_waiting
        FROM clients
        WHERE rm_id = $1
          AND stage::text = 'agreement_sent'
        ORDER BY COALESCE(agreement_sent_at, updated_at) ASC
        """,
        rm_id,
    )

    # Ensure all mapping columns used below exist
    for ddl in [
        "ALTER TABLE client_candidate_mappings ADD COLUMN IF NOT EXISTS slot_requested_at timestamptz",
        "ALTER TABLE client_candidate_mappings ADD COLUMN IF NOT EXISTS interview_done_at timestamptz",
        "ALTER TABLE client_candidate_mappings ADD COLUMN IF NOT EXISTS interview_done boolean DEFAULT false",
        "ALTER TABLE client_candidate_mappings ADD COLUMN IF NOT EXISTS feedback_client text",
    ]:
        await conn.execute(ddl)

    # 3. Interview booking needed: client has invited candidate, RM to book interview
    slot_rows = await conn.fetch(
        """
        SELECT
            ccm.id AS mapping_id,
            cl.id AS client_id, cl.company_name, cl.poc_name, cl.poc_phone, cl.city, cl.location,
            cl.feedback_token, cl.recruiter_slots, cl.available_slots AS client_available_slots,
            ca.name AS candidate_name, ca.phone AS candidate_phone,
            EXTRACT(EPOCH FROM (now() - ccm.updated_at))/3600 AS hours_waiting
        FROM client_candidate_mappings ccm
        JOIN clients    cl ON cl.id = ccm.client_id
        JOIN candidates ca ON ca.id = ccm.candidate_id
        WHERE cl.rm_id = $1
          AND ccm.stage::text = 'invited_for_interview'
        ORDER BY ccm.updated_at ASC
        """,
        rm_id,
    )

    # 3b. Candidates shortlisted for client approval (client_approval_pending)
    shortlisted_rows = await conn.fetch(
        """
        SELECT
            ccm.id AS mapping_id,
            cl.id AS client_id, cl.company_name, cl.poc_name, cl.poc_phone, cl.city, cl.location,
            cl.job_title, cl.feedback_token,
            ca.name AS candidate_name, ca.phone AS candidate_phone,
            ca.category, ca.experience, ca.current_salary, ca.expected_salary,
            ccm.match_score,
            EXTRACT(EPOCH FROM (now() - ccm.updated_at))/3600 AS hours_waiting
        FROM client_candidate_mappings ccm
        JOIN clients    cl ON cl.id = ccm.client_id
        JOIN candidates ca ON ca.id = ccm.candidate_id
        WHERE cl.rm_id = $1
          AND ccm.stage::text = 'client_approval_pending'
        ORDER BY cl.company_name ASC, ccm.match_score DESC NULLS LAST
        """,
        rm_id,
    )

    # 4. Post-interview hire/no-hire needed
    feedback_rows = await conn.fetch(
        """
        SELECT
            ccm.id AS mapping_id,
            cl.id AS client_id, cl.company_name, cl.poc_name, cl.poc_phone, cl.city,
            cl.feedback_token,
            ca.name AS candidate_name, ca.phone AS candidate_phone,
            ccm.interview_slot,
            EXTRACT(EPOCH FROM (now() - COALESCE(ccm.interview_done_at, ccm.updated_at)))/3600 AS hours_since_interview
        FROM client_candidate_mappings ccm
        JOIN clients    cl ON cl.id = ccm.client_id
        JOIN candidates ca ON ca.id = ccm.candidate_id
        WHERE cl.rm_id = $1
          AND ccm.stage::text = 'interview_done'
          AND (ccm.feedback_client IS NULL OR ccm.feedback_client = '')
        ORDER BY COALESCE(ccm.interview_done_at, ccm.updated_at) ASC
        """,
        rm_id,
    )

    # 5. Recently onboarded clients (last 48h)
    onboarded_rows = await conn.fetch(
        """
        SELECT cl.id, cl.company_name, cl.poc_name, cl.poc_phone, cl.city, cl.location, cl.job_title,
               pe.created_at AS onboarded_at,
               EXTRACT(EPOCH FROM (now() - pe.created_at))/3600 AS hours_since
        FROM clients cl
        JOIN pipeline_events pe ON pe.client_id = cl.id AND pe.to_stage = 'onboarded'
        WHERE cl.rm_id = $1
          AND cl.stage = 'onboarded'
          AND pe.created_at > now() - interval '48 hours'
        ORDER BY pe.created_at DESC
        """,
        rm_id,
    )

    # 6. Clients assigned to this RM with pipeline stats
    client_stats_rows = await conn.fetch(
        """
        SELECT
            cl.id,
            cl.company_name,
            cl.stage::text AS stage,
            cl.job_title,
            cl.city,
            cl.location,
            cl.poc_name,
            cl.poc_phone,
            cl.feedback_token,
            COUNT(DISTINCT ms.candidate_id) FILTER (WHERE ms.mms_score > 70) AS high_match_count,
            COUNT(DISTINCT ccm.id) FILTER (
                WHERE ccm.stage::text NOT IN ('rejected_by_client', 'placed', 'rejected', 'not_interested', 'declined_for_interview', 'client_closed')
            ) AS pipeline_count
        FROM clients cl
        LEFT JOIN mms_scores ms ON ms.client_id = cl.id
        LEFT JOIN client_candidate_mappings ccm ON ccm.client_id = cl.id
        WHERE cl.rm_id = $1
          AND cl.stage::text != 'disqualified'
        GROUP BY cl.id
        ORDER BY cl.created_at DESC
        """,
        rm_id,
    )

    def _fmt_hours(h) -> str:
        if h is None:
            return ""
        h = float(h)
        if h < 1:
            return "< 1h ago"
        if h < 24:
            return f"{int(h)}h ago"
        return f"{int(h / 24)}d ago"

    return {
        "data": {
            "rm": {
                "id":       str(rm["id"]),
                "name":     rm["name"],
                "phone":    rm["phone"],
                "city":     rm["city"],
                "location": rm["location"],
            },
            "re_enquiry": [
                {
                    "client_id":    str(r["id"]),
                    "company_name": r["company_name"],
                    "poc_name":     r["poc_name"],
                    "poc_phone":    r["poc_phone"],
                    "location":     r["city"] or r["location"],
                    "job_title":    r["job_title"],
                    "stage":        r["stage"],
                    "when":         _fmt_hours(r["hours_since"]),
                }
                for r in re_enquiry_rows
            ],
            "form_collection": [
                {
                    "client_id":    str(r["id"]),
                    "company_name": r["company_name"],
                    "poc_name":     r["poc_name"],
                    "poc_phone":    r["poc_phone"],
                    "location":     r["city"] or r["location"],
                    "job_title":    r["job_title"],
                    "waiting":      _fmt_hours(r["hours_waiting"]),
                }
                for r in form_rows
            ],
            "agreement_followup": [
                {
                    "client_id":    str(r["id"]),
                    "company_name": r["company_name"],
                    "poc_name":     r["poc_name"],
                    "poc_phone":    r["poc_phone"],
                    "location":     r["city"] or r["location"],
                    "job_title":    r["job_title"],
                    "waiting":      _fmt_hours(r["hours_waiting"]),
                }
                for r in agreement_rows
            ],
            "interview_booking": [
                {
                    "mapping_id":       str(r["mapping_id"]),
                    "client_id":        str(r["client_id"]),
                    "company_name":     r["company_name"],
                    "poc_name":         r["poc_name"],
                    "poc_phone":        r["poc_phone"],
                    "location":         r["city"] or r["location"],
                    "candidate_name":   r["candidate_name"],
                    "candidate_phone":  r["candidate_phone"],
                    "portal_token":     str(r["feedback_token"]) if r["feedback_token"] else None,
                    "waiting":          _fmt_hours(r["hours_waiting"]),
                    "client_slots":     list(r["recruiter_slots"] or r["client_available_slots"] or []),
                }
                for r in slot_rows
            ],
            "shortlisted_candidates": [
                {
                    "mapping_id":       str(r["mapping_id"]),
                    "client_id":        str(r["client_id"]),
                    "company_name":     r["company_name"],
                    "poc_name":         r["poc_name"],
                    "poc_phone":        r["poc_phone"],
                    "location":         r["city"] or r["location"],
                    "job_title":        r["job_title"],
                    "portal_token":     str(r["feedback_token"]) if r["feedback_token"] else None,
                    "candidate_name":   r["candidate_name"],
                    "candidate_phone":  r["candidate_phone"],
                    "category":         r["category"],
                    "experience":       r["experience"],
                    "current_salary":   float(r["current_salary"]) if r["current_salary"] else None,
                    "expected_salary":  float(r["expected_salary"]) if r["expected_salary"] else None,
                    "match_score":      int(r["match_score"]) if r["match_score"] else None,
                    "waiting":          _fmt_hours(r["hours_waiting"]),
                }
                for r in shortlisted_rows
            ],
            "interview_feedback": [
                {
                    "mapping_id":       str(r["mapping_id"]),
                    "client_id":        str(r["client_id"]),
                    "company_name":     r["company_name"],
                    "poc_name":         r["poc_name"],
                    "poc_phone":        r["poc_phone"],
                    "location":         r["city"],
                    "portal_token":     str(r["feedback_token"]) if r["feedback_token"] else None,
                    "candidate_name":   r["candidate_name"],
                    "candidate_phone":  r["candidate_phone"],
                    "interview_slot":   r["interview_slot"].isoformat() if r["interview_slot"] else None,
                    "waiting":          _fmt_hours(r["hours_since_interview"]),
                }
                for r in feedback_rows
            ],
            "recently_onboarded": [
                {
                    "client_id":    str(r["id"]),
                    "company_name": r["company_name"],
                    "poc_name":     r["poc_name"],
                    "poc_phone":    r["poc_phone"],
                    "location":     r["city"] or r["location"],
                    "job_title":    r["job_title"],
                    "when":         _fmt_hours(r["hours_since"]),
                }
                for r in onboarded_rows
            ],
            "clients": [
                {
                    "client_id":        str(r["id"]),
                    "company_name":     r["company_name"],
                    "stage":            r["stage"],
                    "job_title":        r["job_title"],
                    "location":         r["city"] or r["location"],
                    "poc_name":         r["poc_name"],
                    "poc_phone":        r["poc_phone"],
                    "portal_token":     str(r["feedback_token"]) if r["feedback_token"] else None,
                    "high_match_count": int(r["high_match_count"] or 0),
                    "pipeline_count":   int(r["pipeline_count"] or 0),
                }
                for r in client_stats_rows
            ],
        },
        "ok": True,
    }


class TemplateCreate(BaseModel):
    template_name: str
    image_url: Optional[str] = None
    segment: str
    is_active: bool = True


class TemplateUpdate(BaseModel):
    template_name: Optional[str] = None
    image_url: Optional[str] = None
    segment: Optional[str] = None
    is_active: Optional[bool] = None


class CronScheduleUpdate(BaseModel):
    cadence: Optional[str] = None   # 'daily' | 'weekly'
    is_active: Optional[bool] = None


# ── RM: book interview slot for candidate ──────────────────────────────────────

class RMSetClientSlotsBody(BaseModel):
    slots: list[str]   # list of slot labels


@router.post("/rms/set-client-slots/{client_id}")
async def rm_set_client_slots(
    client_id: str,
    body: RMSetClientSlotsBody,
    conn: asyncpg.Connection = Depends(get_conn),
):
    """RM submits interview availability on behalf of the client."""
    import uuid as _uuid
    from datetime import datetime, timezone

    try:
        cid = _uuid.UUID(client_id)
    except ValueError:
        raise HTTPException(400, "Invalid client ID")

    if not body.slots:
        raise HTTPException(400, "At least one slot required")

    bookings = [
        {"label": s, "client_name": "rm", "booked_at": datetime.now(timezone.utc).isoformat()}
        for s in body.slots
    ]

    await conn.execute(
        "UPDATE clients SET available_slots = $1, updated_at = now() WHERE id = $2",
        bookings, cid,
    )

    try:
        from routers.matching import on_slots_submitted
        await on_slots_submitted(cid, conn)
    except Exception as e:
        print(f"[rm/set-client-slots] on_slots_submitted failed: {e}")

    return {"data": {"set": len(bookings)}, "ok": True}


class RMSlotBookBody(BaseModel):
    slot_label: str


@router.post("/rms/book-slot/{mapping_id}")
async def rm_book_slot(
    mapping_id: str,
    body: RMSlotBookBody,
    background_tasks: BackgroundTasks,
    conn: asyncpg.Connection = Depends(get_conn),
):
    """RM books an interview slot directly on behalf of the candidate."""
    import uuid as _uuid
    from datetime import datetime, timezone, timedelta
    from routers.schedule import _parse_slot_label_to_dt

    try:
        mid = _uuid.UUID(mapping_id)
    except ValueError:
        raise HTTPException(400, "Invalid mapping ID")

    row = await conn.fetchrow(
        """
        SELECT ccm.id, ccm.stage, ccm.candidate_id, ccm.client_id,
               ca.name AS candidate_name, ca.phone AS candidate_phone,
               cl.company_name, cl.job_location, cl.location_lat, cl.location_lng,
               cl.recruiter_slots, cl.available_slots AS client_available_slots
        FROM client_candidate_mappings ccm
        JOIN candidates ca ON ca.id = ccm.candidate_id
        JOIN clients    cl ON cl.id = ccm.client_id
        WHERE ccm.id = $1
        """,
        mid,
    )
    if not row:
        raise HTTPException(404, "Mapping not found")
    if row["stage"] not in ("invited_for_interview",):
        raise HTTPException(409, f"Cannot book slot in stage '{row['stage']}'")

    # Validate slot label exists in client's slots
    client_slots = list(row["recruiter_slots"] or row["client_available_slots"] or [])
    slot_labels = [s.get("label", "") for s in client_slots]
    if slot_labels and body.slot_label not in slot_labels:
        raise HTTPException(400, "Slot not found in client availability")

    parsed_slot_dt = _parse_slot_label_to_dt(body.slot_label)

    await conn.execute(
        """
        UPDATE client_candidate_mappings
        SET available_slots = $1,
            stage           = 'slot_booked'::mapping_stage,
            interview_slot  = $3,
            slot_sent_at    = now(),
            updated_at      = now()
        WHERE id = $2
        """,
        [{"label": body.slot_label, "confirmed_by": "rm",
          "confirmed_at": datetime.now(timezone.utc).isoformat()}],
        mid,
        parsed_slot_dt,
    )

    if parsed_slot_dt:
        lock_expires = parsed_slot_dt + timedelta(hours=24)
        await conn.execute(
            """
            INSERT INTO candidate_locks (candidate_id, client_id, expires_at)
            VALUES ($1, $2, $3)
            ON CONFLICT (candidate_id, client_id)
            DO UPDATE SET expires_at = EXCLUDED.expires_at, unlocked_at = NULL
            """,
            row["candidate_id"], row["client_id"], lock_expires,
        )

    from routers.matching import (
        _send_interview_confirmed_to_candidate,
        _send_interview_confirmation_to_client_bg,
    )
    background_tasks.add_task(_send_interview_confirmed_to_candidate, str(mid), body.slot_label)
    background_tasks.add_task(_send_interview_confirmation_to_client_bg, str(mid), body.slot_label)

    return {"data": {"booked": True, "slot_label": body.slot_label}, "ok": True}


# ── RM: resend slot-selection link to candidate (reschedule) ─────────────────

@router.post("/rms/resend-slot/{mapping_id}")
async def rm_resend_slot(
    mapping_id: str,
    conn: asyncpg.Connection = Depends(get_conn),
):
    """Reset slot state and re-send slot-selection link so candidate picks again."""
    import uuid as _uuid
    try:
        mid = _uuid.UUID(mapping_id)
    except ValueError:
        raise HTTPException(400, "Invalid mapping ID")

    row = await conn.fetchrow(
        """
        SELECT ccm.id, ccm.candidate_id, ccm.client_id,
               ca.name AS candidate_name, ca.phone AS candidate_phone
        FROM client_candidate_mappings ccm
        JOIN candidates ca ON ca.id = ccm.candidate_id
        WHERE ccm.id = $1
        """,
        mid,
    )
    if not row:
        raise HTTPException(404, "Mapping not found")
    if not row["candidate_phone"]:
        raise HTTPException(400, "Candidate has no phone number")

    client = await conn.fetchrow("SELECT * FROM clients WHERE id = $1", row["client_id"])
    if not client:
        raise HTTPException(404, "Client not found")

    # Reset slot state so the dedup guard in _send_slot_message_to_candidate is bypassed
    await conn.execute(
        """
        UPDATE client_candidate_mappings
        SET slot_booking_sent = false,
            available_slots   = NULL,
            interview_slot    = NULL,
            stage             = 'invited_for_interview'::mapping_stage,
            updated_at        = now()
        WHERE id = $1
        """,
        mid,
    )

    from routers.matching import _send_slot_message_to_candidate
    import services.msg91_service as _msg91
    phone = _msg91._format_phone(row["candidate_phone"])
    sent = await _send_slot_message_to_candidate(
        mid, row["candidate_id"], phone, row["candidate_name"] or "", dict(client), conn
    )
    if not sent:
        raise HTTPException(500, "Failed to send slot message — check logs")

    return {"data": {"resent": True}, "ok": True}


# ── RM: bulk re-send JD (any stage, any mapping) ─────────────────────────────

class BulkResendJdRequest(BaseModel):
    mapping_ids: list[str]

@router.post("/rms/bulk-resend-jd")
async def rm_bulk_resend_jd(
    body: BulkResendJdRequest,
    conn: asyncpg.Connection = Depends(get_conn),
):
    """Force-resend JD WhatsApp to selected candidates regardless of stage or wa_sent_at."""
    if not body.mapping_ids:
        raise HTTPException(400, "No mapping IDs provided")

    mapping_uuids = [uuid.UUID(mid) for mid in body.mapping_ids]

    rows = await conn.fetch(
        """
        SELECT ccm.id, ccm.client_id, ccm.candidate_id,
               c.phone AS candidate_phone, c.name AS candidate_name,
               c.source AS candidate_source, c.form_submitted AS candidate_form_submitted
        FROM client_candidate_mappings ccm
        JOIN candidates c ON c.id = ccm.candidate_id
        WHERE ccm.id = ANY($1::uuid[]) AND c.phone IS NOT NULL
        """,
        mapping_uuids,
    )
    if not rows:
        raise HTTPException(400, "No valid mappings with phone numbers found")

    client = await conn.fetchrow("SELECT * FROM clients WHERE id = $1", rows[0]["client_id"])
    if not client:
        raise HTTPException(404, "Client not found")

    from routers.cron import _build_jd_components, _jd_intro, _client_maps_url
    import services.msg91_service as msg91

    components = _build_jd_components(dict(client))

    jd_image_url: str | None = client["jd_card_url"]
    if not jd_image_url:
        try:
            from services.jd_card_service import generate_jd_card
            jd_image_url = await generate_jd_card(dict(client))
            await conn.execute("UPDATE clients SET jd_card_url=$1 WHERE id=$2", jd_image_url, client["id"])
        except Exception as e:
            raise HTTPException(500, f"JD card generation failed: {e}")

    maps_url = _client_maps_url(dict(client))
    # Form already filled → keep the existing reachout template. Form not yet
    # filled → hl_cand_jd_img_v3. See services/msg91_service.py.
    _existing_tmpl = WT.CANDIDATE_INTENT_IMG
    templates_by_mapping = {
        str(r["id"]): (_existing_tmpl if r["candidate_form_submitted"] else msg91.JD_V3_TEMPLATE)
        for r in rows
    }
    candidates = [
        {
            "phone": msg91._format_phone(r["candidate_phone"]),
            "mapping_id": str(r["id"]),
            "name": (r["candidate_name"] or "").split()[0] if r["candidate_name"] else "",
            "intro": _jd_intro(
                {"form_submitted": r["candidate_form_submitted"], "source": r["candidate_source"]},
                dict(client),
            ),
            "maps_url": maps_url,
            "template_name": templates_by_mapping[str(r["id"])],
        }
        for r in rows
    ]

    await msg91.send_bulk_intent(candidates, components, image_url=jd_image_url)

    sent_ids = [r["id"] for r in rows]
    await conn.execute(
        """
        UPDATE client_candidate_mappings
        SET stage = 'intent_ask'::mapping_stage, wa_sent_at = now(), updated_at = now()
        WHERE id = ANY($1::uuid[])
        """,
        sent_ids,
    )
    for r in rows:
        await conn.execute(
            """
            INSERT INTO whatsapp_messages
                (candidate_id, mapping_id, message_type, direction, phone, status, sent_at, used_template_name)
            VALUES ($1, $2, 'candidate_intent_ask', 'outbound', $3, 'sent', now(), $4)
            """,
            r["candidate_id"], r["id"], msg91._format_phone(r["candidate_phone"]), templates_by_mapping[str(r["id"])],
        )

    return {"data": {"sent": len(sent_ids)}, "ok": True}


# ── RM: hire candidate (place) ────────────────────────────────────────────────

@router.post("/rms/hire/{mapping_id}")
async def rm_hire_candidate(
    mapping_id: str,
    background_tasks: BackgroundTasks,
    conn: asyncpg.Connection = Depends(get_conn),
):
    """RM marks a candidate as placed/hired."""
    import uuid as _uuid, os
    try:
        mid = _uuid.UUID(mapping_id)
    except ValueError:
        raise HTTPException(400, "Invalid mapping ID")

    row = await conn.fetchrow(
        """
        SELECT ccm.id, ccm.stage, ccm.candidate_id, ccm.client_id,
               ca.name AS candidate_name, ca.phone AS candidate_phone,
               cl.company_name, cl.job_title
        FROM client_candidate_mappings ccm
        JOIN candidates ca ON ca.id = ccm.candidate_id
        JOIN clients    cl ON cl.id = ccm.client_id
        WHERE ccm.id = $1
        """,
        mid,
    )
    if not row:
        raise HTTPException(404, "Mapping not found")
    if row["stage"] in ("placed",):
        raise HTTPException(409, "Candidate already placed")

    # Use the existing mappings stage update which handles placement cascade
    from routers.mappings import update_mapping_stage
    from models.mapping import MappingStageUpdate, MappingStage
    from pydantic import BaseModel as _BM

    await conn.execute(
        """
        UPDATE client_candidate_mappings
        SET stage = 'placed'::mapping_stage, placement_confirmed = true, updated_at = now()
        WHERE id = $1
        """,
        mid,
    )

    # Close other active mappings for this candidate
    await conn.execute(
        """
        UPDATE client_candidate_mappings
        SET stage = 'rejected'::mapping_stage, rejection_reason = 'placed_elsewhere', updated_at = now()
        WHERE candidate_id = $1 AND client_id != $2
          AND stage NOT IN ('placed','rejected','not_interested','declined_for_interview','waitlisted')
        """,
        row["candidate_id"], row["client_id"],
    )

    # Mark candidate as hired, deactivate
    import json as _json
    from datetime import datetime, timezone as _tz
    placement_entry = _json.dumps([{
        "client_id": str(row["client_id"]),
        "company_name": row["company_name"],
        "job_title": row["job_title"],
        "placed_at": datetime.now(_tz.utc).isoformat(),
    }])
    await conn.execute(
        """
        UPDATE candidates
        SET evaluation_status = 'hired',
            is_active = false,
            dnd_until = now() + interval '12 months',
            other_details = jsonb_set(
                COALESCE(other_details, '{}'::jsonb),
                '{placements}',
                COALESCE(other_details->'placements', '[]'::jsonb) || $2::jsonb
            ),
            updated_at = now()
        WHERE id = $1
        """,
        row["candidate_id"], placement_entry,
    )

    # Send hire notification to candidate
    async def _notify():
        try:
            import services.msg91_service as msg91
            phone = row["candidate_phone"]
            if phone:
                first_name = (row["candidate_name"] or "").split()[0] or "there"
                await msg91.send_text(
                    phone,
                    f"Hi {first_name}, you've been selected for the role at {row['company_name']}. "
                    f"Our team will contact you shortly with your joining details and next steps.",
                    sender="candidate",
                )
        except Exception as e:
            print(f"[rm/hire] notify failed: {e}")

    background_tasks.add_task(_notify)
    return {"data": {"placed": True}, "ok": True}


# ── RM: reject candidate ───────────────────────────────────────────────────────

class RMRejectBody(BaseModel):
    reason: str = ""


@router.post("/rms/reject/{mapping_id}")
async def rm_reject_candidate(
    mapping_id: str,
    body: RMRejectBody,
    background_tasks: BackgroundTasks,
    conn: asyncpg.Connection = Depends(get_conn),
):
    """RM rejects a candidate and sends them a rejection WA message."""
    import uuid as _uuid, os
    try:
        mid = _uuid.UUID(mapping_id)
    except ValueError:
        raise HTTPException(400, "Invalid mapping ID")

    row = await conn.fetchrow(
        """
        SELECT ccm.id, ccm.stage, ccm.candidate_id, ccm.client_id,
               ca.name AS candidate_name, ca.phone AS candidate_phone,
               cl.company_name
        FROM client_candidate_mappings ccm
        JOIN candidates ca ON ca.id = ccm.candidate_id
        JOIN clients    cl ON cl.id = ccm.client_id
        WHERE ccm.id = $1
        """,
        mid,
    )
    if not row:
        raise HTTPException(404, "Mapping not found")
    if row["stage"] in ("placed", "rejected", "rejected_by_client", "not_interested", "declined_for_interview"):
        raise HTTPException(409, f"Candidate already in terminal stage '{row['stage']}'")

    stage = "rejected_by_client" if row["stage"] in ("client_approval_pending",) else "rejected"
    await conn.execute(
        """
        UPDATE client_candidate_mappings
        SET stage = $1::mapping_stage,
            rejection_reason = $2,
            updated_at = now()
        WHERE id = $3
        """,
        stage, body.reason or "not_selected", mid,
    )

    # Release lock
    await conn.execute(
        """
        UPDATE candidate_locks SET unlocked_at = now(), unlock_reason = 'rejected'
        WHERE candidate_id = $1 AND client_id = $2 AND unlocked_at IS NULL
        """,
        row["candidate_id"], row["client_id"],
    )

    # Send rejection WA
    async def _notify():
        try:
            import services.msg91_service as msg91
            phone = row["candidate_phone"]
            if not phone:
                return
            template = WT.CANDIDATE_NOT_FIT_CLIENT
            if template:
                components = [{"type": "body", "parameters": [
                    {"type": "text", "text": row["candidate_name"] or ""},
                    {"type": "text", "text": row["company_name"] or ""},
                ]}]
                await msg91.send_template(phone, template, "en", components, sender="candidate")
            else:
                first = (row["candidate_name"] or "").split()[0] or "there"
                await msg91.send_text(
                    phone,
                    f"Hi {first}, thank you for your time and interest! "
                    f"After careful review, we weren't able to move forward with {row['company_name']} "
                    f"at this moment. We will keep your profile and reach out when a suitable opportunity comes up. "
                    f"Best of luck! 😊",
                    sender="candidate",
                )
        except Exception as e:
            print(f"[rm/reject] notify failed: {e}")

    background_tasks.add_task(_notify)
    return {"data": {"rejected": True}, "ok": True}


# ── RM: send client portal link ────────────────────────────────────────────────

@router.post("/rms/send-portal-link/{client_id}")
async def rm_send_portal_link(
    client_id: str,
    conn: asyncpg.Connection = Depends(get_conn),
):
    """Send the client portal link to the POC via WhatsApp."""
    import uuid as _uuid, os
    try:
        cid = _uuid.UUID(client_id)
    except ValueError:
        raise HTTPException(400, "Invalid client ID")

    client = await conn.fetchrow(
        "SELECT id, company_name, poc_name, poc_phone FROM clients WHERE id = $1",
        cid,
    )
    if not client:
        raise HTTPException(404, "Client not found")
    if not client["poc_phone"]:
        raise HTTPException(400, "Client has no POC phone number")

    portal_url = os.environ.get("CLIENT_PORTAL_URL", "https://employer.justaccountants.in").rstrip("/")
    poc_name = client["poc_name"] or "there"
    company = client["company_name"] or ""

    import services.msg91_service as msg91
    template = WT.CLIENT_PORTAL_LINK
    try:
        if template:
            components = [{"type": "body", "parameters": [
                {"type": "text", "text": poc_name},
                {"type": "text", "text": portal_url},
            ]}]
            await msg91.send_template(client["poc_phone"], template, "en", components, sender="client")
        else:
            await msg91.send_text(
                client["poc_phone"],
                f"Hi {poc_name}! 👋\n\n"
                f"Your exclusive hiring portal for *{company}* is ready.\n\n"
                f"👉 {portal_url}\n\n"
                f"Here you can:\n"
                f"• View shortlisted candidates\n"
                f"• Approve or decline profiles\n"
                f"• Book interview slots\n\n"
                f"Let me know if you need any help! — Team Hire Lyra",
                sender="client",
            )
    except Exception as e:
        raise HTTPException(500, f"WhatsApp send failed: {e}")

    return {"data": {"sent": True, "portal_url": portal_url}, "ok": True}


# ── Template pool ──────────────────────────────────────────────────────────────

@router.get("/templates")
async def list_templates(conn: asyncpg.Connection = Depends(get_conn)):
    rows = await conn.fetch(
        """
        SELECT
            tp.*,
            COUNT(wm.id)                                                    AS total_sent,
            COUNT(CASE WHEN wm.status = 'delivered' THEN 1 END)            AS delivered,
            COUNT(CASE WHEN wm.status = 'read'      THEN 1 END)            AS read_count
        FROM template_pool tp
        LEFT JOIN whatsapp_messages wm
            ON wm.used_template_name = tp.template_name
        GROUP BY tp.id
        ORDER BY tp.created_at DESC
        """
    )
    return {"data": [dict(r) for r in rows], "count": len(rows), "ok": True}


@router.post("/templates", status_code=201)
async def create_template(
    body: TemplateCreate,
    conn: asyncpg.Connection = Depends(get_conn),
):
    if body.segment not in VALID_SEGMENTS:
        raise HTTPException(400, f"segment must be one of {sorted(VALID_SEGMENTS)}")
    row = await conn.fetchrow(
        """
        INSERT INTO template_pool (template_name, image_url, segment, is_active)
        VALUES ($1, $2, $3, $4) RETURNING *
        """,
        body.template_name, body.image_url, body.segment, body.is_active,
    )
    return {"data": dict(row), "ok": True}


@router.patch("/templates/{template_id}")
async def update_template(
    template_id: uuid.UUID,
    body: TemplateUpdate,
    conn: asyncpg.Connection = Depends(get_conn),
):
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(400, "No fields to update")
    if "segment" in updates and updates["segment"] not in VALID_SEGMENTS:
        raise HTTPException(400, f"segment must be one of {sorted(VALID_SEGMENTS)}")
    keys = list(updates.keys())
    vals = list(updates.values())
    set_clause = ", ".join(f"{k} = ${i+1}" for i, k in enumerate(keys))
    vals.append(template_id)
    row = await conn.fetchrow(
        f"UPDATE template_pool SET {set_clause}, updated_at = now() WHERE id = ${len(vals)} RETURNING *",
        *vals,
    )
    if not row:
        raise HTTPException(404, "Template not found")
    return {"data": dict(row), "ok": True}


@router.delete("/templates/{template_id}")
async def delete_template(
    template_id: uuid.UUID,
    conn: asyncpg.Connection = Depends(get_conn),
):
    result = await conn.execute("DELETE FROM template_pool WHERE id = $1", template_id)
    if result == "DELETE 0":
        raise HTTPException(404, "Template not found")
    return {"ok": True}


# ── Cron schedules ─────────────────────────────────────────────────────────────

@router.get("/cron-schedules")
async def list_cron_schedules(conn: asyncpg.Connection = Depends(get_conn)):
    rows = await conn.fetch("SELECT * FROM cron_schedules ORDER BY segment")
    return {"data": [dict(r) for r in rows], "ok": True}


@router.patch("/cron-schedules/{segment}")
async def update_cron_schedule(
    segment: str,
    body: CronScheduleUpdate,
    conn: asyncpg.Connection = Depends(get_conn),
):
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(400, "No fields to update")
    if "cadence" in updates and updates["cadence"] not in ("daily", "weekly"):
        raise HTTPException(400, "cadence must be 'daily' or 'weekly'")
    keys = list(updates.keys())
    vals = list(updates.values())
    set_clause = ", ".join(f"{k} = ${i+1}" for i, k in enumerate(keys))
    vals.append(segment)
    row = await conn.fetchrow(
        f"UPDATE cron_schedules SET {set_clause}, updated_at = now() WHERE segment = ${len(vals)} RETURNING *",
        *vals,
    )
    if not row:
        raise HTTPException(404, "Segment not found")
    return {"data": dict(row), "ok": True}


@router.post("/cron-schedules/{segment}/trigger")
async def trigger_segment_reachout(
    segment: str,
    conn: asyncpg.Connection = Depends(get_conn),
):
    """Manual trigger — same logic as the cron endpoint."""
    from routers.cron import _run_reachout
    sent, skipped = await _run_reachout(segment, conn)
    return {"data": {"segment": segment, "sent": sent, "skipped": skipped}, "ok": True}


# ── Analytics ──────────────────────────────────────────────────────────────────

@router.get("/analytics")
async def get_analytics(conn: asyncpg.Connection = Depends(get_conn)):
    stage_counts = await conn.fetch(
        """
        SELECT stage::text, COUNT(*) AS count
        FROM clients
        GROUP BY stage
        ORDER BY
            CASE stage::text
                WHEN 'lead'            THEN 1
                WHEN 'scraping'        THEN 2
                WHEN 'scraped'         THEN 3
                WHEN 'reachout_sent'   THEN 4
                WHEN 'interested'      THEN 5
                WHEN 'agreement_sent'  THEN 6
                WHEN 'onboarded'       THEN 7
                WHEN 'disqualified'    THEN 8
                ELSE 9
            END
        """
    )

    segment_counts = await conn.fetch(
        """
        SELECT COALESCE(segment, 'unset') AS segment, COUNT(*) AS count
        FROM clients
        GROUP BY segment
        ORDER BY count DESC
        """
    )

    wa_stats = await conn.fetchrow(
        """
        SELECT
            COUNT(*) FILTER (WHERE direction = 'outbound')              AS total_sent,
            COUNT(*) FILTER (WHERE status = 'delivered')                AS delivered,
            COUNT(*) FILTER (WHERE status = 'read')                     AS read_count,
            COUNT(*) FILTER (WHERE direction = 'inbound')               AS inbound,
            COUNT(*) FILTER (WHERE direction = 'outbound'
                AND created_at >= now() - interval '7 days')            AS sent_last_7d
        FROM whatsapp_messages
        """
    )

    placement_count = await conn.fetchval(
        "SELECT COUNT(*) FROM client_candidate_mappings WHERE stage = 'placed'"
    )

    dnd_count = await conn.fetchval(
        "SELECT COUNT(*) FROM clients WHERE is_dnd = true"
    )

    return {
        "data": {
            "stage_counts":   [dict(r) for r in stage_counts],
            "segment_counts": [dict(r) for r in segment_counts],
            "wa_stats":       dict(wa_stats),
            "placements":     placement_count,
            "dnd_clients":    dnd_count,
        },
        "ok": True,
    }


# ── RM: mark deal won ─────────────────────────────────────────────────────────

# Stages where candidate booked/attended — move to rejected
_INTERVIEW_STAGES = frozenset(["slot_booked", "interview_scheduled", "interview_done"])

# All terminal stages — never touch these
_TERMINAL_STAGES = frozenset([
    "placed", "rejected", "rejected_by_client",
    "not_interested", "declined_for_interview", "client_closed",
])


async def _send_position_closed_msg(phone: str, candidate_name: str, company_name: str):
    """Send the position-closed WhatsApp message to a candidate."""
    import os
    import services.msg91_service as msg91
    template = WT.CANDIDATE_POSITION_CLOSED
    first = (candidate_name or "").split()[0] or "there"
    if template:
        components = [{"type": "body", "parameters": [
            {"type": "text", "text": candidate_name or ""},
            {"type": "text", "text": company_name or ""},
        ]}]
        await msg91.send_template(phone, template, "en", components, sender="candidate")
    else:
        await msg91.send_text(
            phone,
            f"Hi {first}, the position at {company_name} has been filled. "
            f"Don't worry — we will keep sharing more opportunities with you "
            f"as per your requirements. Stay tuned! 😊",
            sender="candidate",
        )


class MarkWonBody(BaseModel):
    fee_amount: Optional[float] = None


@router.post("/rms/mark-won/{client_id}")
async def rm_mark_won(
    client_id: str,
    body: MarkWonBody,
    background_tasks: BackgroundTasks,
    conn: asyncpg.Connection = Depends(get_conn),
):
    """Mark a client deal as won: close all active mappings with correct stages, notify candidates."""
    import uuid as _uuid
    try:
        cid = _uuid.UUID(client_id)
    except ValueError:
        raise HTTPException(400, "Invalid client ID")

    client = await conn.fetchrow(
        "SELECT id, company_name, job_title, stage FROM clients WHERE id = $1", cid
    )
    if not client:
        raise HTTPException(404, "Client not found")
    if client["stage"] == "deal_won":
        raise HTTPException(409, "Deal already marked as won")

    placed_count = await conn.fetchval(
        "SELECT COUNT(*) FROM client_candidate_mappings WHERE client_id = $1 AND stage = 'placed'",
        cid,
    )
    if not placed_count:
        raise HTTPException(400, "No candidate has been placed for this client yet")

    if body.fee_amount is not None:
        await conn.execute(
            "UPDATE clients SET fee_amount = $1, updated_at = now() WHERE id = $2",
            body.fee_amount, cid,
        )

    await conn.execute(
        "UPDATE clients SET stage = 'deal_won'::client_stage, won_at = COALESCE(won_at, now()), updated_at = now() WHERE id = $1",
        cid,
    )

    # Fetch all non-terminal, non-placed active mappings
    active_rows = await conn.fetch(
        """
        SELECT ccm.id AS mapping_id, ccm.stage::text AS stage,
               ca.name AS candidate_name, ca.phone AS candidate_phone
        FROM client_candidate_mappings ccm
        JOIN candidates ca ON ca.id = ccm.candidate_id
        WHERE ccm.client_id = $1
          AND ccm.stage::text NOT IN (
            'placed','rejected','rejected_by_client',
            'not_interested','declined_for_interview','client_closed'
          )
        """,
        cid,
    )

    interview_ids = [r["mapping_id"] for r in active_rows if r["stage"] in _INTERVIEW_STAGES]
    closed_ids    = [r["mapping_id"] for r in active_rows if r["stage"] not in _INTERVIEW_STAGES]

    if interview_ids:
        await conn.execute(
            """
            UPDATE client_candidate_mappings
            SET stage = 'rejected'::mapping_stage,
                rejection_reason = 'position_filled',
                updated_at = now()
            WHERE id = ANY($1::uuid[])
            """,
            interview_ids,
        )

    if closed_ids:
        await conn.execute(
            """
            UPDATE client_candidate_mappings
            SET stage = 'client_closed'::mapping_stage,
                updated_at = now()
            WHERE id = ANY($1::uuid[])
            """,
            closed_ids,
        )

    # Notify all affected candidates (both groups get the same position-closed message)
    async def _notify_all(rows: list, company: str):
        for r in rows:
            if not r["candidate_phone"]:
                continue
            try:
                await _send_position_closed_msg(r["candidate_phone"], r["candidate_name"], company)
            except Exception as e:
                print(f"[rm/mark-won] notify {r['candidate_name']}: {e}")

    if active_rows:
        background_tasks.add_task(_notify_all, list(active_rows), client["company_name"])

    return {
        "data": {
            "won": True,
            "rejected": len(interview_ids),
            "client_closed": len(closed_ids),
        },
        "ok": True,
    }
