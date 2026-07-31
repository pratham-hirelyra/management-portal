"""
Outreach router — Phases 5, 6, 7.

POST /outreach/{client_id}/send-batch      Phase 5: compute optimal batch size + send JD to candidates
POST /outreach/{client_id}/interested/{mapping_id}  Candidate clicked Interested → lock + notify
GET  /outreach/{client_id}/handover-link   Phase 6: generate / retrieve secure handover token
GET  /handover/{token}                     Phase 6: public client-facing candidate card page

POST /outreach/{mapping_id}/feedback/client    Phase 7: client feedback (liked/disliked)
POST /outreach/{mapping_id}/feedback/candidate Phase 7: candidate feedback (liked/disliked)
GET  /feedback/{token}                         Phase 7: public feedback page (returns mapping info)

POST /cron/reminders-t1       Phase 6: daily 6 PM — T-1 day interview reminders
POST /cron/reminders-t2       Phase 6: every 30 min — T-2 hour interview reminders
POST /cron/feedback-requests  Phase 7: every 30 min — send feedback requests after interview time passes
"""

import uuid
import math
import os
import secrets
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
import asyncpg
from database import get_conn

router = APIRouter(tags=["outreach"])


# ── Phase 5: interest batching ────────────────────────────────────────────────

@router.post("/outreach/{client_id}/send-batch")
async def send_batch(
    client_id: uuid.UUID,
    conn: asyncpg.Connection = Depends(get_conn),
):
    """
    Determine optimal outreach batch size and send JD to candidates.
    Uses dynamic conversion-rate formula.
    """
    import services.msg91_service as msg91

    from routers.matching import GLOBAL_CAP

    client = await conn.fetchrow("SELECT * FROM clients WHERE id = $1", client_id)
    if not client:
        raise HTTPException(404, "Client not found")

    # Hard cap — never exceed GLOBAL_CAP total mappings for this client
    total_mapped = await conn.fetchval(
        "SELECT COUNT(*) FROM client_candidate_mappings WHERE client_id = $1",
        client_id,
    ) or 0
    if total_mapped >= GLOBAL_CAP:
        return {
            "data": {
                "message": f"Global cap of {GLOBAL_CAP} mappings reached. Manual review needed.",
                "sent": 0,
            },
            "ok": True,
        }

    # Current batch state
    batch_rows = await conn.fetch(
        "SELECT * FROM outreach_batches WHERE client_id = $1 ORDER BY batch_number",
        client_id,
    )
    total_sent = sum(r["total_sent"] for r in batch_rows)
    total_interested = sum(r["interested_count"] for r in batch_rows)
    next_batch = (max((r["batch_number"] for r in batch_rows), default=0) + 1)

    # Target = 5 if 1 open position, else open_positions * 3
    open_pos = client["open_positions"] or 1
    target = 5 if open_pos <= 1 else open_pos * 3

    # Current interested count from mappings
    current_interested = await conn.fetchval(
        """
        SELECT COUNT(*) FROM client_candidate_mappings
        WHERE client_id = $1 AND stage IN ('interested', 'slot_booked', 'interview_done', 'placed')
        """,
        client_id,
    )
    current_interested = current_interested or 0

    if current_interested >= target:
        return {"data": {"message": f"Already at target ({target} candidates)", "sent": 0}, "ok": True}

    needed = target - current_interested
    conversion_rate = (total_interested / total_sent) if total_sent > 0 else 0

    if conversion_rate > 0:
        raw = needed / conversion_rate
    else:
        raw = needed * 4  # fallback: 4× if no data

    batch_size = min(math.ceil(raw * 1.2), GLOBAL_CAP - total_mapped)  # 20% safety buffer, hard cap

    # Candidates already contacted (wa_sent_at set) or locked by us
    contacted_ids = await conn.fetch(
        """
        SELECT candidate_id FROM client_candidate_mappings
        WHERE client_id = $1 AND wa_sent_at IS NOT NULL
        """,
        client_id,
    )
    contacted_set = {str(r["candidate_id"]) for r in contacted_ids}

    # Candidates unavailable for this client:
    #   - Non-pass candidates locked by another client (existing 1-client rule)
    #   - Pass candidates already at 4-active-mapping capacity (multi-client race model)
    locked_ids = await conn.fetch(
        """
        SELECT DISTINCT cl.candidate_id
        FROM candidate_locks cl
        JOIN candidates c ON c.id = cl.candidate_id
        WHERE cl.unlocked_at IS NULL
          AND (cl.expires_at IS NULL OR cl.expires_at > now())
          AND cl.client_id != $1
          AND lower(c.evaluation_status) NOT IN ('pass', 'passed')

        UNION

        SELECT ccm.candidate_id
        FROM client_candidate_mappings ccm
        JOIN candidates c ON c.id = ccm.candidate_id
        WHERE lower(c.evaluation_status) IN ('pass', 'passed')
          AND ccm.stage::text NOT IN (
              'placed','rejected','rejected_by_client','not_interested','not_looking_for_job',
              'declined_for_interview','waitlisted','client_closed'
          )
        GROUP BY ccm.candidate_id
        HAVING COUNT(*) >= 4
        """,
        client_id,
    )
    locked_set = {str(r["candidate_id"]) for r in locked_ids}

    # Prefer mms_scores if populated, else fall back to client_candidate_mappings
    mms_count = await conn.fetchval(
        "SELECT COUNT(*) FROM mms_scores WHERE client_id = $1", client_id
    )
    if mms_count:
        candidates = await conn.fetch(
            """
            SELECT ms.candidate_id, c.phone, c.name
            FROM mms_scores ms
            JOIN candidates c ON c.id = ms.candidate_id
            WHERE ms.client_id = $1
              AND ms.filter_passed = true
              AND ms.mms_score >= 70
              AND ms.dnd_flagged = false
            ORDER BY ms.mms_score DESC
            """,
            client_id,
        )
    else:
        # auto_match path — all manually mapped candidates are eligible regardless of score
        candidates = await conn.fetch(
            """
            SELECT ccm.candidate_id, c.phone, c.name
            FROM client_candidate_mappings ccm
            JOIN candidates c ON c.id = ccm.candidate_id
            WHERE ccm.client_id = $1
            ORDER BY COALESCE(ccm.match_score, 0) DESC
            """,
            client_id,
        )

    eligible = [r for r in candidates
                if str(r["candidate_id"]) not in contacted_set
                and str(r["candidate_id"]) not in locked_set]

    to_send = eligible[:batch_size]
    if not to_send:
        sourcing_count = math.ceil(needed / 0.35)
        await conn.execute(
            """
            UPDATE clients
            SET sourcing_needed = true,
                sourcing_count_needed = $1,
                labels = array_append(array_remove(COALESCE(labels, '{}'), 'Sourcing Needed'), 'Sourcing Needed'),
                updated_at = now()
            WHERE id = $2
            """,
            sourcing_count, client_id,
        )
        return {
            "data": {
                "message": "No eligible candidates remaining. Sourcing flag set.",
                "sourcing_count_needed": sourcing_count,
                "sent": 0,
            },
            "ok": True,
        }

    # Build components for bulk intent template
    from routers.cron import _build_jd_components
    client_dict = dict(client)
    body_components = _build_jd_components(client_dict)

    # Create mapping rows + send
    mapping_rows = []
    for cand in to_send:
        mapping = await conn.fetchrow(
            """
            INSERT INTO client_candidate_mappings
                (client_id, candidate_id, stage, batch_number, created_at, updated_at)
            VALUES ($1, $2, 'matched'::mapping_stage, $3, now(), now())
            ON CONFLICT (client_id, candidate_id) DO NOTHING
            RETURNING id, candidate_id
            """,
            client_id, cand["candidate_id"], next_batch,
        )
        if not mapping:
            # Mapping already existed from auto-match — fetch its id
            mapping = await conn.fetchrow(
                """
                SELECT id, candidate_id FROM client_candidate_mappings
                WHERE client_id = $1 AND candidate_id = $2
                """,
                client_id, cand["candidate_id"],
            )
        if mapping:
            cand_eval = await conn.fetchval(
                "SELECT evaluation_status FROM candidates WHERE id = $1",
                cand["candidate_id"],
            )
            if (cand_eval or "").lower() not in ("pass", "passed"):
                await conn.execute(
                    """
                    INSERT INTO candidate_locks (candidate_id, client_id)
                    VALUES ($1, $2)
                    ON CONFLICT (candidate_id, client_id)
                    DO UPDATE SET locked_at = now(), unlocked_at = NULL, unlock_reason = NULL
                    """,
                    cand["candidate_id"], client_id,
                )
            mapping_rows.append({
                "mapping_id": str(mapping["id"]),
                "phone": _fmt_phone(cand["phone"] or ""),
            })

    sent_count = 0
    if mapping_rows:
        try:
            await msg91.send_bulk_intent(mapping_rows, body_components)
            sent_count = len(mapping_rows)
            mids = [uuid.UUID(m["mapping_id"]) for m in mapping_rows]
            await conn.execute(
                """
                UPDATE client_candidate_mappings
                SET stage = 'intent_ask'::mapping_stage, wa_sent_at = now(), updated_at = now()
                WHERE id = ANY($1::uuid[])
                """,
                mids,
            )
        except Exception as e:
            print(f"[batch] Bulk send failed: {e}")
            try:
                from services.google_chat_service import send_alert
                await send_alert("Bulk JD send failed", str(e), severity="ERROR", context={"client_id": str(client_id), "batch_size": len(mapping_rows)})
            except Exception:
                pass

    try:
        await conn.execute(
            """
            INSERT INTO outreach_batches (client_id, batch_number, candidate_ids, sent_at, total_sent)
            VALUES ($1, $2, $3, now(), $4)
            """,
            client_id, next_batch,
            [uuid.UUID(r["mapping_id"]) for r in mapping_rows],
            sent_count,
        )
    except Exception as e:
        print(f"[batch] outreach_batches record failed: {e}")

    return {
        "data": {
            "batch_number": next_batch,
            "sent": sent_count,
            "batch_size_computed": batch_size,
            "conversion_rate": round(conversion_rate, 3),
        },
        "ok": True,
    }


