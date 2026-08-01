"""
Company-facing client portal API.

"Company" has no dedicated table — it's derived from clients.poc_phone. A company
with N open hiring requirements has N `clients` rows sharing the same poc_phone.
Auth: phone + WhatsApp OTP -> session token, written identically to every `clients`
row sharing that phone, so one login unlocks every hiring requirement for that
company via a single indexed lookup. Mirrors candidate_portal.py's auth pattern.
"""
import os
import re
import random
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Header, BackgroundTasks, Query
from pydantic import BaseModel
from typing import List
import asyncpg
from database import get_conn
from routers.client_portal import (
    build_requirement_workspace,
    invite_candidate_for_client,
    reject_candidate_for_client,
)
import constants.whatsapp_templates as WT

router = APIRouter(prefix="/company-portal", tags=["company-portal"])


def _normalize_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    return digits


# ── Auth ────────────────────────────────────────────────────────────────────

async def get_company_portal_auth(
    authorization: str = Header(...),
    conn: asyncpg.Connection = Depends(get_conn),
) -> str:
    """Returns the authenticated poc_phone — the 'company' identity."""
    token = authorization.removeprefix("Bearer ").strip()
    row = await conn.fetchrow(
        """
        SELECT poc_phone FROM clients
        WHERE poc_session_token = $1 AND poc_session_expires_at > now()
        LIMIT 1
        """,
        token,
    )
    if not row:
        raise HTTPException(401, "Invalid or expired session — please log in again")
    return row["poc_phone"]


class SendOtpBody(BaseModel):
    phone: str


class VerifyOtpBody(BaseModel):
    phone: str
    otp: str


@router.post("/send-otp")
async def send_otp(body: SendOtpBody, conn: asyncpg.Connection = Depends(get_conn)):
    phone = _normalize_phone(body.phone)
    if len(phone) != 10:
        raise HTTPException(400, "Enter a valid 10-digit phone number")

    count = await conn.fetchval("SELECT COUNT(*) FROM clients WHERE poc_phone = $1", phone)
    if not count:
        raise HTTPException(404, "We don't have any hiring requirements on file for this number yet. Please submit your hiring requirement first.")

    otp = str(random.randint(100000, 999999))
    await conn.execute(
        """
        UPDATE clients
        SET poc_otp = $1, poc_otp_expires_at = now() + interval '10 minutes', updated_at = now()
        WHERE poc_phone = $2
        """,
        otp, phone,
    )

    try:
        import services.msg91_service as msg91
        # Reuses the candidate-portal AUTHENTICATION template by default — pass
        # CLIENT_OTP_TEMPLATE if/when a client-branded template is approved in Meta.
        otp_template = WT.CLIENT_OTP
        components = [
            {"type": "body", "parameters": [{"type": "text", "text": otp}]},
            {"type": "button", "sub_type": "url", "index": "0", "parameters": [{"type": "text", "text": otp}]},
        ]
        await msg91.send_template(phone, otp_template, "en", components, sender="client")
    except Exception as e:
        print(f"[company-portal/send-otp] WhatsApp send failed for {phone}: {e}")
        raise HTTPException(500, "Failed to send OTP — please try again")

    return {"ok": True, "message": "OTP sent on WhatsApp"}


@router.post("/verify-otp")
async def verify_otp(body: VerifyOtpBody, conn: asyncpg.Connection = Depends(get_conn)):
    phone = _normalize_phone(body.phone)

    row = await conn.fetchrow(
        "SELECT poc_otp, poc_otp_expires_at FROM clients WHERE poc_phone = $1 LIMIT 1",
        phone,
    )
    if not row:
        raise HTTPException(404, "We don't have any hiring requirements on file for this number yet. Please submit your hiring requirement first.")
    if not row["poc_otp"] or row["poc_otp"] != body.otp.strip():
        raise HTTPException(400, "Incorrect OTP")
    if not row["poc_otp_expires_at"] or row["poc_otp_expires_at"] < datetime.now(timezone.utc):
        raise HTTPException(400, "OTP has expired — request a new one")

    session_token = str(uuid.uuid4())
    await conn.execute(
        """
        UPDATE clients
        SET poc_otp = NULL, poc_otp_expires_at = NULL,
            poc_session_token = $1, poc_session_expires_at = now() + interval '30 days',
            updated_at = now()
        WHERE poc_phone = $2
        """,
        session_token, phone,
    )

    company_name, requirements = await _list_requirements(phone, conn)
    return {
        "ok": True,
        "token": session_token,
        "data": {"company_name": company_name, "requirements": requirements},
    }


