"""
Customer Executive calling system.

Deliberately separate from the RM (Relationship Manager) system — RM
assignment is permanent and post-onboarding (see rm_service.py); CE
assignment is a daily call queue with shift/reschedule semantics, scoped to
pre-onboarding leads.

Pool (top_end only):
  (clients.segment = 'employer_lead' AND clients.stage = 'interested')
  OR clients.segment = 'active_job_post'
  excluding stage IN ('onboarded', 'disqualified', 'churned')
  AND created_at >= ce_service._ELIGIBLE_SINCE

Shared, claim-based queue — ce_service.sweep_assign just tops up a shared
unclaimed pool (ce_id=NULL); any active top_end CE can see and claim from
it (see the /claim endpoint below). Mirrors the bottom_end ticket desk's
model, not rm_service.assign_rm's auto-pick-a-specific-CE style.
daily_target is a visible daily minimum target (claimed-today vs. target),
not an assignment cap — claiming isn't capped.
"""
import re
import uuid
import json as _json
from datetime import date
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
import asyncpg
from database import get_conn

router = APIRouter(tags=["customer-executives"])


# ── Request models ───────────────────────────────────────────────────────────

class CECreate(BaseModel):
    name: str
    phone: Optional[str] = None
    pin: Optional[str] = None
    tier: str = "top_end"
    daily_target: int = 20
    is_active: bool = True