@router.post("/outreach/{client_id}/interested/{mapping_id}")
async def mark_interested(
    client_id: uuid.UUID,
    mapping_id: uuid.UUID,
    conn: asyncpg.Connection = Depends(get_conn),
):
    """
    Called when a candidate clicks the Interested web link.
    Mirrors flow_engine._handle_interested_response:
      eval pass  → lock + client_approval_pending
      eval fail  → not_interested + unlock
      eval null  → interested (form not filled yet, cron will follow up)
    """
    mapping = await conn.fetchrow(
        "SELECT * FROM client_candidate_mappings WHERE id = $1 AND client_id = $2",
        mapping_id, client_id,
    )
    if not mapping:
        raise HTTPException(404, "Mapping not found")

    cand_row = await conn.fetchrow(
        "SELECT evaluation_status, form_submitted FROM candidates WHERE id = $1",
        mapping["candidate_id"],
    )
    eval_status = (cand_row["evaluation_status"] or "").lower() if cand_row else ""

    if eval_status in ("pass", "passed"):
        # Pass candidates: multi-client race model — no lock, just advance stage
        await conn.execute(
            """
            UPDATE client_candidate_mappings
            SET stage = 'client_approval_pending'::mapping_stage,
                intent_received_at = now(), updated_at = now()
            WHERE id = $1
            """,
            mapping_id,
        )
        return {"data": {"stage": "client_approval_pending"}, "ok": True}

    elif eval_status in ("fail", "failed", "reject"):
        # Failed evaluation → close out, no lock needed
        await conn.execute(
            """
            UPDATE client_candidate_mappings
            SET stage = 'not_interested'::mapping_stage,
                decline_reason = 'evaluation_failed',
                intent_received_at = now(), updated_at = now()
            WHERE id = $1
            """,
            mapping_id,
        )
        await conn.execute(
            """
            UPDATE candidate_locks
            SET unlocked_at = now(), unlock_reason = 'evaluation_failed'
            WHERE candidate_id = $1 AND client_id = $2 AND unlocked_at IS NULL
            """,
            mapping["candidate_id"], client_id,
        )
        return {"data": {"stage": "not_interested"}, "ok": True}

    else:
        # eval_status is null → form not filled / AI call not done yet.
        # Stage = interested; cron will send form reminders. Not yet in
        # client_approval_pending, so no client notify here — that fires once
        # evaluation passes and this candidate actually lands in review.
        await conn.execute(
            """
            UPDATE client_candidate_mappings
            SET stage = 'interested'::mapping_stage,
                intent_received_at = now(), updated_at = now()
            WHERE id = $1
            """,
            mapping_id,
        )
        return {"data": {"stage": "interested", "form_needed": True}, "ok": True}