# ── Dashboard (Level 1) ───────────────────────────────────────────────────────

def _hiring_progress_label(counts: dict, matching_state: str | None) -> str:
    if counts.get("placed", 0) > 0:
        return "Hired"
    if counts.get("interview_scheduled", 0) or counts.get("slot_booked", 0):
        return "Interviews scheduled"
    if counts.get("client_approval_pending", 0):
        return "Pending your review"
    if counts.get("interested", 0):
        return "Candidates interested"
    if sum(counts.values()) > 0:
        return "Sourcing candidates"
    return "Searching candidates" if matching_state else "Setting up your matchmaking"


async def _list_requirements(phone: str, conn: asyncpg.Connection) -> tuple[str | None, list[dict]]:
    clients_rows = await conn.fetch(
        """
        SELECT id, company_name, job_title, open_positions, stage, matching_state, created_at
        FROM clients
        WHERE poc_phone = $1
        ORDER BY created_at DESC
        """,
        phone,
    )
    if not clients_rows:
        return None, []

    company_name = clients_rows[0]["company_name"]
    client_ids = [r["id"] for r in clients_rows]

    stage_rows = await conn.fetch(
        """
        SELECT client_id, stage::text AS stage, COUNT(*) AS cnt
        FROM client_candidate_mappings
        WHERE client_id = ANY($1::uuid[])
          AND stage NOT IN ('rejected', 'declined_for_interview')
        GROUP BY client_id, stage
        """,
        client_ids,
    )
    counts_by_client: dict = {}
    for r in stage_rows:
        counts_by_client.setdefault(r["client_id"], {})[r["stage"]] = int(r["cnt"])

    requirements = []
    for r in clients_rows:
        counts = counts_by_client.get(r["id"], {})
        requirements.append({
            "id":                   str(r["id"]),
            "job_title":            r["job_title"],
            "open_positions":       r["open_positions"] or 1,
            "stage":                r["stage"],
            "hiring_progress":      _hiring_progress_label(counts, r["matching_state"]),
            "candidates_reached":   sum(counts.values()),
            "interested":           counts.get("interested", 0),
            "pending_review":       counts.get("client_approval_pending", 0),
            "interviews_scheduled": counts.get("interview_scheduled", 0) + counts.get("slot_booked", 0),
            "offers_in_progress":   0,  # reserved — no offer stage exists yet (see Offer & Hiring tab)
            "hires":                counts.get("placed", 0),
        })
    return company_name, requirements


@router.get("/requirements")
async def list_requirements(
    phone: str = Depends(get_company_portal_auth),
    conn: asyncpg.Connection = Depends(get_conn),
):
    company_name, requirements = await _list_requirements(phone, conn)
    return {"data": {"company_name": company_name, "requirements": requirements}, "ok": True}


# ── Workspace (Level 2) ────────────────────────────────────────────────────────

async def get_owned_client(client_id: str, phone: str, conn: asyncpg.Connection) -> dict:
    """Fetch a clients row and verify it belongs to the authenticated company (phone).
    Shared by every requirement-scoped endpoint below."""
    try:
        cid = uuid.UUID(client_id)
    except ValueError:
        raise HTTPException(400, "Invalid requirement ID")
    row = await conn.fetchrow(
        "SELECT id, company_name, job_title, poc_name, poc_phone, available_slots, recruiter_slots, "
        "stage, matching_state, max_salary, job_location, interview_location FROM clients WHERE id = $1",
        cid,
    )
    if not row or row["poc_phone"] != phone:
        raise HTTPException(404, "Hiring requirement not found")
    return dict(row)


