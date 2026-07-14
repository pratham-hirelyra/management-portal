import os
import uuid
from fastapi import APIRouter, Depends, HTTPException
import asyncpg
from database import get_conn
from pydantic import BaseModel
from typing import List

router = APIRouter(prefix="/client-portal", tags=["client-portal"])


def _initials(name: str | None) -> str:
    if not name:
        return "?"
    return "".join(p[0].upper() for p in name.strip().split() if p)[:2]


async def _get_client_by_token(token: str, conn: asyncpg.Connection) -> dict:
    try:
        token_uuid = uuid.UUID(token)
    except ValueError:
        raise HTTPException(403, "Invalid portal link")
    row = await conn.fetchrow(
        "SELECT id, company_name, job_title, poc_name, poc_phone, available_slots, recruiter_slots, "
        "stage, matching_state, max_salary FROM clients WHERE feedback_token = $1",
        token_uuid,
    )
    if not row:
        raise HTTPException(403, "Invalid portal link")
    return dict(row)


@router.get("/{token}")
async def get_portal(token: str, conn: asyncpg.Connection = Depends(get_conn)):
    client = await _get_client_by_token(token, conn)
    payload = await build_requirement_workspace(client, conn)
    return {"data": payload, "ok": True}


async def build_requirement_workspace(client: dict, conn: asyncpg.Connection) -> dict:
    """Build the full single-requirement workspace payload (funnel, activity, candidate
    lists) for one `clients` row. Shared by the legacy token-based portal above and the
    new company-portal (routers/company_portal.py), which resolves `client` via session
    auth + ownership check instead of a magic-link token."""
    client_id = client["id"]

    # ── Slot pool ──────────────────────────────────────────────────────────
    # available_slots is the field candidate_portal.py's pick_slot endpoint
    # actually validates candidate picks against, so it's the field the
    # client-portal's slot picker reads/writes as the current pool.
    # recruiter_slots is a separate, older RM-facing field — checked here
    # only so a client's existing legacy pool still unlocks the Feedback tab.
    recruiter_slots = client["available_slots"] or []
    legacy_recruiter_slots = client["recruiter_slots"] or []
    has_recruiter_slots = bool(recruiter_slots) or bool(legacy_recruiter_slots)
    slot_labels = [s.get("label", "") for s in recruiter_slots if s.get("label")]

    # ── Overview stats ────────────────────────────────────────────────────
    stage_counts = await conn.fetch(
        """
        SELECT stage::text AS stage, COUNT(*) AS cnt
        FROM client_candidate_mappings
        WHERE client_id = $1
          AND stage NOT IN ('rejected','declined_for_interview')
        GROUP BY stage
        """,
        client_id,
    )
    counts_map = {r["stage"]: int(r["cnt"]) for r in stage_counts}

    # Funnel stats — JD sent, replied, passed screening
    funnel_row = await conn.fetchrow(
        """
        SELECT
            COUNT(*)                                                           AS total,
            COUNT(*) FILTER (WHERE wa_sent_at IS NOT NULL)                    AS jd_sent,
            COUNT(*) FILTER (WHERE intent_received_at IS NOT NULL)            AS replied
        FROM client_candidate_mappings
        WHERE client_id = $1
        """,
        client_id,
    )
    passed_screen = await conn.fetchval(
        "SELECT COUNT(DISTINCT candidate_id) FROM mms_scores WHERE client_id = $1 AND filter_passed = true",
        client_id,
    ) or 0

    overview = {
        "sourced":             int(funnel_row["total"] or 0),
        "jd_sent":             int(funnel_row["jd_sent"] or 0),
        "replied":             int(funnel_row["replied"] or 0),
        "passed_screen":       int(passed_screen),
        "interested":          counts_map.get("interested", 0),
        "in_review":           counts_map.get("client_approval_pending", 0),
        "invited":             counts_map.get("invited_for_interview", 0),
        "interview_scheduled": counts_map.get("interview_scheduled", 0) + counts_map.get("slot_booked", 0),
        "interview_done":      counts_map.get("interview_done", 0),
        "placed":              counts_map.get("placed", 0),
    }

    # ── Activity feed ─────────────────────────────────────────────────────
    activity_rows = await conn.fetch(
        """
        SELECT event_type, event_at FROM (
            SELECT 'sourced'::text          AS event_type, created_at         AS event_at
            FROM client_candidate_mappings WHERE client_id = $1

            UNION ALL

            SELECT 'jd_sent'::text,         wa_sent_at
            FROM client_candidate_mappings WHERE client_id = $1 AND wa_sent_at IS NOT NULL

            UNION ALL

            SELECT 'interested'::text,      intent_received_at
            FROM client_candidate_mappings WHERE client_id = $1 AND intent_received_at IS NOT NULL

            UNION ALL

            SELECT 'shortlisted'::text,     updated_at
            FROM client_candidate_mappings WHERE client_id = $1 AND stage::text = 'client_approval_pending'

            UNION ALL

            SELECT 'invited'::text,         updated_at
            FROM client_candidate_mappings WHERE client_id = $1 AND stage::text = 'invited_for_interview'

            UNION ALL

            SELECT 'interview_done'::text,  interview_done_at
            FROM client_candidate_mappings
            WHERE client_id = $1 AND interview_done = true AND interview_done_at IS NOT NULL

            UNION ALL

            SELECT 'placed'::text,          updated_at
            FROM client_candidate_mappings WHERE client_id = $1 AND stage::text = 'placed'
        ) t
        ORDER BY event_at DESC
        LIMIT 10
        """,
        client_id,
    )
    activity_events = [
        {"type": r["event_type"], "at": r["event_at"].isoformat()}
        for r in activity_rows
    ]

    # ── Review Candidates tab (pre-interview approval) ─────────────────────
    approval_rows = await conn.fetch(
        """
        SELECT
            ccm.id AS mapping_id, ccm.stage,
            c.id AS candidate_id, c.name AS candidate_name,
            c.age_years, c.current_location, c.current_salary,
            c.technical_evaluation_score, c.cv_url, c.final_evaluation_report_url,
            c.photo_url, c.labels, c.other_details, c.notice_period,
            ms.mms_score
        FROM client_candidate_mappings ccm
        JOIN candidates c ON c.id = ccm.candidate_id
        LEFT JOIN mms_scores ms ON ms.candidate_id = c.id AND ms.client_id = ccm.client_id
        WHERE ccm.client_id = $1
          AND ccm.stage = 'client_approval_pending'::mapping_stage
        ORDER BY COALESCE(ms.mms_score, 0) DESC
        """,
        client_id,
    )

    approval_candidates = []
    for r in approval_rows:
        r = dict(r)
        od = r.get("other_details") or {}
        if isinstance(od, str):
            import json as _j
            try: od = _j.loads(od)
            except Exception: od = {}

        tech = float(r.get("technical_evaluation_score") or 0)
        labels = r.get("labels") or []
        _sl = {"High Stability", "Average Stability", "High Turnover Risk"}
        stability = next((l for l in labels if l in _sl), "")

        approval_candidates.append({
            "mapping_id":          str(r["mapping_id"]),
            "stage":               r["stage"],
            "candidate_id":        str(r["candidate_id"]),
            "name":                r["candidate_name"],
            "initials":            _initials(r["candidate_name"]),
            "age_years":           r["age_years"],
            "current_location":    r["current_location"],
            "current_salary":      r["current_salary"],
            "ai_technical_score":  tech,
            "job_stability":       stability,
            "accounting_software": od.get("tools"),
            "notice_period":       r.get("notice_period"),
            "cv_url":              r["cv_url"],
            "evaluation_report_url": r["final_evaluation_report_url"],
            "photo_url":           r.get("photo_url"),
            "labels":              labels,
        })

    # ── Invited candidates (invited + slot link sent, awaiting booking) ──
    invited_rows = await conn.fetch(
        """
        SELECT
            ccm.id AS mapping_id, ccm.stage::text AS stage, ccm.slot_sent_at,
            ccm.interview_round, c.name AS candidate_name, c.photo_url
        FROM client_candidate_mappings ccm
        JOIN candidates c ON c.id = ccm.candidate_id
        WHERE ccm.client_id = $1
          AND ccm.stage IN (
            'invited_for_interview'::mapping_stage,
            'slot_sent_candidate'::mapping_stage
          )
        ORDER BY ccm.updated_at DESC
        """,
        client_id,
    )
    invited_candidates = [
        {
            "mapping_id":  str(r["mapping_id"]),
            "name":        r["candidate_name"],
            "initials":    _initials(r["candidate_name"]),
            "photo_url":   r["photo_url"],
            "stage":       r["stage"],
            "slot_sent_at": r["slot_sent_at"].isoformat() if r["slot_sent_at"] else None,
            "interview_round": r.get("interview_round") or 1,
        }
        for r in invited_rows
    ]

    # ── Interview Feedback tab (post-interview) ────────────────────────────
    max_salary = float(client.get("max_salary") or 0)

    feedback_rows = await conn.fetch(
        """
        SELECT
            ccm.id AS mapping_id, ccm.stage, ccm.interview_slot,
            ccm.available_slots, ccm.feedback_client, ccm.feedback_client_reason,
            ccm.interview_done, ccm.interview_round, ccm.documents,
            ccm.intent_letter_url, ccm.offered_salary, ccm.offer_expires_at,
            c.id AS candidate_id, c.name AS candidate_name,
            c.age_years, c.current_location, c.current_salary, c.expected_salary,
            c.technical_evaluation_score, c.cv_url, c.final_evaluation_report_url,
            c.photo_url, c.labels, c.other_details, c.notice_period,
            ms.mms_score, ms.bts
        FROM client_candidate_mappings ccm
        JOIN candidates c ON c.id = ccm.candidate_id
        LEFT JOIN mms_scores ms ON ms.candidate_id = c.id AND ms.client_id = ccm.client_id
        WHERE ccm.client_id = $1
          AND ccm.stage IN ('slot_booked', 'interview_scheduled', 'interview_done',
                             'join_intent_requested', 'documents_requested', 'offer_sent',
                             'placed', 'rejected')
          AND (ccm.stage != 'rejected' OR ccm.interview_done = true OR ccm.interview_slot IS NOT NULL)
        ORDER BY ccm.interview_slot ASC NULLS LAST
        """,
        client_id,
    )

    feedback_candidates = []
    for r in feedback_rows:
        r = dict(r)
        od = r.get("other_details") or {}
        if isinstance(od, str):
            import json as _j
            try: od = _j.loads(od)
            except Exception: od = {}

        tech = float(r.get("technical_evaluation_score") or 0)
        labels = r.get("labels") or []
        _sl = {"High Stability", "Average Stability", "High Turnover Risk"}
        stability = next((l for l in labels if l in _sl), "")

        av = r.get("available_slots")
        if isinstance(av, str):
            import json as _jj
            try: av = _jj.loads(av)
            except Exception: av = []
        slot_label = av[0]["label"] if av and isinstance(av, list) and av[0].get("label") else None

        feedback_candidates.append({
            "mapping_id":            str(r["mapping_id"]),
            "stage":                 r["stage"],
            "interview_slot":        r["interview_slot"].isoformat() if r["interview_slot"] else None,
            "interview_slot_label":  slot_label,
            "interview_done":        r["interview_done"],
            "interview_round":       r.get("interview_round") or 1,
            "documents":             r.get("documents") or {},
            "intent_letter_url":     r.get("intent_letter_url"),
            "offered_salary":        r.get("offered_salary"),
            "offer_expires_at":      r["offer_expires_at"].isoformat() if r.get("offer_expires_at") else None,
            "feedback":              r["feedback_client"],
            "feedback_reason":       r["feedback_client_reason"],
            "candidate_id":          str(r["candidate_id"]),
            "name":                  r["candidate_name"],
            "initials":              _initials(r["candidate_name"]),
            "age_years":             r["age_years"],
            "current_location":      r["current_location"],
            "current_salary":        r["current_salary"],
            "expected_salary":       r.get("expected_salary"),
            "salary_vs_budget":      max_salary,
            "mms_score_pct":         round(float(r["mms_score"] or 0), 1),
            "ai_technical_score":    tech,
            "job_stability":         stability,
            "accounting_software":   od.get("tools"),
            "notice_period":         r.get("notice_period"),
            "cv_url":                r["cv_url"],
            "evaluation_report_url": r["final_evaluation_report_url"],
            "photo_url":             r.get("photo_url"),
            "labels":                labels,
        })

    # Determine active tab hint — feedback tab unlocks once slots exist
    if has_recruiter_slots:
        active_tab = "feedback"
    elif approval_candidates:
        active_tab = "review"
    else:
        active_tab = "overview"

    return {
        "client": {
            "id":                  str(client_id),
            "company_name":        client["company_name"],
            "job_title":           client["job_title"],
            "poc_name":            client["poc_name"],
            "stage":               client["stage"],
            "matching_state":      client["matching_state"],
            "job_location":        client.get("job_location"),
            "interview_location":  client.get("interview_location"),
        },
        "has_recruiter_slots":  has_recruiter_slots,
        "slot_labels":          slot_labels,
        "recruiter_slots":      recruiter_slots,
        "active_tab":           active_tab,
        "overview":             overview,
        "activity_events":      activity_events,
        "approval_candidates":  approval_candidates,
        "invited_candidates":   invited_candidates,
        "feedback_candidates":  feedback_candidates,
    }