# ── Phase 6: handover link ─────────────────────────────────────────────────────

@router.get("/outreach/{client_id}/handover-link")
async def get_handover_link(
    client_id: uuid.UUID,
    conn: asyncpg.Connection = Depends(get_conn),
):
    # Reuse existing token if not expired
    existing = await conn.fetchrow(
        """
        SELECT token FROM handover_tokens
        WHERE client_id = $1 AND (expires_at IS NULL OR expires_at > now())
        ORDER BY created_at DESC LIMIT 1
        """,
        client_id,
    )
    if existing:
        token = existing["token"]
    else:
        token = secrets.token_urlsafe(32)
        await conn.execute(
            "INSERT INTO handover_tokens (client_id, token) VALUES ($1, $2)",
            client_id, token,
        )

    base = os.environ.get("FRONTEND_URL", "http://localhost:5173")
    return {"data": {"token": token, "url": f"{base}/handover/{token}"}, "ok": True}


@router.get("/handover/{token}")
async def handover_page_data(
    token: str,
    conn: asyncpg.Connection = Depends(get_conn),
):
    """Public endpoint — returns candidate cards for the pre-interview handover page."""
    ht = await conn.fetchrow(
        "SELECT client_id, expires_at FROM handover_tokens WHERE token = $1", token
    )
    if not ht:
        raise HTTPException(403, "Invalid or expired link")
    if ht["expires_at"] and ht["expires_at"] < datetime.now(timezone.utc):
        raise HTTPException(403, "This link has expired")

    client_id = ht["client_id"]
    client = await conn.fetchrow("SELECT company_name, job_title, max_salary, feedback_token FROM clients WHERE id = $1", client_id)

    rows = await conn.fetch(
        """
        SELECT
            c.id, c.name, c.age_years, c.current_location,
            c.current_salary, c.technical_evaluation_score,
            c.cv_url, c.final_evaluation_report_url,
            c.other_details, c.experience, c.photo_url,
            c.labels, c.notice_period,
            ms.mms_score, ms.bts, ms.classified_category,
            ccm.interview_slot, ccm.available_slots, ccm.id AS mapping_id,
            ccm.feedback_client, ccm.feedback_client_reason,
            ccm.interview_done
        FROM client_candidate_mappings ccm
        JOIN candidates c ON c.id = ccm.candidate_id
        JOIN mms_scores ms ON ms.candidate_id = c.id AND ms.client_id = ccm.client_id
        WHERE ccm.client_id = $1
          AND ccm.stage IN ('interview_scheduled', 'interview_done', 'slot_booked', 'placed', 'rejected')
        ORDER BY ms.mms_score DESC
        """,
        client_id,
    )

    max_salary = float(client["max_salary"] or 0) if client else 0

    cards = []
    for r in rows:
        r = dict(r)
        od = r.get("other_details") or {}
        if isinstance(od, str):
            import json as _j
            try: od = _j.loads(od)
            except Exception: od = {}

        bts = float(r.get("bts") or 40)
        tech = float(r.get("technical_evaluation_score") or 0)
        ratio = tech / bts if bts > 0 else 0

        _stability_labels = {"High Stability", "Average Stability", "High Turnover Risk"}
        labels = r.get("labels") or []
        stability = next((l for l in labels if l in _stability_labels), None)

        # Extract human-readable slot label from available_slots
        slot_label = None
        av = r.get("available_slots")
        if isinstance(av, str):
            import json as _jj
            try: av = _jj.loads(av)
            except Exception: av = []
        if av and isinstance(av, list) and av[0].get("label"):
            slot_label = av[0]["label"]

        cards.append({
            "candidate_id": str(r["id"]),
            "name": r["name"],
            "initials": _initials(r["name"]),
            "age_years": r["age_years"],
            "current_area": r["current_location"],
            "current_salary": r["current_salary"],
            "salary_vs_budget": max_salary,
            "mms_score_pct": round(float(r["mms_score"] or 0), 1),
            "ai_technical_score": tech,
            "job_stability": stability or "",
            "accounting_software": od.get("tools"),
            "gst_experience": od.get("gst"),
            "tds_experience": od.get("tds"),
            "notice_period": r.get("notice_period"),
            "photo_url": r.get("photo_url"),
            "cv_url": r["cv_url"],
            "evaluation_report_url": r["final_evaluation_report_url"],
            "interview_slot": r["interview_slot"].isoformat() if r["interview_slot"] else None,
            "interview_slot_label": slot_label,
            "mapping_id": str(r["mapping_id"]),
            "feedback_client": r["feedback_client"],
            "feedback_client_reason": r["feedback_client_reason"],
            "interview_done": r["interview_done"],
        })

    feedback_token = str(client["feedback_token"]) if client and client["feedback_token"] else None

    return {
        "data": {
            "client": {"company_name": client["company_name"], "job_title": client["job_title"]} if client else {},
            "feedback_token": feedback_token,
            "candidates": cards,
        },
        "ok": True,
    }