@router.get("/requirements/{client_id}")
async def get_requirement_workspace(
    client_id: str,
    phone: str = Depends(get_company_portal_auth),
    conn: asyncpg.Connection = Depends(get_conn),
):
    client = await get_owned_client(client_id, phone, conn)
    payload = await build_requirement_workspace(client, conn)
    return {"data": payload, "ok": True}


# ── Internal admin preview (no OTP session) ─────────────────────────────────
# Mirrors candidate_portal.py's /search + unauthenticated /{id}/jobs pattern —
# gated on the client-portal frontend side by a static ADMIN_KEY, not a
# poc_session_token. Lets staff open a client's real portal view without
# needing that client's phone for OTP.

@router.get("/admin/search")
async def admin_search_clients(
    q: str = Query(..., min_length=2),
    conn: asyncpg.Connection = Depends(get_conn),
):
    pattern = f"%{q}%"
    rows = await conn.fetch(
        """
        SELECT id, company_name, poc_name, poc_phone, job_title
        FROM clients
        WHERE (company_name ILIKE $1 OR poc_name ILIKE $1 OR poc_phone ILIKE $1)
        ORDER BY company_name
        LIMIT 20
        """,
        pattern,
    )
    return {"data": [dict(r) for r in rows], "count": len(rows), "ok": True}


@router.get("/admin/requirements/{client_id}")
async def admin_get_requirement_workspace(
    client_id: str,
    conn: asyncpg.Connection = Depends(get_conn),
):
    try:
        cid = uuid.UUID(client_id)
    except ValueError:
        raise HTTPException(400, "Invalid requirement ID")
    row = await conn.fetchrow(
        "SELECT id, company_name, job_title, poc_name, poc_phone, available_slots, recruiter_slots, "
        "stage, matching_state, max_salary, job_location, interview_location FROM clients WHERE id = $1",
        cid,
    )
    if not row:
        raise HTTPException(404, "Hiring requirement not found")
    payload = await build_requirement_workspace(dict(row), conn)
    return {"data": payload, "ok": True}


# ── Candidate Review actions ───────────────────────────────────────────────────

class RejectCandidateBody(BaseModel):
    reason: str | None = None


class BulkMappingIdsBody(BaseModel):
    mapping_ids: List[str]


class BulkRejectBody(BaseModel):
    mapping_ids: List[str]
    reason: str | None = None


@router.post("/requirements/{client_id}/candidates/{mapping_id}/invite")
async def invite_candidate(
    client_id: str,
    mapping_id: str,
    phone: str = Depends(get_company_portal_auth),
    conn: asyncpg.Connection = Depends(get_conn),
):
    client = await get_owned_client(client_id, phone, conn)
    await invite_candidate_for_client(client, mapping_id, conn)
    return {"data": {"invited": True}, "ok": True}


@router.post("/requirements/{client_id}/candidates/{mapping_id}/reject")
async def reject_candidate(
    client_id: str,
    mapping_id: str,
    body: RejectCandidateBody = RejectCandidateBody(),
    phone: str = Depends(get_company_portal_auth),
    conn: asyncpg.Connection = Depends(get_conn),
):
    client = await get_owned_client(client_id, phone, conn)
    await reject_candidate_for_client(client, mapping_id, body.reason, conn)
    return {"data": {"rejected": True}, "ok": True}


@router.post("/requirements/{client_id}/candidates/bulk-invite")
async def bulk_invite_candidates(
    client_id: str,
    body: BulkMappingIdsBody,
    phone: str = Depends(get_company_portal_auth),
    conn: asyncpg.Connection = Depends(get_conn),
):
    client = await get_owned_client(client_id, phone, conn)
    succeeded, failed = [], []
    for mapping_id in body.mapping_ids:
        try:
            await invite_candidate_for_client(client, mapping_id, conn)
            succeeded.append(mapping_id)
        except HTTPException as e:
            failed.append({"mapping_id": mapping_id, "error": e.detail})
    return {"data": {"invited": succeeded, "failed": failed}, "ok": True}