@router.post("/{token}/invite/{mapping_id}")
async def invite_candidate(
    token: str,
    mapping_id: str,
    conn: asyncpg.Connection = Depends(get_conn),
):
    client = await _get_client_by_token(token, conn)
    await invite_candidate_for_client(client, mapping_id, conn)
    return {"data": {"invited": True}, "ok": True}


async def invite_candidate_for_client(client: dict, mapping_id: str, conn: asyncpg.Connection) -> None:
    """Approve candidate for interview: send slot-selection link, advance stage.
    Shared by the legacy token portal and the new company-portal."""
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
    if row["stage"] != "client_approval_pending":
        raise HTTPException(409, f"Candidate is in stage '{row['stage']}', not pending approval")

    await conn.execute(
        """
        UPDATE client_candidate_mappings
        SET stage = 'invited_for_interview'::mapping_stage, updated_at = now()
        WHERE id = $1 AND stage = 'client_approval_pending'::mapping_stage
        """,
        mid,
    )

    # Send slot selection message to candidate immediately
    from routers.matching import _send_slot_message_to_candidate
    await _send_slot_message_to_candidate(
        mid,
        row["candidate_id"],
        row["candidate_phone"] or "",
        row["candidate_name"] or "",
        client,
        conn,
    )


class RejectBody(BaseModel):
    reason: str | None = None