# ── Phase 7: feedback ──────────────────────────────────────────────────────────

@router.get("/feedback/{token}")
async def get_feedback_page(
    token: str,
    conn: asyncpg.Connection = Depends(get_conn),
):
    """Returns info needed to render the feedback page (client or candidate)."""
    row = await conn.fetchrow(
        """
        SELECT ccm.id AS mapping_id, ccm.feedback_client, ccm.feedback_candidate,
               c_cand.name AS candidate_name, cl.company_name, cl.job_title,
               ccm.feedback_token_client, ccm.feedback_token_candidate
        FROM client_candidate_mappings ccm
        JOIN candidates c_cand ON c_cand.id = ccm.candidate_id
        JOIN clients cl ON cl.id = ccm.client_id
        WHERE ccm.feedback_token_client = $1 OR ccm.feedback_token_candidate = $1
        """,
        token,
    )
    if not row:
        raise HTTPException(404, "Feedback link not found")

    is_client = row["feedback_token_client"] == token
    return {
        "data": {
            "mapping_id": str(row["mapping_id"]),
            "role": "client" if is_client else "candidate",
            "candidate_name": row["candidate_name"],
            "company_name": row["company_name"],
            "job_title": row["job_title"],
            "already_submitted": bool(
                row["feedback_client"] if is_client else row["feedback_candidate"]
            ),
        },
        "ok": True,
    }


@router.post("/feedback/{token}/submit")
async def submit_feedback(
    token: str,
    response: str = Query(..., pattern="^(liked|disliked)$"),
    conn: asyncpg.Connection = Depends(get_conn),
):
    row = await conn.fetchrow(
        """
        SELECT id, feedback_token_client, feedback_token_candidate,
               feedback_client, feedback_candidate,
               client_id, candidate_id
        FROM client_candidate_mappings
        WHERE feedback_token_client = $1 OR feedback_token_candidate = $1
        """,
        token,
    )
    if not row:
        raise HTTPException(404, "Feedback link not found")

    is_client = row["feedback_token_client"] == token
    mapping_id = row["id"]

    if is_client:
        if row["feedback_client"]:
            return {"data": {"message": "Already submitted"}, "ok": True}
        await conn.execute(
            "UPDATE client_candidate_mappings SET feedback_client = $1, updated_at = now() WHERE id = $2",
            response, mapping_id,
        )
    else:
        if row["feedback_candidate"]:
            return {"data": {"message": "Already submitted"}, "ok": True}
        await conn.execute(
            "UPDATE client_candidate_mappings SET feedback_candidate = $1, updated_at = now() WHERE id = $2",
            response, mapping_id,
        )

    # Re-read both feedbacks to check outcome
    updated = await conn.fetchrow(
        "SELECT feedback_client, feedback_candidate, client_id, candidate_id FROM client_candidate_mappings WHERE id = $1",
        mapping_id,
    )
    await _handle_outcome(mapping_id, updated, conn)

    return {"data": {"submitted": True}, "ok": True}


# ── cron: T-1 day reminders ───────────────────────────────────────────────────

@router.post("/cron/reminders-t1")
async def cron_t1_reminders(conn: asyncpg.Connection = Depends(get_conn)):
    """
    Cloud Scheduler: daily at 6 PM.
    Send T-1 day reminders for interviews happening tomorrow.
    """
    import services.msg91_service as msg91

    tomorrow_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    ) + timedelta(days=1)
    tomorrow_end = tomorrow_start + timedelta(days=1)

    rows = await conn.fetch(
        """
        SELECT ccm.id AS mapping_id, ccm.interview_slot, ccm.candidate_id,
               c.phone AS candidate_phone, c.name AS candidate_name,
               cl.poc_phone AS client_phone, cl.poc_name, cl.company_name, cl.job_location,
               cl.job_title
        FROM client_candidate_mappings ccm
        JOIN candidates c  ON c.id  = ccm.candidate_id
        JOIN clients    cl ON cl.id = ccm.client_id
        WHERE ccm.interview_slot >= $1
          AND ccm.interview_slot <  $2
          AND ccm.interview_reminder_sent = false
          AND ccm.stage = 'slot_booked'
        """,
        tomorrow_start, tomorrow_end,
    )

    sent = 0
    for r in rows:
        slot_str = r["interview_slot"].strftime("%d %b %Y at %I:%M %p") if r["interview_slot"] else ""
        any_sent = False
        # Candidate reminder
        if r["candidate_phone"]:
            try:
                await msg91.send_text(
                    r["candidate_phone"],
                    f"Hi {r['candidate_name'] or 'there'}, reminder: your interview with "
                    f"{r['company_name']} is tomorrow — {slot_str}. "
                    f"Office address: {r['job_location'] or 'check with HR'}. All the best!",
                    sender="candidate",
                )
                any_sent = True
            except Exception as e:
                print(f"[reminders-t1] candidate WA failed for {r['mapping_id']}: {e}")
                try:
                    from services.google_chat_service import send_alert
                    await send_alert("T-1 candidate reminder send failed", str(e), severity="ERROR", context={"mapping_id": str(r["mapping_id"]), "phone": r["candidate_phone"]})
                except Exception:
                    pass
        # Client reminder
        if r["client_phone"]:
            try:
                await msg91.send_text(
                    r["client_phone"],
                    f"Hi {r['poc_name'] or 'there'}, reminder: interview scheduled for tomorrow — "
                    f"{slot_str}. Candidate: {r['candidate_name']}.",
                    sender="client",
                )
                any_sent = True
            except Exception as e:
                print(f"[reminders-t1] client WA failed for {r['mapping_id']}: {e}")
                try:
                    from services.google_chat_service import send_alert
                    await send_alert("T-1 client reminder send failed", str(e), severity="ERROR", context={"mapping_id": str(r["mapping_id"]), "phone": r["client_phone"]})
                except Exception:
                    pass
        # Only mark reminder as sent if at least one WA send succeeded
        if any_sent:
            await conn.execute(
                "UPDATE client_candidate_mappings SET interview_reminder_sent = true, updated_at = now() WHERE id = $1",
                r["mapping_id"],
            )
            sent += 1

    return {"data": {"sent": sent}, "ok": True}