@router.post("/requirements/{client_id}/candidates/bulk-reject")
async def bulk_reject_candidates(
    client_id: str,
    body: BulkRejectBody,
    phone: str = Depends(get_company_portal_auth),
    conn: asyncpg.Connection = Depends(get_conn),
):
    client = await get_owned_client(client_id, phone, conn)
    succeeded, failed = [], []
    for mapping_id in body.mapping_ids:
        try:
            await reject_candidate_for_client(client, mapping_id, body.reason, conn)
            succeeded.append(mapping_id)
        except HTTPException as e:
            failed.append({"mapping_id": mapping_id, "error": e.detail})
    return {"data": {"rejected": succeeded, "failed": failed}, "ok": True}


class InterviewLocationBody(BaseModel):
    location: str


@router.patch("/requirements/{client_id}/interview-location")
async def set_interview_location(
    client_id: str,
    body: InterviewLocationBody,
    phone: str = Depends(get_company_portal_auth),
    conn: asyncpg.Connection = Depends(get_conn),
):
    client = await get_owned_client(client_id, phone, conn)
    await conn.execute(
        "UPDATE clients SET interview_location = $1, updated_at = now() WHERE id = $2",
        body.location.strip(), client["id"],
    )
    return {"data": {"interview_location": body.location.strip()}, "ok": True}


# ── Interview Management ───────────────────────────────────────────────────────

class InterviewSlotsBody(BaseModel):
    slots: List[dict]  # [{label, iso}] — full replacement list, matches admin's set_recruiter_slots semantics


async def _run_slot_fanout(client_id: uuid.UUID) -> None:
    from database import get_pool
    from routers.matching import on_slots_submitted
    pool = get_pool()
    async with pool.acquire() as fanout_conn:
        try:
            await on_slots_submitted(client_id, fanout_conn)
        except Exception as e:
            print(f"[company-portal/slot_fanout] failed for {client_id}: {e}")


@router.post("/requirements/{client_id}/interview-slots")
async def set_interview_slots(
    client_id: str,
    body: InterviewSlotsBody,
    background_tasks: BackgroundTasks,
    phone: str = Depends(get_company_portal_auth),
    conn: asyncpg.Connection = Depends(get_conn),
):
    """Create or replace the pool of interview-time slots offered to invited candidates."""
    client = await get_owned_client(client_id, phone, conn)
    if 0 < len(body.slots) < 5:
        raise HTTPException(400, "Please provide at least 5 interview slots.")
    slots_with_ids = [
        {"id": s.get("id") or str(uuid.uuid4()), "label": s["label"], "iso": s.get("iso", "")}
        for s in body.slots
    ]
    # Written to available_slots — this is the field routers/candidate_portal.py's
    # pick_slot endpoint actually validates candidate slot picks against
    # (cl.available_slots AS client_slots). recruiter_slots is a separate,
    # older field used by a different RM-facing flow — do not write slots
    # there from the client-portal, candidates will never see them.
    await conn.execute(
        "UPDATE clients SET available_slots = $1, updated_at = now() WHERE id = $2",
        slots_with_ids, client["id"],
    )
    if slots_with_ids:
        background_tasks.add_task(_run_slot_fanout, client["id"])
    return {"data": {"slots": slots_with_ids}, "ok": True}


async def _get_owned_mapping_with_candidate(client: dict, mapping_id: str, conn: asyncpg.Connection) -> dict:
    try:
        mid = uuid.UUID(mapping_id)
    except ValueError:
        raise HTTPException(400, "Invalid mapping ID")
    row = await conn.fetchrow(
        """
        SELECT ccm.id, ccm.stage, ccm.candidate_id,
               c.name AS candidate_name, c.phone AS candidate_phone
        FROM client_candidate_mappings ccm
        JOIN candidates c ON c.id = ccm.candidate_id
        WHERE ccm.id = $1 AND ccm.client_id = $2
        """,
        mid, client["id"],
    )
    if not row:
        raise HTTPException(404, "Candidate not found for this client")
    return dict(row)