@router.post("/{token}/reject/{mapping_id}")
async def reject_candidate(
    token: str,
    mapping_id: str,
    body: RejectBody = RejectBody(),
    conn: asyncpg.Connection = Depends(get_conn),
):
    client = await _get_client_by_token(token, conn)
    await reject_candidate_for_client(client, mapping_id, body.reason, conn)
    return {"data": {"rejected": True}, "ok": True}


async def reject_candidate_for_client(
    client: dict, mapping_id: str, reason: str | None, conn: asyncpg.Connection
) -> None:
    """Reject candidate before interview: send polite rejection WA, close mapping.
    Shared by the legacy token portal and the new company-portal."""
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
    if row["stage"] != "client_approval_pending":
        raise HTTPException(409, f"Candidate is in stage '{row['stage']}', not pending approval")

    await conn.execute(
        """
        UPDATE client_candidate_mappings
        SET stage = 'declined_for_interview'::mapping_stage,
            rejection_reason = $2,
            updated_at = now()
        WHERE id = $1
        """,
        mid, reason or "client_not_selected_for_interview",
    )

    # Release lock so candidate can be matched elsewhere
    await conn.execute(
        """
        UPDATE candidate_locks cl
        SET unlocked_at = now(), unlock_reason = 'client_not_selected'
        FROM client_candidate_mappings ccm
        WHERE ccm.id = $1
          AND cl.candidate_id = ccm.candidate_id
          AND cl.client_id    = ccm.client_id
          AND cl.unlocked_at IS NULL
        """,
        mid,
    )

    # Send polite rejection WA to candidate
    if row["candidate_phone"]:
        try:
            import services.msg91_service as msg91
            template = os.environ.get("CANDIDATE_NOT_FIT_CLIENT_TEMPLATE", "").strip()
            if template:
                components = [{"type": "body", "parameters": [
                    {"type": "text", "text": row["candidate_name"] or ""},
                    {"type": "text", "text": client["company_name"] or ""},
                ]}]
                await msg91.send_template(
                    row["candidate_phone"], template, "en", components, sender="candidate"
                )
            else:
                await msg91.send_text(
                    row["candidate_phone"],
                    f"Hi {row['candidate_name'] or 'there'}, thank you for your interest! "
                    f"After reviewing your profile, we feel you may not be the right fit for "
                    f"{client['company_name']} at this time. We'll keep your profile and reach "
                    f"out as soon as a more suitable opportunity comes up. Best of luck! 😊",
                    sender="candidate",
                )
        except Exception as e:
            print(f"[client_portal] rejection WA failed for {mid}: {e}")