# ── cron: T-2 hour reminders ──────────────────────────────────────────────────

@router.post("/cron/reminders-t2")
async def cron_t2_reminders(conn: asyncpg.Connection = Depends(get_conn)):
    """
    Cloud Scheduler: every 30 minutes.
    Send T-2 hour reminder to candidate for interviews happening within 2 hours.
    """
    import services.msg91_service as msg91

    now = datetime.now(timezone.utc)
    window_end = now + timedelta(hours=2)

    rows = await conn.fetch(
        """
        SELECT ccm.id AS mapping_id, ccm.interview_slot,
               c.phone AS candidate_phone, c.name AS candidate_name,
               cl.job_location, cl.company_name
        FROM client_candidate_mappings ccm
        JOIN candidates c  ON c.id  = ccm.candidate_id
        JOIN clients    cl ON cl.id = ccm.client_id
        WHERE ccm.interview_slot >= $1
          AND ccm.interview_slot <= $2
          AND ccm.reminder_t2_sent = false
          AND ccm.stage = 'slot_booked'
        """,
        now, window_end,
    )

    sent = 0
    for r in rows:
        slot_str = r["interview_slot"].strftime("%I:%M %p") if r["interview_slot"] else ""
        wa_sent = False
        if r["candidate_phone"]:
            try:
                await msg91.send_text(
                    r["candidate_phone"],
                    f"Hi {r['candidate_name'] or 'there'}, your interview with "
                    f"{r['company_name']} is in about 2 hours at {slot_str}. "
                    f"Address: {r['job_location'] or 'check with HR'}. Good luck!",
                    sender="candidate",
                )
                wa_sent = True
            except Exception as e:
                print(f"[reminders-t2] candidate WA failed for {r['mapping_id']}: {e}")
                try:
                    from services.google_chat_service import send_alert
                    await send_alert("T-2 candidate reminder send failed", str(e), severity="ERROR", context={"mapping_id": str(r["mapping_id"]), "phone": r["candidate_phone"]})
                except Exception:
                    pass
        # Only mark reminder as sent if WA send succeeded
        if wa_sent:
            await conn.execute(
                "UPDATE client_candidate_mappings SET reminder_t2_sent = true, updated_at = now() WHERE id = $1",
                r["mapping_id"],
            )
            sent += 1

    return {"data": {"sent": sent}, "ok": True}


# ── cron: feedback requests ───────────────────────────────────────────────────

@router.post("/cron/feedback-requests")
async def cron_feedback_requests(conn: asyncpg.Connection = Depends(get_conn)):
    """
    Cloud Scheduler: every 30 minutes.
    For interviews whose slot time has passed, mark interview_done. This is
    bookkeeping only — it does NOT message the client directly. The actual
    client-facing "please share your feedback" ping (proper approved template,
    button deep-linking into the client portal's Feedback tab) is sent by
    _followup_feedback_pending (cron.py), which runs immediately after this in
    cron_client_followups and picks up every interview_done mapping with no
    feedback_client yet — including ones this function just transitioned.
    Previously this function also sent its own plain-text message with a raw
    client_token link (client portal used to be token-based); that link used a
    stale/nonexistent FRONTEND_BASE_URL env var and had drifted out of sync
    with the client-portal's move to an authenticated dashboard, so real
    clients were receiving broken localhost links. Removed rather than fixed
    in place since _followup_feedback_pending already covers the same ping
    correctly and stamping last_feedback_reminder_at here was actually
    suppressing that better message from also going out.

    The candidate is deliberately NOT asked here — candidate feedback is only
    requested once the client has liked/selected them (see
    _followup_post_interview_candidate in cron.py, gated on feedback_client
    = 'liked'), since there's nothing to ask the candidate about a client who
    already rejected them.
    """
    now = datetime.now(timezone.utc)

    rows = await conn.fetch(
        """
        SELECT ccm.id AS mapping_id
        FROM client_candidate_mappings ccm
        JOIN clients cl ON cl.id = ccm.client_id
        WHERE ccm.interview_slot IS NOT NULL
          AND ccm.interview_slot < $1
          AND ccm.feedback_requested_at IS NULL
          AND ccm.stage = 'slot_booked'
          AND cl.stage = 'onboarded'::client_stage
        """,
        now,
    )

    sent = 0
    for r in rows:
        await conn.execute(
            """
            UPDATE client_candidate_mappings
            SET feedback_requested_at = now(), stage = 'interview_done'::mapping_stage,
                interview_done = true, interview_done_at = now(), updated_at = now()
            WHERE id = $1
            """,
            r["mapping_id"],
        )
        sent += 1

    # No-show check: flag low trust after 48h with no feedback from either side
    no_response_rows = await conn.fetch(
        """
        SELECT id FROM client_candidate_mappings
        WHERE feedback_requested_at IS NOT NULL
          AND feedback_requested_at < now() - interval '48 hours'
          AND feedback_client IS NULL
          AND feedback_candidate IS NULL
          AND low_trust_flagged = false
          AND stage = 'interview_done'
        """
    )
    for r in no_response_rows:
        await conn.execute(
            "UPDATE client_candidate_mappings SET low_trust_flagged = true, updated_at = now() WHERE id = $1",
            r["id"],
        )

    return {"data": {"feedback_requests_sent": sent}, "ok": True}