@router.post("/requirements/{client_id}/candidates/{mapping_id}/reschedule")
async def reschedule_interview(
    client_id: str,
    mapping_id: str,
    phone: str = Depends(get_company_portal_auth),
    conn: asyncpg.Connection = Depends(get_conn),
):
    """Reset slot state and re-send the slot-selection link so the candidate picks again."""
    client = await get_owned_client(client_id, phone, conn)
    row = await _get_owned_mapping_with_candidate(client, mapping_id, conn)
    if not row["candidate_phone"]:
        raise HTTPException(400, "Candidate has no phone number")

    mid = uuid.UUID(mapping_id)
    await conn.execute(
        """
        UPDATE client_candidate_mappings
        SET slot_booking_sent = false, available_slots = NULL, interview_slot = NULL,
            stage = 'invited_for_interview'::mapping_stage, updated_at = now()
        WHERE id = $1
        """,
        mid,
    )

    from routers.matching import _send_slot_message_to_candidate
    import services.msg91_service as _msg91
    formatted_phone = _msg91._format_phone(row["candidate_phone"])
    sent = await _send_slot_message_to_candidate(
        mid, row["candidate_id"], formatted_phone, row["candidate_name"] or "", client, conn
    )
    if not sent:
        raise HTTPException(500, "Failed to send slot message — please try again")
    return {"data": {"resent": True}, "ok": True}


class CancelInterviewBody(BaseModel):
    reason: str | None = None


@router.post("/requirements/{client_id}/candidates/{mapping_id}/cancel-interview")
async def cancel_interview(
    client_id: str,
    mapping_id: str,
    body: CancelInterviewBody = CancelInterviewBody(),
    phone: str = Depends(get_company_portal_auth),
    conn: asyncpg.Connection = Depends(get_conn),
):
    """Cancel a scheduled/pending interview. Sends the candidate back to
    Candidate Review (client_approval_pending) rather than leaving them in an
    open self-service slot-picker state — the client should explicitly decide
    to re-invite (or reject) rather than the candidate quietly grabbing a new
    slot on their own. Candidate stays locked to this client throughout,
    since client_approval_pending is still an active pipeline stage. Reason
    is optional but stored (client_cancel_reason) so the RM has context."""
    client = await get_owned_client(client_id, phone, conn)
    row = await _get_owned_mapping_with_candidate(client, mapping_id, conn)
    if row["stage"] not in ("slot_sent_candidate", "interview_scheduled", "slot_booked"):
        raise HTTPException(409, f"Candidate is in stage '{row['stage']}' — no interview to cancel")

    mid = uuid.UUID(mapping_id)
    reason = (body.reason or "").strip() or None
    await conn.execute(
        """
        UPDATE client_candidate_mappings
        SET slot_booking_sent = false, available_slots = NULL, interview_slot = NULL,
            stage = 'client_approval_pending'::mapping_stage,
            client_cancel_reason = $2, updated_at = now()
        WHERE id = $1
        """,
        mid, reason,
    )

    if row["candidate_phone"]:
        try:
            import services.msg91_service as msg91
            from services.flow_engine import candidate_portal_deep_link
            portal_url = os.environ.get("CANDIDATE_PORTAL_URL", "https://careers.justaccountants.in").rstrip("/")
            await msg91.send_text(
                row["candidate_phone"],
                f"Hi {row['candidate_name'] or 'there'}, your interview with {client.get('company_name')} "
                f"has been cancelled. We'll reach out if it gets rescheduled. Sorry for the inconvenience! "
                f"You can always check your latest status on our portal: "
                f"{portal_url}/{candidate_portal_deep_link(mid, 'jobs')}",
                sender="candidate",
            )
        except Exception as e:
            print(f"[company-portal/cancel_interview] notify failed for {mid}: {e}")

    return {"data": {"cancelled": True}, "ok": True}