# ── Slot booking by token (same as /schedule/:clientId/book but uses feedback_token) ──

class SlotBookingRequest(BaseModel):
    client_name: str
    slots: List[str]


@router.post("/{token}/submit-slots")
async def submit_slots(
    token: str,
    body: SlotBookingRequest,
    conn: asyncpg.Connection = Depends(get_conn),
):
    """Book interview slots from the portal page (uses feedback_token)."""
    from datetime import datetime, timezone

    client = await _get_client_by_token(token, conn)

    if not body.slots:
        raise HTTPException(400, "No slots provided")
    if len(body.slots) < 4:
        raise HTTPException(400, "Please provide at least 4 time slots")

    if client["available_slots"]:
        raise HTTPException(409, "Slots have already been submitted.")

    bookings = [
        {
            "label": slot,
            "client_name": body.client_name,
            "booked_at": datetime.now(timezone.utc).isoformat(),
        }
        for slot in body.slots
    ]

    await conn.execute(
        "UPDATE clients SET available_slots = $1, updated_at = now() WHERE id = $2",
        bookings, client["id"],
    )

    # Confirmation WA
    if client["poc_phone"]:
        try:
            import services.msg91_service as msg91
            slot_lines = "\n".join(f"  • {s}" for s in body.slots)
            template = os.environ.get("CLIENT_SLOTS_RECEIVED_TEMPLATE", "").strip()
            if template:
                components = [{"type": "body", "parameters": [
                    {"type": "text", "text": slot_lines},
                ]}]
                await msg91.send_template(client["poc_phone"], template, "en", components, sender="client")
            else:
                await msg91.send_text(
                    client["poc_phone"],
                    f"Thank you for sharing your availability! ✅\n\nYour preferred times:\n{slot_lines}\n\n"
                    f"We will review the available candidates and share their profiles with you shortly.",
                    sender="client",
                )
        except Exception as e:
            print(f"[client_portal/submit-slots] confirmation WA failed: {e}")

    # Fan out to candidates
    try:
        from routers.matching import on_slots_submitted
        await on_slots_submitted(client["id"], conn)
    except Exception as e:
        print(f"[client_portal/submit-slots] on_slots_submitted failed: {e}")

    return {"data": {"booked": len(bookings)}, "ok": True}