# ── outcome handler ────────────────────────────────────────────────────────────

async def _handle_outcome(mapping_id: uuid.UUID, row: dict, conn: asyncpg.Connection) -> None:
    """
    Phase 7 — Process interview outcome once both (or sufficient) feedback is received.
    """
    import services.msg91_service as msg91

    fc = row.get("feedback_client")
    fca = row.get("feedback_candidate")
    client_id = row["client_id"]
    candidate_id = row["candidate_id"]

    if not fc:
        return  # wait for client feedback at minimum

    client = await conn.fetchrow(
        "SELECT poc_phone, poc_name, company_name, rm_id FROM clients WHERE id = $1", client_id
    )
    candidate = await conn.fetchrow(
        "SELECT name, phone FROM candidates WHERE id = $1", candidate_id
    )

    rm_phone = None
    if client and client["rm_id"]:
        rm_row = await conn.fetchrow("SELECT phone FROM rms WHERE id = $1", client["rm_id"])
        if rm_row:
            rm_phone = rm_row["phone"]

    # Outcome 1 — mutual match
    if fc == "liked" and fca == "liked":
        await conn.execute(
            """
            UPDATE client_candidate_mappings
            SET stage = 'placed'::mapping_stage, placement_confirmed = true, updated_at = now()
            WHERE id = $1
            """,
            mapping_id,
        )
        await conn.execute(
            "UPDATE clients SET stage = 'onboarded'::client_stage, updated_at = now() WHERE id = $1",
            client_id,
        )
        if candidate and candidate["phone"]:
            try:
                await msg91.send_text(
                    candidate["phone"],
                    f"Hi, {client['company_name']} has selected you. To complete the placement, "
                    f"please email your last 3 months' salary slips and last 6 months' bank statements.",
                    sender="candidate",
                )
            except Exception as e:
                print(f"[outcome] mutual match candidate WA failed for mapping {mapping_id}: {e}")
                try:
                    from services.google_chat_service import send_alert
                    await send_alert("Mutual match candidate notification failed", str(e), severity="ERROR", context={"mapping_id": str(mapping_id), "phone": candidate["phone"]})
                except Exception:
                    pass
        if rm_phone:
            try:
                await msg91.send_text(
                    rm_phone,
                    f"Mutual match confirmed: {candidate['name']} × {client['company_name']}. "
                    f"Please initiate manual closure.",
                    sender="client",
                )
            except Exception as e:
                print(f"[outcome] mutual match RM WA failed for mapping {mapping_id}: {e}")
                try:
                    from services.google_chat_service import send_alert
                    await send_alert("Mutual match RM notification failed", str(e), severity="ERROR", context={"mapping_id": str(mapping_id), "rm_phone": rm_phone})
                except Exception:
                    pass

    # Outcome 2 — client rejects (disliked, and all candidates in current batch disliked)
    elif fc == "disliked":
        # Check if all candidates in current batch are disliked
        batch_num = await conn.fetchval(
            "SELECT batch_number FROM client_candidate_mappings WHERE id = $1", mapping_id
        )
        remaining_liked = await conn.fetchval(
            """
            SELECT COUNT(*) FROM client_candidate_mappings
            WHERE client_id = $1 AND batch_number = $2
              AND (feedback_client IS NULL OR feedback_client = 'liked')
              AND id != $3
            """,
            client_id, batch_num, mapping_id,
        )
        if remaining_liked == 0 and rm_phone:
            base = os.environ.get("FRONTEND_URL", "http://localhost:5173")
            try:
                await msg91.send_text(
                    rm_phone,
                    f"Client {client['company_name']} has rejected all candidates in batch {batch_num}. "
                    f"Please review and update the JD: {base}/clients/{client_id}",
                    sender="client",
                )
            except Exception as e:
                print(f"[outcome] all-rejected RM WA failed for client {client_id}: {e}")
                try:
                    from services.google_chat_service import send_alert
                    await send_alert("All-batch-rejected RM notification failed", str(e), severity="ERROR", context={"client_id": str(client_id), "rm_phone": rm_phone, "batch_num": batch_num})
                except Exception:
                    pass

    # Outcome 3 — candidate disinterest (client liked, candidate disliked or no response)
    elif fc == "liked" and (fca == "disliked" or fca is None):
        if rm_phone and candidate:
            try:
                await msg91.send_text(
                    rm_phone,
                    f"Candidate {candidate['name']} ({candidate['phone']}) declined after the interview "
                    f"with {client['company_name']}. Manual follow-up may be needed.",
                    sender="client",
                )
            except Exception as e:
                print(f"[outcome] candidate-declined RM WA failed for mapping {mapping_id}: {e}")
                try:
                    from services.google_chat_service import send_alert
                    await send_alert("Candidate-declined RM notification failed", str(e), severity="ERROR", context={"mapping_id": str(mapping_id), "rm_phone": rm_phone})
                except Exception:
                    pass

    # Outcome 4 — no-show: handled by the 48h flag in cron, no action here