# ── Interview Feedback ─────────────────────────────────────────────────────────

class FeedbackRejectBody(BaseModel):
    reason: str | None = None


@router.post("/requirements/{client_id}/candidates/{mapping_id}/invite-round2")
async def invite_round2(
    client_id: str,
    mapping_id: str,
    phone: str = Depends(get_company_portal_auth),
    conn: asyncpg.Connection = Depends(get_conn),
):
    """Client marked the candidate 'Fit' after round 1 and wants a second round
    (owner/CA deep-dive) instead of hiring outright. Re-runs the exact same
    invite -> slot-pick -> interview pipeline used for round 1 on this same
    mapping row, tagged interview_round=2, after archiving round 1's outcome."""
    client = await get_owned_client(client_id, phone, conn)
    row = await conn.fetchrow(
        """
        SELECT ccm.id, ccm.stage, ccm.candidate_id, ccm.interview_round,
               ccm.interview_slot, ccm.interview_done_at,
               c.name AS candidate_name, c.phone AS candidate_phone
        FROM client_candidate_mappings ccm
        JOIN candidates c ON c.id = ccm.candidate_id
        WHERE ccm.id = $1 AND ccm.client_id = $2
        """,
        uuid.UUID(mapping_id), client["id"],
    )
    if not row:
        raise HTTPException(404, "Candidate not found for this client")
    if row["interview_round"] != 1:
        raise HTTPException(409, "Round 2 has already been used for this candidate")
    if not row["candidate_phone"]:
        raise HTTPException(400, "Candidate has no phone number")

    mid = uuid.UUID(mapping_id)
    history_entry = [{
        "round": 1,
        "interview_slot": row["interview_slot"].isoformat() if row["interview_slot"] else None,
        "interview_done_at": row["interview_done_at"].isoformat() if row["interview_done_at"] else None,
    }]
    await conn.execute(
        """
        UPDATE client_candidate_mappings
        SET slot_booking_sent = false, available_slots = NULL, interview_slot = NULL,
            interview_done = false, interview_round = 2,
            interview_history = COALESCE(interview_history, '[]'::jsonb) || $2::jsonb,
            stage = 'invited_for_interview'::mapping_stage, updated_at = now()
        WHERE id = $1
        """,
        mid, history_entry,
    )

    from routers.matching import _send_slot_message_to_candidate
    import services.msg91_service as _msg91
    formatted_phone = _msg91._format_phone(row["candidate_phone"])
    sent = await _send_slot_message_to_candidate(
        mid, row["candidate_id"], formatted_phone, row["candidate_name"] or "", client, conn
    )
    if not sent:
        raise HTTPException(500, "Failed to send slot message — please try again")
    return {"data": {"invited_round2": True}, "ok": True}