class CEUpdate(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    pin: Optional[str] = None
    tier: Optional[str] = None
    daily_target: Optional[int] = None
    is_active: Optional[bool] = None


class CEVerifyPin(BaseModel):
    pin: str


class MarkCalledBody(BaseModel):
    sentiment: str  # 'positive' | 'negative' | 'wrong_poc' | 'wrong_company'
    comment: Optional[str] = None


class UpdatePocPhoneBody(BaseModel):
    new_poc_phone: str


class ShiftBody(BaseModel):
    call_date: date  # ISO date, e.g. "2026-08-01"


# ── Admin CRUD ────────────────────────────────────────────────────────────────

@router.get("/admin/ces")
async def list_ces(conn: asyncpg.Connection = Depends(get_conn)):
    rows = await conn.fetch(
        """
        SELECT
            ce.*,
            COUNT(a.id) FILTER (WHERE a.status = 'pending' AND a.call_date <= CURRENT_DATE) AS today_pending,
            COUNT(a.id) FILTER (WHERE a.status = 'called') AS total_called,
            COUNT(a.id) FILTER (WHERE a.sentiment = 'positive') AS positive,
            COUNT(a.id) FILTER (WHERE a.sentiment = 'negative') AS negative,
            COUNT(a.id) FILTER (WHERE a.sentiment = 'wrong_poc') AS wrong_poc,
            COUNT(a.id) FILTER (WHERE a.sentiment = 'wrong_company') AS wrong_company,
            COUNT(a.id) FILTER (WHERE cl.stage::text = 'onboarded') AS onboarded,
            COUNT(a.id) FILTER (WHERE a.claimed_at::date = CURRENT_DATE) AS claimed_today,
            -- Scalar subqueries, not another JOIN — a direct join against
            -- tickets would fan out against the ce_assignments join above
            -- and corrupt every COUNT(a.id) aggregate.
            (SELECT COUNT(*) FROM tickets t WHERE t.claimed_by = ce.id AND t.status <> 'RESOLVED') AS open_tickets,
            (SELECT COUNT(*) FROM tickets t WHERE t.claimed_by = ce.id AND t.status = 'RESOLVED') AS resolved_tickets
        FROM customer_executives ce
        LEFT JOIN ce_assignments a ON a.ce_id = ce.id
        LEFT JOIN clients cl ON cl.id = a.client_id
        GROUP BY ce.id
        ORDER BY ce.name
        """
    )
    return {"data": [dict(r) for r in rows], "count": len(rows), "ok": True}


@router.post("/admin/ces", status_code=201)
async def create_ce(body: CECreate, conn: asyncpg.Connection = Depends(get_conn)):
    if body.tier not in ("top_end", "bottom_end"):
        raise HTTPException(400, "tier must be 'top_end' or 'bottom_end'")
    row = await conn.fetchrow(
        """
        INSERT INTO customer_executives (name, phone, pin, tier, daily_target, is_active)
        VALUES ($1, $2, $3, $4, $5, $6) RETURNING *
        """,
        body.name, body.phone, body.pin, body.tier, body.daily_target, body.is_active,
    )
    return {"data": dict(row), "ok": True}


@router.patch("/admin/ces/{ce_id}")
async def update_ce(ce_id: uuid.UUID, body: CEUpdate, conn: asyncpg.Connection = Depends(get_conn)):
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(400, "No fields to update")
    if "tier" in updates and updates["tier"] not in ("top_end", "bottom_end"):
        raise HTTPException(400, "tier must be 'top_end' or 'bottom_end'")
    keys = list(updates.keys())
    vals = list(updates.values())
    set_clause = ", ".join(f"{k} = ${i+1}" for i, k in enumerate(keys))
    vals.append(ce_id)
    row = await conn.fetchrow(
        f"UPDATE customer_executives SET {set_clause}, updated_at = now() WHERE id = ${len(vals)} RETURNING *",
        *vals,
    )
    if not row:
        raise HTTPException(404, "Customer executive not found")
    return {"data": dict(row), "ok": True}


@router.delete("/admin/ces/{ce_id}")
async def delete_ce(ce_id: uuid.UUID, conn: asyncpg.Connection = Depends(get_conn)):
    try:
        result = await conn.execute("DELETE FROM customer_executives WHERE id = $1", ce_id)
    except asyncpg.ForeignKeyViolationError:
        raise HTTPException(
            409,
            "This executive has call history or an active queue — reassign their pending "
            "clients first (see /reassign), then deactivate instead of deleting to keep "
            "their call history intact.",
        )
    if result == "DELETE 0":
        raise HTTPException(404, "Customer executive not found")
    return {"ok": True}


@router.post("/admin/ces/{ce_id}/release-pending")
async def release_pending(
    ce_id: uuid.UUID,
    conn: asyncpg.Connection = Depends(get_conn),
):
    """Release all of this CE's still-uncalled claims back to the shared pool
    (ce_id=NULL) — e.g. when someone leaves, so their unworked claims don't
    just sit orphaned. Any active top_end CE can claim them from there.
    Called assignments (history) are never touched — they stay permanently
    attributed to whoever actually made the call."""
    rows = await conn.fetch(
        """
        UPDATE ce_assignments
        SET ce_id = NULL, claimed_at = NULL, updated_at = now()
        WHERE ce_id = $1 AND status = 'pending'
        RETURNING id
        """,
        ce_id,
    )
    return {"data": {"released": len(rows)}, "ok": True}


@router.post("/admin/ces/{ce_id}/verify-pin")
async def verify_ce_pin(ce_id: uuid.UUID, body: CEVerifyPin, conn: asyncpg.Connection = Depends(get_conn)):
    row = await conn.fetchrow("SELECT pin FROM customer_executives WHERE id = $1", ce_id)
    if not row:
        raise HTTPException(404, "Customer executive not found")
    if not row["pin"]:
        raise HTTPException(403, "No PIN set for this executive")
    if row["pin"] != body.pin:
        raise HTTPException(401, "Incorrect PIN")
    return {"ok": True}


# ── Executive-facing ──────────────────────────────────────────────────────────

_ASSIGNMENT_SELECT = """
    SELECT
        a.id AS assignment_id, a.client_id, a.call_date, a.status, a.sentiment,
        a.comment, a.called_at, a.assigned_at, a.no_pickup_count, a.last_attempt_at,
        cl.company_name, cl.poc_name, cl.poc_phone, cl.job_title, cl.stage::text AS client_stage,
        cl.segment, cl.industry, COALESCE(cl.job_location, cl.location) AS location,
        CASE WHEN jsonb_typeof(COALESCE(cl.phone_numbers, '[]'::jsonb)) = 'array'
             THEN COALESCE(cl.phone_numbers, '[]'::jsonb) ELSE '[]'::jsonb END AS phone_numbers,
        CASE WHEN jsonb_typeof(COALESCE(cl.dnd_phones, '[]'::jsonb)) = 'array'
             THEN COALESCE(cl.dnd_phones, '[]'::jsonb) ELSE '[]'::jsonb END AS dnd_phones,
        GREATEST(
            cl.last_outreach_at,
            (SELECT MAX(wm.created_at) FROM whatsapp_messages wm
             WHERE cl.poc_phone IS NOT NULL AND RIGHT(wm.phone, 10) = RIGHT(cl.poc_phone, 10))
        ) AS last_engagement_at
    FROM ce_assignments a
    JOIN clients cl ON cl.id = a.client_id
"""


@router.get("/ce/{ce_id}/me")
async def get_ce_info(ce_id: uuid.UUID, conn: asyncpg.Connection = Depends(get_conn)):
    row = await conn.fetchrow(
        "SELECT id, name, phone, tier, daily_target, is_active FROM customer_executives WHERE id = $1",
        ce_id,
    )
    if not row:
        raise HTTPException(404, "Customer executive not found")
    return {"data": dict(row), "ok": True}


@router.get("/ce/{ce_id}/queue")
async def get_ce_queue(
    ce_id: uuid.UUID, view: str = "unclaimed", conn: asyncpg.Connection = Depends(get_conn),
):
    """
    view='unclaimed' (default): the shared pool — every active top_end CE
    sees the same list, ordered by a fixed lead-quality tier (Job Post
    Interested > Employer Lead Interested > CA Network Interested > Job Post
    Reachout Sent), then oldest-added-to-pool first within each tier.
    view='mine': assignments this CE has already claimed and not yet called,
    whose call_date has arrived (today or overdue — overdue is the carry-over
    from days they weren't called). Ordered so a no-pickup retry sinks behind
    anyone not yet attempted today (see /no-pickup).
    """
    if view == "mine":
        rows = await conn.fetch(
            _ASSIGNMENT_SELECT + """
            WHERE a.ce_id = $1 AND a.status = 'pending' AND a.call_date <= CURRENT_DATE
            ORDER BY a.call_date ASC, COALESCE(a.last_attempt_at, a.assigned_at) ASC
            """,
            ce_id,
        )
    else:
        rows = await conn.fetch(
            _ASSIGNMENT_SELECT + """
            WHERE a.ce_id IS NULL AND a.status = 'pending' AND a.call_date <= CURRENT_DATE
            ORDER BY
                CASE
                    WHEN cl.segment = 'active_job_post' AND cl.stage::text = 'interested'    THEN 1
                    WHEN cl.segment = 'employer_lead'   AND cl.stage::text = 'interested'    THEN 2
                    WHEN cl.segment = 'ca_network'       AND cl.stage::text = 'interested'    THEN 3
                    WHEN cl.segment = 'active_job_post' AND cl.stage::text = 'reachout_sent' THEN 4
                    ELSE 5
                END,
                a.assigned_at ASC
            """
        )
    return {"data": [dict(r) for r in rows], "count": len(rows), "ok": True}


@router.post("/ce/{ce_id}/assignments/{assignment_id}/claim")
async def claim_assignment(
    ce_id: uuid.UUID, assignment_id: uuid.UUID, conn: asyncpg.Connection = Depends(get_conn),
):
    ce = await conn.fetchrow("SELECT tier, is_active FROM customer_executives WHERE id = $1", ce_id)
    if not ce or ce["tier"] != "top_end" or not ce["is_active"]:
        raise HTTPException(403, "Not an active top_end executive")

    row = await conn.fetchrow(
        """
        UPDATE ce_assignments SET ce_id = $1, claimed_at = now(), updated_at = now()
        WHERE id = $2 AND ce_id IS NULL
        RETURNING *
        """,
        ce_id, assignment_id,
    )
    if not row:
        raise HTTPException(409, "Already claimed")
    return {"data": dict(row), "ok": True}


@router.post("/ce/{ce_id}/assignments/claim-batch")
async def claim_batch(ce_id: uuid.UUID, conn: asyncpg.Connection = Depends(get_conn)):
    """
    One click, no manual picking: claims the top daily_target unclaimed pool
    rows in one shot, by the same lead-quality tier order as the unclaimed
    list (Job Post Interested > Employer Lead Interested > CA Network
    Interested > Job Post Reachout Sent), oldest-added-to-pool first within
    each tier. daily_target is a per-click batch size here, not a ceiling —
    nothing stops a CE from clicking again (or using the per-row Claim
    button) to take on more once they've already hit their target for the
    day; there's no cap check against how much they've already claimed.

    Race-safety: this is a single atomic statement using
    `FOR UPDATE SKIP LOCKED` — the standard "grab N items off a shared queue"
    pattern. If two CEs hit this at the same instant, each transaction locks
    and claims a disjoint set of rows; whichever row a concurrent transaction
    has already locked is simply skipped rather than waited on or double-
    claimed. This is why batch claiming is NOT implemented as "pick N ids
    client-side, then fire N individual /claim calls" — two CEs' frontends
    could both see the same top-N unclaimed rows before either claims
    anything, both attempt the same ids, and whoever loses the per-row race
    would silently end up with fewer than their batch. Doing the selection
    and the claim in one server-side statement removes that gap entirely.
    """
    ce = await conn.fetchrow("SELECT daily_target, tier, is_active FROM customer_executives WHERE id = $1", ce_id)
    if not ce or ce["tier"] != "top_end" or not ce["is_active"]:
        raise HTTPException(403, "Not an active top_end executive")

    rows = await conn.fetch(
        """
        WITH to_claim AS (
            SELECT a.id
            FROM ce_assignments a
            JOIN clients cl ON cl.id = a.client_id
            WHERE a.ce_id IS NULL AND a.status = 'pending' AND a.call_date <= CURRENT_DATE
            ORDER BY
                CASE
                    WHEN cl.segment = 'active_job_post' AND cl.stage::text = 'interested'    THEN 1
                    WHEN cl.segment = 'employer_lead'   AND cl.stage::text = 'interested'    THEN 2
                    WHEN cl.segment = 'ca_network'       AND cl.stage::text = 'interested'    THEN 3
                    WHEN cl.segment = 'active_job_post' AND cl.stage::text = 'reachout_sent' THEN 4
                    ELSE 5
                END,
                a.assigned_at ASC
            LIMIT $2
            FOR UPDATE OF a SKIP LOCKED
        )
        UPDATE ce_assignments a
        SET ce_id = $1, claimed_at = now(), updated_at = now()
        FROM to_claim
        WHERE a.id = to_claim.id
        RETURNING a.*
        """,
        ce_id, ce["daily_target"],
    )
    return {"data": {"claimed": [dict(r) for r in rows], "count": len(rows)}, "ok": True}


@router.get("/ce/{ce_id}/search")
async def search_ce_assignments(ce_id: uuid.UUID, q: str, conn: asyncpg.Connection = Depends(get_conn)):
    """Search across ALL of this executive's assignments (any status/date) — e.g. a
    client calling back after being marked called, or scheduled for a future date."""
    if len(q.strip()) < 2:
        return {"data": [], "count": 0, "ok": True}
    pattern = f"%{q.strip()}%"
    rows = await conn.fetch(
        _ASSIGNMENT_SELECT + """
        WHERE a.ce_id = $1 AND (cl.company_name ILIKE $2 OR cl.poc_name ILIKE $2 OR cl.poc_phone ILIKE $2)
        ORDER BY a.call_date DESC
        LIMIT 30
        """,
        ce_id, pattern,
    )
    return {"data": [dict(r) for r in rows], "count": len(rows), "ok": True}


@router.get("/ce/{ce_id}/history")
async def get_ce_history(
    ce_id: uuid.UUID,
    limit: int = 100,
    conn: asyncpg.Connection = Depends(get_conn),
):
    """All calls this executive has already made, most recent first."""
    rows = await conn.fetch(
        _ASSIGNMENT_SELECT + """
        WHERE a.ce_id = $1 AND a.status = 'called'
        ORDER BY a.called_at DESC
        LIMIT $2
        """,
        ce_id, limit,
    )
    return {"data": [dict(r) for r in rows], "count": len(rows), "ok": True}


_TERMINAL_CLIENT_STAGES = {"onboarded", "disqualified", "churned"}


async def _check_not_terminal(conn: asyncpg.Connection, assignment_id: uuid.UUID) -> None:
    """A client can leave the eligible pool independently of the CE (e.g. they
    onboard through another channel entirely). Block further call actions on
    that assignment once that's happened — nothing left to do."""
    stage = await conn.fetchval(
        """
        SELECT cl.stage::text FROM ce_assignments a
        JOIN clients cl ON cl.id = a.client_id
        WHERE a.id = $1
        """,
        assignment_id,
    )
    if stage in _TERMINAL_CLIENT_STAGES:
        raise HTTPException(409, f"Client is already {stage} — no further action needed")


_NO_PICKUP_LIMIT = 2  # after this many same-day no-pickups, give up for today and push to tomorrow


@router.post("/ce/{ce_id}/assignments/{assignment_id}/no-pickup")
async def mark_no_pickup(
    ce_id: uuid.UUID,
    assignment_id: uuid.UUID,
    conn: asyncpg.Connection = Depends(get_conn),
):
    """
    Client didn't answer. 1st time today: sink to the back of today's queue
    (via last_attempt_at, read by the queue's ORDER BY) so other not-yet-tried
    clients get called first, then this one gets retried once those are done.
    2nd time today (_NO_PICKUP_LIMIT): give up for today, push call_date to
    tomorrow, and reset the counter for a fresh attempt count that day.
    """
    await _check_not_terminal(conn, assignment_id)
    current = await conn.fetchrow(
        "SELECT no_pickup_count FROM ce_assignments WHERE id = $1 AND ce_id = $2 AND status = 'pending'",
        assignment_id, ce_id,
    )
    if not current:
        raise HTTPException(404, "Assignment not found")

    next_count = current["no_pickup_count"] + 1
    if next_count >= _NO_PICKUP_LIMIT:
        row = await conn.fetchrow(
            """
            UPDATE ce_assignments
            SET no_pickup_count = 0, last_attempt_at = now(),
                call_date = CURRENT_DATE + 1, updated_at = now()
            WHERE id = $1
            RETURNING *
            """,
            assignment_id,
        )
        return {"data": dict(row), "moved_to_tomorrow": True, "ok": True}

    row = await conn.fetchrow(
        """
        UPDATE ce_assignments
        SET no_pickup_count = $1, last_attempt_at = now(), updated_at = now()
        WHERE id = $2
        RETURNING *
        """,
        next_count, assignment_id,
    )
    return {"data": dict(row), "moved_to_tomorrow": False, "ok": True}


@router.post("/ce/{ce_id}/assignments/{assignment_id}/call")
async def mark_called(
    ce_id: uuid.UUID,
    assignment_id: uuid.UUID,
    body: MarkCalledBody,
    conn: asyncpg.Connection = Depends(get_conn),
):
    if body.sentiment not in ("positive", "negative", "wrong_poc", "wrong_company"):
        raise HTTPException(400, "sentiment must be 'positive', 'negative', 'wrong_poc', or 'wrong_company'")
    await _check_not_terminal(conn, assignment_id)
    row = await conn.fetchrow(
        """
        UPDATE ce_assignments
        SET status = 'called', sentiment = $1, comment = $2, called_at = now(), updated_at = now()
        WHERE id = $3 AND ce_id = $4
        RETURNING *
        """,
        body.sentiment, body.comment, assignment_id, ce_id,
    )
    if not row:
        raise HTTPException(404, "Assignment not found")

    # Org-wide visibility into scraping accuracy — surface it on the client
    # record itself (existing labels mechanism already used for e.g. 'Opted
    # Out'), not just buried in this one CE's call log. 'Wrong POC' means the
    # company is right but the contact/number is stale; 'Wrong Company' means
    # the scraper matched the wrong business entirely — tracked as distinct
    # labels/sentiments so reporting can tell scraping-accuracy issues apart
    # from contact-churn issues.
    label = "Wrong POC" if body.sentiment == "wrong_poc" else "Wrong Company" if body.sentiment == "wrong_company" else None
    if label:
        await conn.execute(
            """
            UPDATE clients
            SET labels = array_append(array_remove(COALESCE(labels, '{}'), $2), $2),
                updated_at = now()
            WHERE id = $1
            """,
            row["client_id"], label,
        )

    return {"data": dict(row), "ok": True}


def _entry_number(p) -> str | None:
    if isinstance(p, dict):
        return p.get("number")
    if isinstance(p, str):
        return p
    return None


async def _apply_poc_phone_correction(conn: asyncpg.Connection, client_id: uuid.UUID, new_phone: str) -> str:
    """Normalize + append the corrected number to phone_numbers[] (tagged
    'ce_correction', deduped) and promote it to poc_phone immediately, so the
    very next outreach uses it rather than waiting on someone else to review
    and promote it later. Returns the normalized 10-digit number."""
    phone_10 = re.sub(r"\D", "", new_phone)
    if phone_10.startswith("91") and len(phone_10) == 12:
        phone_10 = phone_10[2:]
    if len(phone_10) != 10:
        raise HTTPException(400, "new_poc_phone must be a valid 10-digit phone number")

    client = await conn.fetchrow("SELECT phone_numbers FROM clients WHERE id = $1", client_id)
    raw = client["phone_numbers"] if client else None
    current_entries = (raw if isinstance(raw, list) else _json.loads(raw)) if raw else []
    if not isinstance(current_entries, list):
        current_entries = []

    already_present = any(
        (n := _entry_number(p)) and re.sub(r"\D", "", n)[-10:] == phone_10
        for p in current_entries
    )
    if not already_present:
        current_entries.append({"number": phone_10, "source": "ce_correction"})

    await conn.execute(
        """
        UPDATE clients
        SET poc_phone = $2, phone_numbers = $3::jsonb, updated_at = now()
        WHERE id = $1
        """,
        client_id, phone_10, current_entries,
    )
    return phone_10


@router.post("/ce/{ce_id}/assignments/{assignment_id}/update-poc-phone")
async def update_poc_phone(
    ce_id: uuid.UUID,
    assignment_id: uuid.UUID,
    body: UpdatePocPhoneBody,
    conn: asyncpg.Connection = Depends(get_conn),
):
    """
    Standalone POC phone correction — independent of marking a call outcome,
    so a CE can fix a stale number the moment they discover it (mid-call,
    from history, wherever) without that also forcing the assignment to be
    marked 'called'. Allowed regardless of client stage, unlike the call
    actions — correcting contact info isn't a "call action" that should be
    blocked once a client goes onboarded/disqualified/churned.
    """
    client_id = await conn.fetchval(
        "SELECT client_id FROM ce_assignments WHERE id = $1 AND ce_id = $2",
        assignment_id, ce_id,
    )
    if not client_id:
        raise HTTPException(404, "Assignment not found")

    new_phone = await _apply_poc_phone_correction(conn, client_id, body.new_poc_phone)
    return {"data": {"poc_phone": new_phone}, "ok": True}


@router.post("/ce/{ce_id}/assignments/{assignment_id}/send-reachout")
async def send_reachout(
    ce_id: uuid.UUID,
    assignment_id: uuid.UUID,
    conn: asyncpg.Connection = Depends(get_conn),
):
    """
    Manual (re-)ping with the same client_reachout template the automated
    drip job uses — reuses routers.cron._send_client_reachout_step entirely
    (phone validation, GCS image, button/template construction, outreach_step
    and stage bookkeeping) rather than duplicating any of that. step_idx off
    the client's current outreach_step means this naturally sends the true
    first-reachout image for anyone never contacted, or the next step in
    sequence for a manual re-ping.
    """
    client_id = await conn.fetchval(
        "SELECT client_id FROM ce_assignments WHERE id = $1 AND ce_id = $2",
        assignment_id, ce_id,
    )
    if not client_id:
        raise HTTPException(404, "Assignment not found")

    client = await conn.fetchrow("SELECT * FROM clients WHERE id = $1", client_id)
    if not client:
        raise HTTPException(404, "Client not found")
    if client["is_dnd"]:
        raise HTTPException(400, "Client has opted out (DND) — cannot send")

    raw_numbers = client["phone_numbers"]
    if raw_numbers:
        phones = list(dict.fromkeys(
            p for p in (raw_numbers if isinstance(raw_numbers, list) else _json.loads(raw_numbers)) if p
        ))
    else:
        phones = [client["poc_phone"]] if client["poc_phone"] else []
    if not phones:
        raise HTTPException(400, "Client has no phone numbers")

    from services import gcs_service
    from routers.cron import _send_client_reachout_step

    image_urls = await gcs_service.list_reachout_images()
    if not image_urls:
        raise HTTPException(500, "No reachout images configured")
    step_idx = min(int(client["outreach_step"] or 0), len(image_urls) - 1)

    ok = await _send_client_reachout_step(client, image_urls[step_idx], conn, phones)
    if not ok:
        raise HTTPException(502, "Send failed — check server logs")
    return {"data": {"sent": True}, "ok": True}


@router.post("/ce/{ce_id}/assignments/{assignment_id}/shift")
async def shift_call_date(
    ce_id: uuid.UUID,
    assignment_id: uuid.UUID,
    body: ShiftBody,
    conn: asyncpg.Connection = Depends(get_conn),
):
    await _check_not_terminal(conn, assignment_id)
    row = await conn.fetchrow(
        """
        UPDATE ce_assignments
        SET call_date = $1, no_pickup_count = 0, updated_at = now()
        WHERE id = $2 AND ce_id = $3 AND status = 'pending'
        RETURNING *
        """,
        body.call_date, assignment_id, ce_id,
    )
    if not row:
        raise HTTPException(404, "Assignment not found (or already called)")
    return {"data": dict(row), "ok": True}


@router.get("/ce/{ce_id}/dashboard")
async def get_ce_dashboard(ce_id: uuid.UUID, conn: asyncpg.Connection = Depends(get_conn)):
    row = await conn.fetchrow(
        """
        SELECT
            COUNT(a.id) FILTER (WHERE a.status = 'called') AS total_called,
            COUNT(a.id) FILTER (WHERE a.sentiment = 'positive') AS positive,
            COUNT(a.id) FILTER (WHERE a.sentiment = 'negative') AS negative,
            COUNT(a.id) FILTER (WHERE a.sentiment = 'wrong_poc') AS wrong_poc,
            COUNT(a.id) FILTER (WHERE a.sentiment = 'wrong_company') AS wrong_company,
            COUNT(a.id) FILTER (WHERE cl.stage::text = 'onboarded') AS onboarded,
            COUNT(a.id) FILTER (WHERE a.status = 'pending' AND a.call_date <= CURRENT_DATE) AS today_pending,
            COUNT(a.id) FILTER (WHERE a.claimed_at::date = CURRENT_DATE) AS claimed_today,
            COUNT(a.id) FILTER (WHERE a.claimed_at IS NOT NULL) AS total_claimed
        FROM ce_assignments a
        JOIN clients cl ON cl.id = a.client_id
        WHERE a.ce_id = $1
        """,
        ce_id,
    )
    return {"data": dict(row), "ok": True}


@router.post("/ce/{ce_id}/sweep-assign")
async def trigger_sweep_assign(ce_id: uuid.UUID, conn: asyncpg.Connection = Depends(get_conn)):
    """
    Manual on-demand trigger for ce_service.sweep_assign — the same pool-fill
    sweep that otherwise only runs inside the bundled cron job
    (routers/cron.py). Tops up the shared unclaimed pool, not scoped to this
    ce_id — the id in the path is just so the CE's own page can trigger "check
    for new leads" without an admin having to wait for the next scheduled run.
    """
    ce = await conn.fetchval("SELECT 1 FROM customer_executives WHERE id = $1", ce_id)
    if not ce:
        raise HTTPException(404, "Customer executive not found")

    from services.ce_service import sweep_assign
    result = await sweep_assign(conn)
    return {"data": result, "ok": True}