# ── Re-run batch (manual, excludes already-contacted) ─────────────────────────

@router.post("/outreach/{client_id}/rerun-batch")
async def rerun_batch(
    client_id: uuid.UUID,
    conn: asyncpg.Connection = Depends(get_conn),
):
    """Re-run auto-matchmaking for the client. Same as auto-match but callable from outreach flow."""
    from routers.mappings import run_auto_match_for_client
    return await run_auto_match_for_client(client_id, conn)


# ── Client-level feedback page ────────────────────────────────────────────────

class ClientFeedbackBody(BaseModel):
    status: str
    reason: str | None = None


@router.get("/client-feedback/{token}")
async def get_client_feedback_page(
    token: str,
    conn: asyncpg.Connection = Depends(get_conn),
):
    """Public endpoint — returns all interview candidates for a client's bulk feedback page."""
    try:
        token_uuid = uuid.UUID(token)
    except ValueError:
        raise HTTPException(403, "Invalid feedback link")

    client = await conn.fetchrow(
        "SELECT id, company_name, job_title FROM clients WHERE feedback_token = $1",
        token_uuid,
    )
    if not client:
        raise HTTPException(403, "Invalid feedback link")

    rows = await conn.fetch(
        """
        SELECT
            ccm.id AS mapping_id, ccm.stage, ccm.interview_slot,
            ccm.feedback_client, ccm.feedback_client_reason,
            c.id AS candidate_id, c.name AS candidate_name,
            c.age_years, c.current_location, c.current_salary,
            c.technical_evaluation_score, c.cv_url, c.final_evaluation_report_url,
            c.experience,
            c.photo_url, c.labels
        FROM client_candidate_mappings ccm
        JOIN candidates c ON c.id = ccm.candidate_id
        WHERE ccm.client_id = $1
          AND ccm.stage IN ('slot_booked', 'interview_done', 'placed', 'rejected')
        ORDER BY ccm.interview_slot ASC NULLS LAST
        """,
        client["id"],
    )

    candidates = []
    for r in rows:
        r = dict(r)
        candidates.append({
            "mapping_id":      str(r["mapping_id"]),
            "stage":           r["stage"],
            "interview_slot":  r["interview_slot"].isoformat() if r["interview_slot"] else None,
            "feedback":        r["feedback_client"],
            "feedback_reason": r["feedback_client_reason"],
            "candidate_id":    str(r["candidate_id"]),
            "name":            r["candidate_name"],
            "initials":        _initials(r["candidate_name"]),
            "age_years":       r["age_years"],
            "current_location": r["current_location"],
            "current_salary":  r["current_salary"],
            "technical_score": float(r["technical_evaluation_score"] or 0),
            "experience":      r["experience"],
            "cv_url":          r["cv_url"],
            "report_url":      r["final_evaluation_report_url"],
            "photo_url":       r.get("photo_url"),
            "labels":          r.get("labels") or [],
        })

    return {
        "data": {
            "client": {
                "company_name": client["company_name"],
                "job_title":    client["job_title"],
            },
            "candidates": candidates,
        },
        "ok": True,
    }


@router.post("/client-feedback/{token}/candidate/{mapping_id}")
async def submit_client_candidate_feedback(
    token: str,
    mapping_id: uuid.UUID,
    body: ClientFeedbackBody,
    conn: asyncpg.Connection = Depends(get_conn),
):
    import services.msg91_service as msg91

    if body.status not in ("liked", "disliked"):
        raise HTTPException(400, "status must be 'liked' or 'disliked'")
    try:
        token_uuid = uuid.UUID(token)
    except ValueError:
        raise HTTPException(403, "Invalid feedback link")

    client = await conn.fetchrow(
        "SELECT id, company_name FROM clients WHERE feedback_token = $1", token_uuid
    )
    if not client:
        raise HTTPException(403, "Invalid feedback link")

    if body.status == "disliked":
        result = await conn.execute(
            """
            UPDATE client_candidate_mappings
            SET feedback_client = $1, feedback_client_reason = $2,
                stage = 'rejected'::mapping_stage, rejection_reason = 'client_disliked', updated_at = now()
            WHERE id = $3 AND client_id = $4
            """,
            body.status, body.reason, mapping_id, client["id"],
        )
        if result == "UPDATE 0":
            raise HTTPException(404, "Mapping not found for this client")

        # Unlock candidate so they can be matched to other clients
        await conn.execute(
            """
            UPDATE candidate_locks cl
            SET unlocked_at = now(), unlock_reason = 'client_rejected'
            FROM client_candidate_mappings ccm
            WHERE ccm.id = $1
              AND cl.candidate_id = ccm.candidate_id
              AND cl.client_id = ccm.client_id
              AND cl.unlocked_at IS NULL
            """,
            mapping_id,
        )

        # Notify candidate of rejection
        cand_row = await conn.fetchrow(
            """
            SELECT c.phone, c.name
            FROM client_candidate_mappings ccm
            JOIN candidates c ON c.id = ccm.candidate_id
            WHERE ccm.id = $1
            """,
            mapping_id,
        )
        if cand_row and cand_row["phone"]:
            try:
                await msg91.send_text(
                    cand_row["phone"],
                    f"Hi {cand_row['name'] or 'there'}, thank you for appearing for the interview "
                    f"with {client['company_name']}. Unfortunately, they have decided not to move "
                    f"forward at this time. We will reach out as soon as a suitable opportunity "
                    f"arises. Best of luck!",
                    sender="candidate",
                )
            except Exception as e:
                print(f"[feedback] Rejection WA failed: {e}")
    else:
        result = await conn.execute(
            """
            UPDATE client_candidate_mappings
            SET feedback_client = $1, feedback_client_reason = $2, updated_at = now()
            WHERE id = $3 AND client_id = $4
            """,
            body.status, body.reason, mapping_id, client["id"],
        )
        if result == "UPDATE 0":
            raise HTTPException(404, "Mapping not found for this client")

    return {"data": {"submitted": True}, "ok": True}