@router.post("/requirements/{client_id}/candidates/{mapping_id}/hire")
async def hire_candidate(
    client_id: str,
    mapping_id: str,
    phone: str = Depends(get_company_portal_auth),
    conn: asyncpg.Connection = Depends(get_conn),
):
    """Client decided to hire after interview. Doesn't request documents
    immediately — first asks the candidate to confirm they're still willing
    to join (join_intent_requested). Only once the candidate accepts (see
    candidate_portal.respond_to_join_intent) do we move to documents_requested
    and collect salary slips/bank statement; the actual placement
    happens later once the candidate accepts the final offer (see
    final_hire / respond-offer)."""
    client = await get_owned_client(client_id, phone, conn)
    row = await _get_owned_mapping_with_candidate(client, mapping_id, conn)
    if row["stage"] in ("placed", "join_intent_requested", "documents_requested", "offer_sent"):
        raise HTTPException(409, f"Candidate is already in stage '{row['stage']}'")

    mid = uuid.UUID(mapping_id)
    await conn.execute(
        """
        UPDATE client_candidate_mappings
        SET stage = 'join_intent_requested'::mapping_stage,
            join_intent_requested_at = COALESCE(join_intent_requested_at, now()),
            updated_at = now()
        WHERE id = $1
        """,
        mid,
    )

    if row["candidate_phone"]:
        try:
            import services.msg91_service as msg91
            from services.flow_engine import candidate_portal_deep_link
            template = WT.CANDIDATE_JOIN_INTENT
            portal_url = os.environ.get("CANDIDATE_PORTAL_URL", "https://careers.justaccountants.in").rstrip("/")
            deep_link = candidate_portal_deep_link(mid, "join-intent")
            if template:
                components = [
                    {"type": "body", "parameters": [
                        {"type": "text", "text": (row["candidate_name"] or "").split()[0] if row["candidate_name"] else "there"},
                        {"type": "text", "text": client.get("company_name") or ""},
                        {"type": "text", "text": client.get("job_title") or ""},
                    ]},
                    {"type": "button", "sub_type": "url", "index": "0", "parameters": [
                        {"type": "text", "text": deep_link},
                    ]},
                ]
                await msg91.send_template(row["candidate_phone"], template, "en", components, sender="candidate")
            else:
                await msg91.send_text(
                    row["candidate_phone"],
                    f"Hi {row['candidate_name'] or 'there'}, great news — {client.get('company_name')} would "
                    f"like to move forward with you for the {client.get('job_title')} role. Please confirm "
                    f"you're still willing to join as soon as possible: {portal_url}/{deep_link}",
                    sender="candidate",
                )
        except Exception as e:
            print(f"[company-portal/hire_candidate] join-intent WA failed for {mid}: {e}")

    return {"data": {"join_intent_requested": True}, "ok": True}


class FinalHireBody(BaseModel):
    offered_salary: float


@router.post("/requirements/{client_id}/candidates/{mapping_id}/final-hire")
async def final_hire(
    client_id: str,
    mapping_id: str,
    body: FinalHireBody,
    phone: str = Depends(get_company_portal_auth),
    conn: asyncpg.Connection = Depends(get_conn),
):
    """Client reviewed the candidate's documents and is ready to make it official.
    Generates the intent letter, moves to offer_sent, and starts the 6h response
    window — the candidate accepts/declines in their own portal (see
    candidate_portal.respond_to_offer), not via WhatsApp buttons."""
    client = await get_owned_client(client_id, phone, conn)
    row = await _get_owned_mapping_with_candidate(client, mapping_id, conn)
    if row["stage"] != "documents_requested":
        raise HTTPException(409, f"Candidate isn't ready for an offer yet (stage: {row['stage']})")
    if not row["candidate_phone"]:
        raise HTTPException(400, "Candidate has no phone number")

    from datetime import datetime as _dt, timezone as _tz
    from services import agreement_service

    letter_html = agreement_service.build_intent_letter_html({
        "company_name": client.get("company_name"),
        "candidate_name": row["candidate_name"],
        "job_title": client.get("job_title"),
        "job_location": client.get("job_location"),
        "offered_salary": body.offered_salary,
        "generated_date": _dt.now(_tz.utc).strftime("%d %b %Y"),
    })
    pdf_bytes = agreement_service.generate_pdf(letter_html)
    safe_candidate = agreement_service._safe(row["candidate_name"] or "candidate")
    safe_company = agreement_service._safe(client.get("company_name") or "company")
    date_str = _dt.now(_tz.utc).strftime("%Y-%m-%d")
    intent_letter_url = agreement_service.upload_to_gcp(
        pdf_bytes, safe_candidate, ext="pdf",
        blob_path=f"intent_letters/{safe_company}/{safe_candidate}-{date_str}.pdf",
    )

    mid = uuid.UUID(mapping_id)
    await conn.execute(
        """
        UPDATE client_candidate_mappings
        SET stage = 'offer_sent'::mapping_stage,
            intent_letter_url = $1, offered_salary = $2,
            offer_issued_at = now(), offer_expires_at = now() + interval '6 hours',
            updated_at = now()
        WHERE id = $3
        """,
        intent_letter_url, body.offered_salary, mid,
    )

    try:
        import services.msg91_service as msg91
        from services.flow_engine import candidate_portal_deep_link
        template = WT.CANDIDATE_OFFER
        salary_str = f"{int(body.offered_salary):,}"
        if template:
            components = [
                {"type": "body", "parameters": [
                    {"type": "text", "text": (row["candidate_name"] or "").split()[0] if row["candidate_name"] else "there"},
                    {"type": "text", "text": client.get("job_title") or ""},
                    {"type": "text", "text": client.get("company_name") or ""},
                    {"type": "text", "text": salary_str},
                ]},
                {"type": "button", "sub_type": "url", "index": "0", "parameters": [
                    {"type": "text", "text": candidate_portal_deep_link(mid, "offer")},
                ]},
            ]
            await msg91.send_template(row["candidate_phone"], template, "en", components, sender="candidate")
        else:
            portal_url = os.environ.get("CANDIDATE_PORTAL_URL", "https://careers.justaccountants.in").rstrip("/")
            await msg91.send_text(
                row["candidate_phone"],
                f"Hi {row['candidate_name'] or 'there'}, you've been offered the {client.get('job_title')} "
                f"role at {client.get('company_name')}. Review and respond within 6 hours: "
                f"{portal_url}/{candidate_portal_deep_link(mid, 'offer')}",
                sender="candidate",
            )
    except Exception as e:
        print(f"[company-portal/final_hire] offer WA failed for {mid}: {e}")

    return {"data": {"offer_sent": True, "intent_letter_url": intent_letter_url}, "ok": True}


@router.post("/requirements/{client_id}/candidates/{mapping_id}/feedback-reject")
async def feedback_reject_candidate(
    client_id: str,
    mapping_id: str,
    body: FeedbackRejectBody = FeedbackRejectBody(),
    phone: str = Depends(get_company_portal_auth),
    conn: asyncpg.Connection = Depends(get_conn),
):
    """Reject a candidate after interview (distinct from the pre-interview /reject
    action). Requires a reason, notifies the candidate, releases their lock."""
    client = await get_owned_client(client_id, phone, conn)
    row = await _get_owned_mapping_with_candidate(client, mapping_id, conn)
    if row["stage"] in ("placed", "rejected"):
        raise HTTPException(409, f"Candidate is already in stage '{row['stage']}'")
    if not body.reason:
        raise HTTPException(400, "A rejection reason is required")

    mid = uuid.UUID(mapping_id)
    await conn.execute(
        """
        UPDATE client_candidate_mappings
        SET feedback_client = 'disliked', feedback_client_reason = $1,
            stage = 'rejected'::mapping_stage, rejection_reason = 'client_disliked', updated_at = now()
        WHERE id = $2
        """,
        body.reason, mid,
    )

    await conn.execute(
        """
        UPDATE candidate_locks cl
        SET unlocked_at = now(), unlock_reason = 'client_rejected'
        FROM client_candidate_mappings ccm
        WHERE ccm.id = $1
          AND cl.candidate_id = ccm.candidate_id
          AND cl.client_id    = ccm.client_id
          AND cl.unlocked_at IS NULL
        """,
        mid,
    )

    if row["candidate_phone"]:
        try:
            import services.msg91_service as msg91
            await msg91.send_text(
                row["candidate_phone"],
                f"Hi {row['candidate_name'] or 'there'}, thank you for appearing for the interview "
                f"with {client.get('company_name')}. Unfortunately, they have decided not to move "
                f"forward at this time. We will reach out as soon as a suitable opportunity "
                f"arises. Best of luck!",
                sender="candidate",
            )
        except Exception as e:
            print(f"[company-portal/feedback_reject] notify failed for {mid}: {e}")

    return {"data": {"rejected": True}, "ok": True}