@router.post("/client-feedback/{token}/candidate/{mapping_id}/reopen")
async def reopen_client_candidate_feedback(
    token: str,
    mapping_id: uuid.UUID,
    conn: asyncpg.Connection = Depends(get_conn),
):
    try:
        token_uuid = uuid.UUID(token)
    except ValueError:
        raise HTTPException(403, "Invalid feedback link")

    client = await conn.fetchrow(
        "SELECT id FROM clients WHERE feedback_token = $1", token_uuid
    )
    if not client:
        raise HTTPException(403, "Invalid feedback link")

    # If stage is rejected (set by a dislike), revert to interview_done
    await conn.execute(
        """
        UPDATE client_candidate_mappings
        SET feedback_client = NULL, feedback_client_reason = NULL,
            rejection_reason = CASE WHEN rejection_reason = 'client_disliked' THEN NULL ELSE rejection_reason END,
            stage = CASE WHEN stage = 'rejected'::mapping_stage
                         THEN 'interview_done'::mapping_stage
                         ELSE stage END,
            updated_at = now()
        WHERE id = $1 AND client_id = $2
        """,
        mapping_id, client["id"],
    )

    # Re-lock candidate for this client if we reverted from rejected
    mapping = await conn.fetchrow(
        "SELECT candidate_id, stage FROM client_candidate_mappings WHERE id = $1", mapping_id
    )
    if mapping and mapping["stage"] == "interview_done":
        await conn.execute(
            """
            INSERT INTO candidate_locks (candidate_id, client_id)
            VALUES ($1, $2)
            ON CONFLICT (candidate_id, client_id) DO UPDATE
            SET unlocked_at = NULL, unlock_reason = NULL
            """,
            mapping["candidate_id"], client["id"],
        )

    return {"data": {"reopened": True}, "ok": True}


# ── Candidate post-interview feedback ─────────────────────────────────────────

class CandidateFeedbackBody(BaseModel):
    decision: str  # 'join' | 'no_join'


@router.get("/candidate-feedback/{token}")
async def get_candidate_feedback_page(
    token: str,
    conn: asyncpg.Connection = Depends(get_conn),
):
    """Public — returns job info for candidate's post-interview join/no-join page."""
    row = await conn.fetchrow(
        """
        SELECT ccm.id AS mapping_id, ccm.feedback_candidate,
               c.name AS candidate_name,
               cl.company_name, cl.job_title, cl.job_location
        FROM client_candidate_mappings ccm
        JOIN candidates c  ON c.id  = ccm.candidate_id
        JOIN clients    cl ON cl.id = ccm.client_id
        WHERE ccm.feedback_token_candidate = $1
        """,
        token,
    )
    if not row:
        raise HTTPException(403, "Invalid feedback link")

    return {
        "data": {
            "mapping_id":     str(row["mapping_id"]),
            "feedback":       row["feedback_candidate"],
            "candidate_name": row["candidate_name"],
            "company_name":   row["company_name"],
            "job_title":      row["job_title"],
            "job_location":   row["job_location"],
        },
        "ok": True,
    }


@router.post("/candidate-feedback/{token}")
async def submit_candidate_feedback(
    token: str,
    body: CandidateFeedbackBody,
    conn: asyncpg.Connection = Depends(get_conn),
):
    """Candidate submits join / no_join decision."""
    if body.decision not in ("join", "no_join"):
        raise HTTPException(400, "decision must be 'join' or 'no_join'")

    row = await conn.fetchrow(
        "SELECT id FROM client_candidate_mappings WHERE feedback_token_candidate = $1",
        token,
    )
    if not row:
        raise HTTPException(403, "Invalid feedback link")

    await conn.execute(
        """
        UPDATE client_candidate_mappings
        SET feedback_candidate = $1, updated_at = now()
        WHERE id = $2
        """,
        body.decision, row["id"],
    )

    return {"data": {"submitted": True}, "ok": True}


# ── helpers ───────────────────────────────────────────────────────────────────

def _fmt_phone(phone: str) -> str:
    phone = phone.strip().lstrip("+")
    if len(phone) == 10 and phone.isdigit():
        return "91" + phone
    return phone


def _initials(name: str | None) -> str:
    if not name:
        return "?"
    parts = name.strip().split()
    return "".join(p[0].upper() for p in parts[:2] if p)


def _job_stability(experience: str | None) -> str:
    """Derive a simple stability label from experience text until job_history is added."""
    if not experience:
        return "Unknown"
    exp = experience.lower().strip()
    # Extract numeric value
    try:
        val = float("".join(c for c in exp if c.isdigit() or c == ".").strip())
        if "month" in exp:
            val = val / 12
    except (ValueError, AttributeError):
        return "Unknown"
    if val > 2:
        return "High Stability"
    if val >= 1:
        return "Average Stability"
    return "High Turnover Risk"
