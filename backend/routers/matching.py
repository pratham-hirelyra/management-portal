"""
Matching router — Decision Point + Path 1 / Path 2 + slot fan-out.

Fully event-driven; no polling crons. Lock expiry is lazy — every active-lock
query filters on `expires_at IS NULL OR expires_at > now()`. All flows fire from:
  • client onboarded                 → /decision-point  (auto-triggered)
  • client submits interview slots   → /on-slots-submitted (auto via PATCH /recruiter-slots)
  • candidate responds INTERESTED    → flow_engine._handle_interested_response (WA webhook)
  • external eval system finishes    → /on-evaluation-complete

Workflow:
  Step 0 — Decision-point counts free-pool candidates passing hard filters
  with mms > 70 (free now + projected free within 48h), locks/maps as many as
  fit within POOL_CAP, then routes:
    pool >= SLOT_REQUEST_THRESHOLD → Path 1 (request slots from client) — fires once.
    pool <  POOL_CAP                → Path 2 (Branch A or B) keeps sourcing/reaching
                                       out — including in parallel after Path 1 has
                                       already fired — until the pool reaches POOL_CAP.
    pool >= POOL_CAP                → fully capped, no further sourcing.

  Path 1 — On client submits slots → fan out:
    • Interested candidates with slot_booking_sent=false → slot-selection msg.
    • mms > 70 candidates with JD never sent → send JD; on their interest reply,
      /on-candidate-interested sends slot-selection msg.

  Path 2:
    x = max(0, (SLOT_REQUEST_THRESHOLD - active_total) * NULL_EVAL_TO_MMS70_MULTIPLIER)
    Branch A: null-eval free pool ≥ x → send JD to null-eval candidates.
    Branch B: null-eval free pool < x → flag sourcing (qty=x), notify RM.
    Gate: total reached (sourced + interested + not_interested) < GLOBAL_CAP.
    Re-run on /on-evaluation-complete → return to Decision Point.

  Passed-but-waiting (handled in /on-evaluation-complete):
    Candidate now passes (mms > 70) and mapped to a client:
      Client has slots → slot-selection msg.  Else → waiting msg.

Routes:
  POST /matching/{client_id}/decision-point
  POST /matching/{client_id}/run
  POST /matching/{client_id}/on-slots-submitted
  POST /matching/on-evaluation-complete/{candidate_id}
  GET  /matching/{client_id}/scores
  GET  /matching/{client_id}/pool
  GET  /matching/{client_id}/slots
  POST /matching/{client_id}/lock/{candidate_id}
  POST /matching/{client_id}/unlock/{candidate_id}
  POST /matching/{client_id}/candidate/{candidate_id}/book-slot
"""

import math
import os
import uuid
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel
import asyncpg
from database import get_conn
from services.filter_service import filter_candidates
from services.mms_service import score_candidates
import constants.whatsapp_templates as WT

router = APIRouter(prefix="/matching", tags=["matching"])

# ── constants ─────────────────────────────────────────────────────────────────
SLOT_REQUEST_THRESHOLD = 35  # high-quality (mms >= MMS_GATE) candidates needed before asking the client for interview slots
POOL_CAP = 700               # total candidate pool target per client — sourcing/reach-out continues until this is reached
SINGLE_RUN_CAP = 35         # max candidates inserted in one auto-matchmake tap
GLOBAL_CAP = 1500
MMS_GATE = 70.0
NULL_EVAL_TO_MMS70_MULTIPLIER = 20  # null-eval candidates to reach per still-needed mms>=70 candidate (accounts for AI-eval dropoff)

# matching_state values:
#   None / NULL    — never run / pre-onboard
#   'path1'        — Path 1 active (client asked for slots)
#   'path2_a'      — Path 2 Branch A: null-eval candidates being contacted
#   'path2_b'      — Path 2 Branch B: sourcing flagged
#   'capped'       — GLOBAL_CAP breached, RM escalated


# ── idempotent migrations ─────────────────────────────────────────────────────
async def _ensure_columns(conn: asyncpg.Connection) -> None:
    for ddl in [
        "ALTER TABLE clients ADD COLUMN IF NOT EXISTS matching_state text",
        "ALTER TABLE clients ADD COLUMN IF NOT EXISTS is_ca_firm boolean DEFAULT false",
        "ALTER TABLE client_candidate_mappings ADD COLUMN IF NOT EXISTS slot_booking_sent boolean DEFAULT false",
        "ALTER TABLE client_candidate_mappings ADD COLUMN IF NOT EXISTS waiting_message_sent boolean DEFAULT false",
        "ALTER TABLE client_candidate_mappings ADD COLUMN IF NOT EXISTS waitlisted_message_sent boolean DEFAULT false",
        "ALTER TYPE mapping_stage ADD VALUE IF NOT EXISTS 'waitlisted'",
    ]:
        try:
            await conn.execute(ddl)
        except Exception:
            pass  # enum value already exists


# ── helpers ───────────────────────────────────────────────────────────────────

async def _load_client(client_id: uuid.UUID, conn: asyncpg.Connection) -> dict:
    row = await conn.fetchrow("SELECT * FROM clients WHERE id = $1", client_id)
    if not row:
        raise HTTPException(404, "Client not found")
    return dict(row)


async def _fetch_road_distances(client: dict, candidates: list[dict]) -> dict:
    """Pre-fetch road distances (km) from the client's location to each candidate.
    Returns {str(candidate_id): distance_km}. Skips candidates without coordinates
    or if the client itself has no coords. Errors fall back to empty map (callers
    use haversine as a backstop)."""
    from services import gmaps_service
    clat, clng = client.get("location_lat"), client.get("location_lng")
    if not (clat and clng) or not candidates:
        return {}
    destinations = [
        (str(c["id"]), float(c["location_lat"]), float(c["location_lng"]))
        for c in candidates
        if c.get("location_lat") and c.get("location_lng")
    ]
    if not destinations:
        return {}
    try:
        raw = await gmaps_service.road_distances(float(clat), float(clng), destinations)
        return {k: v["distance_km"] for k, v in raw.items() if "distance_km" in v}
    except Exception as e:
        print(f"[matching] road_distances fetch failed: {e}")
        return {}


_ACTIVE_STAGES_EXCL = (
    'placed', 'rejected', 'not_interested', 'not_looking_for_job',
    'declined_for_interview', 'waitlisted',
)

async def _free_pool_evaluated(client_id: uuid.UUID, conn: asyncpg.Connection) -> list[dict]:
    """Evaluated-pass candidates with fewer than 3 active client mappings (multi-client race model)."""
    rows = await conn.fetch(
        """
        SELECT c.*
        FROM candidates c
        WHERE lower(c.evaluation_status) IN ('pass', 'passed')
          AND c.technical_evaluation_score IS NOT NULL
          AND c.category IS NOT NULL
          AND c.category != 'Reject'
          AND (c.dnd_until IS NULL OR c.dnd_until < now())
          AND c.is_active = true
          AND (
              SELECT COUNT(*) FROM client_candidate_mappings ccm
              WHERE ccm.candidate_id = c.id
                AND ccm.stage::text NOT IN (
                    'placed','rejected','rejected_by_client','not_interested','not_looking_for_job',
                    'declined_for_interview','waitlisted','client_closed'
                )
          ) < 3
        """,
    )
    return [dict(r) for r in rows]


async def _free_pool_null_eval(client_id: uuid.UUID, conn: asyncpg.Connection) -> list[dict]:
    """Un-evaluated candidates not locked by any OTHER client."""
    rows = await conn.fetch(
        """
        SELECT c.*
        FROM candidates c
        WHERE c.technical_evaluation_score IS NULL
          AND (c.dnd_until IS NULL OR c.dnd_until < now())
          AND c.is_active = true
          AND NOT EXISTS (
              SELECT 1 FROM candidate_locks cl
              WHERE cl.candidate_id = c.id
                AND cl.client_id   != $1
                AND cl.unlocked_at IS NULL
                AND (cl.expires_at IS NULL OR cl.expires_at > now())
          )
        """,
        client_id,
    )
    return [dict(r) for r in rows]




async def _persist_scores(
    client_id: uuid.UUID,
    passed: list[dict],
    rejected: list[dict],
    scored: list[dict],
    conn: asyncpg.Connection,
) -> list[uuid.UUID]:
    """Upsert mms_scores rows. Returns list of DND-flagged candidate ids."""
    for r in rejected:
        await conn.execute(
            """
            INSERT INTO mms_scores
                (client_id, candidate_id, filter_passed, filter_exclusion_reason, updated_at)
            VALUES ($1, $2::uuid, false, $3, now())
            ON CONFLICT (client_id, candidate_id)
            DO UPDATE SET
                filter_passed           = false,
                filter_exclusion_reason = EXCLUDED.filter_exclusion_reason,
                updated_at              = now()
            """,
            client_id, r["candidate_id"], r["reason"],
        )

    dnd_ids: list[uuid.UUID] = []
    for s in scored:
        cand_id = uuid.UUID(str(s["id"]))
        await conn.execute(
            """
            INSERT INTO mms_scores (
                client_id, candidate_id, mms_score,
                technical_component, proximity_component, salary_component, skills_component,
                scoring_path, filter_passed, travel_time_minutes, bts,
                classified_category, dnd_flagged, updated_at
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,true,$9,$10,$11,$12,now())
            ON CONFLICT (client_id, candidate_id)
            DO UPDATE SET
                mms_score            = EXCLUDED.mms_score,
                technical_component  = EXCLUDED.technical_component,
                proximity_component  = EXCLUDED.proximity_component,
                salary_component     = EXCLUDED.salary_component,
                skills_component     = EXCLUDED.skills_component,
                scoring_path         = EXCLUDED.scoring_path,
                filter_passed        = true,
                travel_time_minutes  = EXCLUDED.travel_time_minutes,
                bts                  = EXCLUDED.bts,
                classified_category  = EXCLUDED.classified_category,
                dnd_flagged          = EXCLUDED.dnd_flagged,
                updated_at           = now()
            """,
            client_id, cand_id,
            s["mms_score"], s["technical_component"], s["proximity_component"],
            s["salary_component"], s["skills_component"],
            s["scoring_path"], s.get("travel_time_minutes"), s.get("bts"),
            s["classified_category"], s["dnd_flagged"],
        )
        # Sync score back to existing mapping row so portal displays it correctly
        await conn.execute(
            """
            UPDATE client_candidate_mappings
            SET match_score = $3, updated_at = now()
            WHERE client_id = $1 AND candidate_id = $2
              AND (match_score IS NULL OR match_score != $3)
            """,
            client_id, cand_id, int(round(s["mms_score"])),
        )
        if s["dnd_flagged"]:
            dnd_ids.append(cand_id)

    if dnd_ids:
        dnd_until = datetime.now(timezone.utc) + timedelta(days=365)
        await conn.execute(
            "UPDATE candidates SET dnd_until = $2, is_active = false, updated_at = now() WHERE id = ANY($1::uuid[])",
            dnd_ids, dnd_until,
        )
    return dnd_ids


async def _global_cap_count(client_id: uuid.UUID, conn: asyncpg.Connection) -> int:
    """sourced + interested + not_interested = every mapping ever created for this client."""
    return await conn.fetchval(
        "SELECT COUNT(*) FROM client_candidate_mappings WHERE client_id = $1",
        client_id,
    ) or 0


async def _notify_rm(client_id: uuid.UUID, message: str, conn: asyncpg.Connection) -> None:
    import services.msg91_service as msg91
    row = await conn.fetchrow(
        "SELECT rm_id, company_name FROM clients WHERE id = $1", client_id
    )
    if not row or not row["rm_id"]:
        return
    rm = await conn.fetchrow("SELECT phone FROM rms WHERE id = $1", row["rm_id"])
    if not rm or not rm["phone"]:
        return
    try:
        await msg91.send_text(rm["phone"], message, sender="client")
    except Exception as e:
        print(f"[matching] RM notify failed for {client_id}: {e}")


async def _set_state(
    client_id: uuid.UUID,
    state: str | None,
    conn: asyncpg.Connection,
) -> None:
    await conn.execute(
        "UPDATE clients SET matching_state = $1, updated_at = now() WHERE id = $2",
        state, client_id,
    )




# ── Path 1: ask client for interview slots ────────────────────────────────────
async def _trigger_path1(client_id: uuid.UUID, client: dict, conn: asyncpg.Connection) -> bool:
    import services.msg91_service as msg91

    # Atomically claim path1 — serialises concurrent on_evaluation_complete calls
    # so the slot-request message is sent exactly once.
    result = await conn.execute(
        """
        UPDATE clients SET matching_state = 'path1', updated_at = now()
        WHERE id = $1 AND (matching_state IS DISTINCT FROM 'path1')
        """,
        client_id,
    )
    if result == "UPDATE 0":
        return False

    # If the client already submitted slots, don't re-ask them for slots.
    # Instead queue the JD for any 'matched' candidates so they can express
    # intent first. mark_interested will send the slot link once they reply.
    if await _client_has_slots(client_id, conn):
        from routers.cron import _build_jd_components
        matched_candidates = await conn.fetch(
            """
            SELECT c.*
            FROM client_candidate_mappings ccm
            JOIN candidates c ON c.id = ccm.candidate_id
            WHERE ccm.client_id = $1 AND ccm.stage = 'matched'::mapping_stage
            """,
            client_id,
        )
        if matched_candidates:
            await _send_jd_to_candidates(client_id, client, [dict(r) for r in matched_candidates], conn)
        return True

    poc_phone = client.get("poc_phone") or ""
    if not poc_phone:
        return False

    import asyncio
    await asyncio.sleep(60)  # let agreement-confirmed + RM intro messages land first

    frontend_url = os.environ.get("FRONTEND_URL", "http://localhost:5173").rstrip("/")
    feedback_token = client.get("feedback_token")
    portal_url = f"{frontend_url}/client/{feedback_token}" if feedback_token else f"{frontend_url}/schedule/{client_id}"
    template = WT.CLIENT_SLOT
    try:
        if template:
            components = [{"type": "body", "parameters": [
                {"type": "text", "text": portal_url},
            ]}]
            await msg91.send_template(poc_phone, template, "en", components, sender="client")
        else:
            await msg91.send_text(
                poc_phone,
                f"We have shortlisted candidates ready for you! 🎉\n\n"
                f"Please pick your preferred interview slots:\n{portal_url}",
                sender="client",
            )
        # Stamp all matched mappings so the slot-reminder cron can track non-responses
        await conn.execute(
            """
            UPDATE client_candidate_mappings
            SET stage            = 'slot_requested_client'::mapping_stage,
                slot_requested_at = now(),
                updated_at        = now()
            WHERE client_id = $1
              AND stage     = 'matched'::mapping_stage
            """,
            client_id,
        )
        return True
    except Exception as e:
        print(f"[matching] path1 slot request failed for {client_id}: {e}")
        return False


# ── Path 2: null-eval reachout OR sourcing flag ───────────────────────────────
async def _trigger_path2(
    client_id: uuid.UUID,
    client: dict,
    active_total: int,
    conn: asyncpg.Connection,
    include_muslim: bool = True,
) -> dict:
    """Decide Branch A vs B based on null-eval pool size and GLOBAL_CAP gate.

    active_total: candidates already locked/mapped to this client (mms >= MMS_GATE,
    non-terminal) after _fill_pool_and_route has topped up from the free pool.
    Sourcing targets the remaining gap to SLOT_REQUEST_THRESHOLD (35 mms>=70
    candidates), scaled by NULL_EVAL_TO_MMS70_MULTIPLIER to account for AI-eval
    dropoff among null-eval candidates reached. Worst case (active_total=0) is
    35 * 20 = 700, which is under POOL_CAP by construction.
    """
    if client.get("stop_sourcing"):
        return {"branch": "none", "x": 0, "reason": "stop_sourcing"}

    # hl_client_search_started is now sent at onboarding time (clients.py), not here

    x = max(0, (SLOT_REQUEST_THRESHOLD - active_total) * NULL_EVAL_TO_MMS70_MULTIPLIER)
    if x <= 0:
        return {"branch": "none", "x": 0}

    # Null-eval candidates that pass hard filters (free pool)
    null_eval_all = await _free_pool_null_eval(client_id, conn)
    null_passed, _ = filter_candidates(null_eval_all, client, include_muslim=include_muslim)

    # Exclude already-contacted (any prior mapping for this client)
    contacted_rows = await conn.fetch(
        "SELECT candidate_id FROM client_candidate_mappings WHERE client_id = $1",
        client_id,
    )
    contacted = {str(r["candidate_id"]) for r in contacted_rows}
    eligible = [c for c in null_passed if str(c["id"]) not in contacted]

    if len(eligible) >= x:
        # Branch A — reach out to null-eval candidates
        await _set_state(client_id, "path2_a", conn)
        sent = await _send_jd_to_candidates(client_id, client, eligible[:x], conn)
        return {"branch": "a", "x": x, "eligible": len(eligible), "sent": sent}

    # Branch B — flag sourcing
    await _set_state(client_id, "path2_b", conn)
    await conn.execute(
        """
        UPDATE clients
        SET sourcing_needed       = true,
            sourcing_count_needed = $1,
            labels = array_append(array_remove(COALESCE(labels, '{}'), 'Sourcing Needed'), 'Sourcing Needed'),
            updated_at            = now()
        WHERE id = $2
        """,
        x, client_id,
    )
    job_title   = client.get("job_title") or "—"
    job_type    = client.get("job_type") or "—"
    max_sal     = client.get("max_salary")
    sal_str     = f"≤ ₹{int(max_sal):,}" if max_sal else "—"
    location    = client.get("job_location") or client.get("location") or "—"
    office_type = (client.get("office_type") or "").capitalize() or "—"
    gender_req  = (client.get("gender_requirements") or "").strip()
    sourcing_msg = (
        f"*Sourcing needed — {client.get('company_name')}*\n"
        f"Target: *{x} candidates*  |  Available now: {len(eligible)}\n\n"
        f"*Hard filters (source only within these):*\n"
        f"• Role: {job_title}\n"
        f"• Type: {job_type}\n"
        f"• Salary: {sal_str} current salary\n"
        f"• Location: {location} ({office_type})\n"
        f"• No freshers (must have prior accounting experience)\n"
        f"• No Part-Time candidates for Full-Time role"
        + (f"\n• Gender: {gender_req} only" if gender_req and gender_req.lower() != "any" else "")
    )
    await _notify_rm(client_id, sourcing_msg, conn)

    # Still blast JD to whatever null-eval candidates ARE available — don't leave them idle
    sent = 0
    if eligible:
        sent = await _send_jd_to_candidates(client_id, client, eligible, conn)

    return {"branch": "b", "x": x, "available_null_eval": len(eligible), "jd_sent": sent}


# ── Fill pool from free candidates, then route to Path 1 / Path 2 ─────────────
async def _fill_pool_and_route(
    client_id: uuid.UUID,
    client: dict,
    new_scored: list[dict],
    active_mappings: int,
    conn: asyncpg.Connection,
    include_muslim: bool = True,
    trigger_path2: bool = True,
) -> dict:
    """Lock/map as many qualifying free candidates (mms >= MMS_GATE, not dnd_flagged)
    as fit within POOL_CAP, then:
      • trigger Path 1 (interview-slot request) once the pool reaches
        SLOT_REQUEST_THRESHOLD — idempotent, fires only once per client.
      • trigger Path 2 (sourcing/reach-out) while the pool is still below
        POOL_CAP — runs even after Path 1 has already fired, so sourcing
        continues toward POOL_CAP in parallel.

    new_scored: scored free-pool candidates (not yet mapped to this client).
    active_mappings: count of already-mapped, non-terminal, Pass+mms>=MMS_GATE candidates.
    """
    if client.get("stage") in ("churned", "disqualified", "deal_won"):
        return {"decision": "client_inactive", "high_quality_free": 0, "active_total": active_mappings, "pool_cap": POOL_CAP}
    if client.get("stop_sourcing"):
        return {"decision": "stop_sourcing", "high_quality_free": 0, "active_total": active_mappings, "pool_cap": POOL_CAP}

    qualifying_free = [s for s in new_scored if s["mms_score"] >= MMS_GATE and not s["dnd_flagged"]]
    high_quality_free = len(qualifying_free)

    room = max(0, POOL_CAP - active_mappings)
    qualifying = qualifying_free[:min(room, SINGLE_RUN_CAP)]
    for s in qualifying:
        await conn.execute(
            """
            INSERT INTO client_candidate_mappings
                (client_id, candidate_id, stage, match_score, created_at, updated_at)
            VALUES ($1, $2::uuid, 'matched'::mapping_stage, $3, now(), now())
            ON CONFLICT (client_id, candidate_id) DO NOTHING
            """,
            client_id, str(s["id"]), int(round(s["mms_score"])),
        )
        # Pass candidates use the multi-client race model — no lock on match
    active_total = active_mappings + len(qualifying)

    result: dict = {
        "high_quality_free": high_quality_free,
        "active_total":      active_total,
        "pool_cap":          POOL_CAP,
        # Candidates just inserted as 'matched' above — callers that don't run
        # their own wa_sent_at IS NULL catch-up (e.g. on_evaluation_complete's
        # pool top-up) need this to know who still needs their JD queued.
        "newly_matched":     qualifying,
    }

    if trigger_path2 and active_total < POOL_CAP:
        result["path2"] = await _trigger_path2(client_id, client, active_total, conn, include_muslim=include_muslim)

    result["decision"] = "path2" if "path2" in result else "capped"

    return result


async def _send_jd_to_candidates(
    client_id: uuid.UUID,
    client: dict,
    candidates: list[dict],
    conn: asyncpg.Connection,
) -> int:
    """Create mappings + lock + send JD template. Returns count sent."""
    import services.msg91_service as msg91
    from routers.cron import _build_jd_components, _jd_intro

    if not candidates:
        return 0

    components = _build_jd_components(client)

    # Generate JD card image once at match time and persist it on the client row
    from services.jd_card_service import generate_jd_card
    try:
        jd_card_url = await generate_jd_card(client)
        await conn.execute(
            "UPDATE clients SET jd_card_url = $1 WHERE id = $2",
            jd_card_url, client_id,
        )
        print(f"[matching] JD card generated for client {client_id}: {jd_card_url}")
    except Exception as _img_err:
        print(f"[matching] JD card generation failed for client {client_id}: {_img_err}")
        jd_card_url = None

    mapping_rows: list[dict] = []
    area = client.get("job_location") or client.get("location") or ""
    salary = str(int(client["max_salary"])) if client.get("max_salary") else ""

    for cand in candidates:
        cid = cand["id"]
        phone = cand.get("phone") or ""
        if not phone:
            continue

        mapping = await conn.fetchrow(
            """
            INSERT INTO client_candidate_mappings
                (client_id, candidate_id, stage, batch_number, created_at, updated_at)
            VALUES ($1, $2, 'intent_ask'::mapping_stage, 1, now(), now())
            ON CONFLICT (client_id, candidate_id) DO NOTHING
            RETURNING id
            """,
            client_id, cid,
        )
        if not mapping:
            mapping = await conn.fetchrow(
                "SELECT id FROM client_candidate_mappings WHERE client_id = $1 AND candidate_id = $2",
                client_id, cid,
            )
        if not mapping:
            continue

        cand_eval_status = (cand.get("evaluation_status") or "").lower()
        if cand_eval_status not in ("pass", "passed"):
            await conn.execute(
                """
                INSERT INTO candidate_locks (candidate_id, client_id)
                VALUES ($1, $2)
                ON CONFLICT (candidate_id, client_id)
                DO UPDATE SET locked_at = now(), unlocked_at = NULL, unlock_reason = NULL
                """,
                cid, client_id,
            )
        first_name = (cand.get("name") or "").split()[0]
        mapping_rows.append({
            "mapping_id": str(mapping["id"]),
            "phone": msg91._format_phone(phone),
            "candidate_id": cid,
            "name": first_name,
            "intro": _jd_intro(cand, client),
            "template_name": msg91.FF_TEMPLATE if cand.get("form_submitted") else msg91.JD_V3_TEMPLATE,
            "area": area,
            "salary": salary,
            "company_name": client.get("company_name") or "",
            "role": client.get("job_title") or "",
        })

    if not mapping_rows:
        return 0

    JD_BATCH_SIZE  = 25
    JD_BATCH_DELAY = 300  # seconds between batches

    import json as _json
    from datetime import datetime, timezone, timedelta

    batches = [mapping_rows[i:i + JD_BATCH_SIZE] for i in range(0, len(mapping_rows), JD_BATCH_SIZE)]
    components_json = _json.loads(_json.dumps(components, default=str))

    for idx, batch in enumerate(batches):
        scheduled_at = datetime.now(timezone.utc) + timedelta(seconds=idx * JD_BATCH_DELAY)
        await conn.execute(
            """
            INSERT INTO jd_send_queue
                (client_id, batch_number, mapping_rows, components, scheduled_at)
            VALUES ($1, $2, $3::jsonb, $4::jsonb, $5)
            """,
            client_id, idx + 1,
            _json.dumps(batch, default=str),
            _json.dumps(components_json, default=str),
            scheduled_at,
        )

    print(f"[matching] Queued {len(batches)} JD batches ({len(mapping_rows)} candidates) for client {client_id}")
    return len(mapping_rows)


# ── slot fan-out ──────────────────────────────────────────────────────────────

async def _send_slot_message_to_candidate(
    mapping_id: uuid.UUID,
    candidate_id: uuid.UUID,
    phone: str,
    candidate_name: str,
    client: dict,
    conn: asyncpg.Connection,
) -> bool:
    """Send slot selection message to one candidate. Idempotent via slot_booking_sent.

    The invite flow (invite_candidate_for_client) sends this synchronously
    right after client_portal.set_interview_slots, which also schedules the
    on_slots_submitted background fan-out — both target the exact same
    "just invited, not yet sent" candidate. A plain SELECT-then-UPDATE guard
    left a race window where both call sites could read slot_booking_sent as
    false before either wrote true, producing a duplicate WhatsApp message.
    Claiming atomically via UPDATE...WHERE...RETURNING closes that window:
    whichever caller's UPDATE lands first wins the row; the other sees zero
    rows affected and returns immediately without sending.
    """
    import services.msg91_service as msg91

    claimed = await conn.fetchrow(
        """
        UPDATE client_candidate_mappings
        SET slot_booking_sent = true
        WHERE id = $1 AND COALESCE(slot_booking_sent, false) = false
        RETURNING interview_round
        """,
        mapping_id,
    )
    if not claimed:
        return False
    is_round2 = bool((claimed["interview_round"] or 1) >= 2)

    # v4 is the only surviving button version — v2 (MARKETING) and v3
    # (superseded, no "Action Required") were deleted from Meta.
    _SLOT_SELECT_BUTTON_TEMPLATES = {WT.CANDIDATE_SLOT_SELECT}
    # round2_v3 had a button with a hardcoded (non-dynamic) URL — broken,
    # would've sent every candidate to the same sample link — deleted from
    # Meta. round2_v4 is the surviving version with a real {{1}} param.
    _ROUND2_BUTTON_TEMPLATES = {WT.CANDIDATE_SLOT_SELECT_ROUND2}

    portal_url = os.environ.get("CANDIDATE_PORTAL_URL", os.environ.get("FRONTEND_URL", "http://localhost:5174")).rstrip("/")
    slot_url = f"{portal_url}/c/{candidate_id}"
    template = WT.CANDIDATE_SLOT_SELECT
    round2_template = WT.CANDIDATE_SLOT_SELECT_ROUND2
    try:
        if is_round2 and round2_template in _ROUND2_BUTTON_TEMPLATES:
            components = [
                {"type": "body", "parameters": [
                    {"type": "text", "text": candidate_name or ""},
                    {"type": "text", "text": client.get("company_name") or ""},
                    {"type": "text", "text": client.get("job_title")    or ""},
                ]},
                {"type": "button", "sub_type": "url", "index": "0", "parameters": [
                    {"type": "text", "text": f"c/{candidate_id}"},
                ]},
            ]
            await msg91.send_template(phone, round2_template, "en", components, sender="candidate")
        elif is_round2 and round2_template:
            components = [{"type": "body", "parameters": [
                {"type": "text", "text": candidate_name or ""},
                {"type": "text", "text": client.get("company_name") or ""},
                {"type": "text", "text": client.get("job_title")    or ""},
                {"type": "text", "text": slot_url},
            ]}]
            await msg91.send_template(phone, round2_template, "en", components, sender="candidate")
        elif not is_round2 and template in _SLOT_SELECT_BUTTON_TEMPLATES:
            # These versions moved the link to a URL button (dynamic suffix
            # only — base URL is baked into the approved template) instead of
            # embedding the raw link in the body text. The original
            # hl_cand_slot_select template's approved body still expects the
            # link as a 4th body param (see below) — only used for that name.
            components = [
                {"type": "body", "parameters": [
                    {"type": "text", "text": candidate_name or ""},
                    {"type": "text", "text": client.get("company_name") or ""},
                    {"type": "text", "text": client.get("job_title")    or ""},
                ]},
                {"type": "button", "sub_type": "url", "index": "0", "parameters": [
                    {"type": "text", "text": f"c/{candidate_id}"},
                ]},
            ]
            await msg91.send_template(phone, template, "en", components, sender="candidate")
        elif not is_round2 and template:
            components = [{"type": "body", "parameters": [
                {"type": "text", "text": candidate_name or ""},
                {"type": "text", "text": client.get("company_name") or ""},
                {"type": "text", "text": client.get("job_title")    or ""},
                {"type": "text", "text": slot_url},
            ]}]
            await msg91.send_template(phone, template, "en", components, sender="candidate")
        elif is_round2:
            await msg91.send_text(
                phone,
                f"Hi {candidate_name or 'there'}! 🎯 Action Required: Great news — "
                f"{client.get('company_name')} would like to meet you again for a *second round* "
                f"for the {client.get('job_title') or 'role'} role. Pick your slot now to lock it in:\n{slot_url}",
                sender="candidate",
            )
        else:
            await msg91.send_text(
                phone,
                f"Hi {candidate_name or 'there'}! 🎯 Action Required: {client.get('company_name')} wants "
                f"to interview you for the {client.get('job_title') or 'role'} — pick your slot now to "
                f"lock it in:\n{slot_url}",
                sender="candidate",
            )

        await conn.execute(
            """
            UPDATE client_candidate_mappings
            SET slot_sent_at = now(),
                wa_sent_at = COALESCE(wa_sent_at, now()),
                stage = CASE
                    WHEN stage = 'interview_scheduled'::mapping_stage THEN stage
                    ELSE 'slot_sent_candidate'::mapping_stage
                END,
                updated_at = now()
            WHERE id = $1
            """,
            mapping_id,
        )
        await conn.execute(
            """
            INSERT INTO whatsapp_messages
                (client_id, candidate_id, mapping_id, message_type, direction, phone, status, sent_at)
            VALUES ($1, $2, $3, 'candidate_slot', 'outbound', $4, 'sent', now())
            """,
            client["id"], candidate_id, mapping_id, phone,
        )
        return True
    except Exception as e:
        print(f"[matching] slot msg failed for mapping {mapping_id}: {e}")
        # Release the claim so a later invite/fan-out retry can still send.
        await conn.execute(
            "UPDATE client_candidate_mappings SET slot_booking_sent = false WHERE id = $1",
            mapping_id,
        )
        return False


async def _send_waiting_message(
    mapping_id: uuid.UUID,
    candidate_id: uuid.UUID,
    phone: str,
    candidate_name: str,
    client: dict,
    conn: asyncpg.Connection,
) -> bool:
    import services.msg91_service as msg91

    already = await conn.fetchval(
        "SELECT waiting_message_sent FROM client_candidate_mappings WHERE id = $1",
        mapping_id,
    )
    if already:
        return False

    template = WT.CANDIDATE_WAITING
    try:
        if template:
            components = [{"type": "body", "parameters": [
                {"type": "text", "text": candidate_name or ""},
                {"type": "text", "text": client.get("company_name") or ""},
                {"type": "text", "text": client.get("job_title")    or ""},
            ]}]
            await msg91.send_template(phone, template, "en", components, sender="candidate")
        else:
            first_name = (candidate_name or "there").split()[0]
            await msg91.send_text(
                phone,
                f"Hi {first_name}, {client.get('company_name')} is reviewing your profile for the "
                f"{client.get('job_title') or 'Accounting'} role! If they'd like to move forward, "
                f"we'll send you the interview slot link.",
                sender="candidate",
            )
        await conn.execute(
            "UPDATE client_candidate_mappings SET waiting_message_sent = true, updated_at = now() WHERE id = $1",
            mapping_id,
        )
        return True
    except Exception as e:
        print(f"[matching] waiting msg failed for {mapping_id}: {e}")
        return False


_GENERIC_MISMATCH_REASON = "this client has specific requirements for this role that didn't line up with your profile"


def _candidate_facing_mismatch_reason(filter_exclusion_reason: str | None) -> str:
    """Map the internal filter_exclusion_reason (from filter_service.filter_candidates)
    to a candidate-safe phrase. Gender and religion filters are deliberately never
    disclosed — those fall through to the generic fallback, same as soft MMS-score
    misses and geocoding data gaps that aren't real mismatches."""
    r = (filter_exclusion_reason or "").lower()
    if "salary" in r:
        return "your salary expectations are a bit above this role's budget"
    if "travel radius" in r or "client radius" in r:
        if "geocod" in r:
            return _GENERIC_MISMATCH_REASON
        return "the office location is a bit outside your preferred commute area"
    if "employment type" in r:
        return "this role is full-time and your profile shows you're looking for part-time"
    if "role mismatch" in r:
        return "this role needs more senior-level experience than your current profile shows"
    return _GENERIC_MISMATCH_REASON


async def _send_role_not_fit_message(
    phone: str, candidate_name: str, company_name: str, job_title: str,
    reason: str = _GENERIC_MISMATCH_REASON,
) -> bool:
    """Tell candidate they don't fit THIS specific role but we'll share other opportunities."""
    if not phone:
        return False
    import services.msg91_service as msg91
    template = WT.CANDIDATE_ROLE_NOT_FIT
    try:
        if template == "hl_cand_role_not_fit_v2":
            # v2 template's approved body has a 4th placeholder for the reason.
            # The old template (still configured until v2 is approved) only
            # has 3 — see elif below.
            components = [{"type": "body", "parameters": [
                {"type": "text", "text": candidate_name or ""},
                {"type": "text", "text": company_name or ""},
                {"type": "text", "text": job_title or ""},
                {"type": "text", "text": reason},
            ]}]
            await msg91.send_template(phone, template, "en", components, sender="candidate")
        elif template:
            components = [{"type": "body", "parameters": [
                {"type": "text", "text": candidate_name or ""},
                {"type": "text", "text": company_name or ""},
                {"type": "text", "text": job_title or ""},
            ]}]
            await msg91.send_template(phone, template, "en", components, sender="candidate")
        else:
            await msg91.send_text(
                phone,
                f"Hi {candidate_name or 'there'}, the {job_title or 'role'} at {company_name or 'this client'} "
                f"isn't the right match right now — {reason}. We'll keep your profile active and reach out "
                f"as soon as a better-fitting role comes up!",
                sender="candidate",
            )
        return True
    except Exception as e:
        print(f"[matching] role-not-fit msg failed for {phone}: {e}")
        return False


async def _send_waitlisted_message(
    mapping_id: uuid.UUID,
    candidate_id: uuid.UUID,
    phone: str,
    candidate_name: str,
    client: dict,
    conn: asyncpg.Connection,
) -> bool:
    """Notify candidate they are waitlisted — slots full for this client."""
    if not phone:
        return False
    import services.msg91_service as msg91

    already = await conn.fetchval(
        "SELECT waitlisted_message_sent FROM client_candidate_mappings WHERE id = $1",
        mapping_id,
    )
    if already:
        return False

    template = WT.CANDIDATE_WAITLISTED
    try:
        if template:
            components = [{"type": "body", "parameters": [
                {"type": "text", "text": candidate_name or ""},
                {"type": "text", "text": client.get("company_name") or ""},
            ]}]
            await msg91.send_template(phone, template, "en", components, sender="candidate")
        else:
            await msg91.send_text(
                phone,
                f"Hi {candidate_name or 'there'}, we already have many candidates lined up for "
                f"interview at {client.get('company_name') or 'this company'} and will update you "
                f"if there are more requirements from them or any of them gets rejected. Till then "
                f"we will show you newer opportunities which match your preferences. "
                f"Thank you for showing the interest! 😊",
                sender="candidate",
            )
        await conn.execute(
            "UPDATE client_candidate_mappings SET waitlisted_message_sent = true, updated_at = now() WHERE id = $1",
            mapping_id,
        )
        return True
    except Exception as e:
        print(f"[matching] waitlisted msg failed for {mapping_id}: {e}")
        return False


async def _notify_waitlisted_position_closed(client_id: uuid.UUID, client: dict, conn: asyncpg.Connection) -> int:
    """
    Send 'position closed' message to all waitlisted candidates for this client.
    Called when a placement is confirmed (any mapping → placed).
    """
    import services.msg91_service as msg91

    rows = await conn.fetch(
        """
        SELECT ccm.id AS mapping_id, ccm.candidate_id, c.phone, c.name
        FROM client_candidate_mappings ccm
        JOIN candidates c ON c.id = ccm.candidate_id
        WHERE ccm.client_id = $1
          AND ccm.stage     = 'waitlisted'::mapping_stage
        """,
        client_id,
    )
    if not rows:
        return 0

    template = WT.CANDIDATE_POSITION_CLOSED
    company = client.get("company_name") or ""
    sent = 0
    for r in rows:
        phone = r["phone"] or ""
        name  = r["name"]  or ""
        if not phone:
            continue
        try:
            if template:
                components = [{"type": "body", "parameters": [
                    {"type": "text", "text": name},
                    {"type": "text", "text": company},
                ]}]
                await msg91.send_template(phone, template, "en", components, sender="candidate")
            else:
                await msg91.send_text(
                    phone,
                    f"Hi {name or 'there'}, the position at {company or 'this company'} has been "
                    f"filled. But don't worry — there are newer opportunities matching your profile "
                    f"and we'll share them with you soon. Thank you for your patience! 😊",
                    sender="candidate",
                )
            sent += 1
        except Exception as e:
            print(f"[matching] position-closed msg failed for mapping {r['mapping_id']}: {e}")

    # Move all waitlisted → rejected so they don't show again
    await conn.execute(
        """
        UPDATE client_candidate_mappings
        SET stage = 'rejected'::mapping_stage, rejection_reason = 'position_filled', updated_at = now()
        WHERE client_id = $1 AND stage = 'waitlisted'::mapping_stage
        """,
        client_id,
    )
    return sent


async def _client_has_slots(client_id: uuid.UUID, conn: asyncpg.Connection) -> bool:
    row = await conn.fetchrow(
        "SELECT available_slots, recruiter_slots FROM clients WHERE id = $1", client_id
    )
    if not row:
        return False
    import json as _j
    for col in ("available_slots", "recruiter_slots"):
        slots = row[col]
        if isinstance(slots, str):
            try: slots = _j.loads(slots)
            except Exception: slots = []
        if slots:
            return True
    return False


# ── public endpoints ──────────────────────────────────────────────────────────

@router.post("/{client_id}/decision-point")
async def decision_point(
    client_id: uuid.UUID,
    conn: asyncpg.Connection = Depends(get_conn),
    include_muslim: bool = True,
):
    """
    Step 0 — Run hard filters + score over free pool, count high-quality candidates
    (free now + projected free within 48h), then route immediately to Path 1 or Path 2.
    Fires automatically on client onboarded; safe to call again on AI-eval completion.
    """
    await _ensure_columns(conn)
    client = await _load_client(client_id, conn)

    # If the pool is already full, do nothing — slots open only when client rejects
    # or a candidate is placed. Only Pass-evaluated candidates with MMS ≥ 70 count
    # toward POOL_CAP. Null-eval candidates (Path 2 outreach), failed candidates,
    # and low-MMS candidates do not occupy a pool slot.
    _terminal_stages = ('placed', 'rejected_by_client', 'rejected')
    active_mappings = await conn.fetchval(
        """
        SELECT COUNT(*) FROM client_candidate_mappings ccm
        JOIN candidates c ON c.id = ccm.candidate_id
        LEFT JOIN mms_scores ms ON ms.client_id = ccm.client_id AND ms.candidate_id = ccm.candidate_id
        WHERE ccm.client_id = $1
          AND ccm.stage::text != ALL($2::text[])
          AND lower(c.evaluation_status) IN ('pass', 'passed')
          AND COALESCE(ms.mms_score, ccm.match_score, 0) >= $3
        """,
        client_id, list(_terminal_stages), MMS_GATE,
    ) or 0
    if active_mappings >= POOL_CAP:
        # Already capped — graduate to path1 if stuck in path2
        if client.get("matching_state") in ("path2_a", "path2_b"):
            await _trigger_path1(client_id, client, conn)
        return {
            "data": {"decision": "capped_active", "active_mappings": active_mappings, "pool_cap": POOL_CAP},
            "ok": True,
        }

    # Score the evaluated free pool
    # Road distances used only for travel-radius hard filter — scoring uses haversine
    # so stored match_score stays consistent with the hover breakdown.
    evaluated = await _free_pool_evaluated(client_id, conn)
    distances = await _fetch_road_distances(client, evaluated)
    passed, rejected = filter_candidates(evaluated, client, distances, include_muslim=include_muslim)
    scored = score_candidates(passed, client)
    await _persist_scores(client_id, passed, rejected, scored, conn)

    # Exclude already-mapped candidates from free-pool counts and qualifying lists
    existing_rows = await conn.fetch(
        "SELECT candidate_id FROM client_candidate_mappings WHERE client_id = $1", client_id
    )
    already_mapped_ids = {str(r["candidate_id"]) for r in existing_rows}
    new_scored = [s for s in scored if str(s["id"]) not in already_mapped_ids]

    result = await _fill_pool_and_route(client_id, client, new_scored, active_mappings, conn, include_muslim=include_muslim)

    # Send JD to already-mapped evaluated MMS≥70 candidates not yet contacted.
    # Covers candidates mapped manually or from a previous decision-point run
    # who never received a message (wa_sent_at IS NULL).
    _ts = ('placed', 'rejected_by_client', 'rejected', 'waitlisted')
    mapped_not_sent = await conn.fetch(
        """
        SELECT c.*
        FROM mms_scores ms
        JOIN candidates c ON c.id = ms.candidate_id
        JOIN client_candidate_mappings ccm
               ON ccm.client_id = ms.client_id AND ccm.candidate_id = c.id
        WHERE ms.client_id     = $1
          AND ms.filter_passed = true
          AND ms.mms_score    >= $2
          AND ms.dnd_flagged   = false
          AND (c.dnd_until IS NULL OR c.dnd_until < now())
          AND c.is_active      = true
          AND lower(c.evaluation_status) IN ('pass', 'passed')
          AND ccm.wa_sent_at  IS NULL
          AND ccm.stage::text != ALL($3::text[])
        """,
        client_id, MMS_GATE, list(_ts),
    )
    jd_sent = await _send_jd_to_candidates(client_id, client, [dict(r) for r in mapped_not_sent], conn)
    result["jd_sent_evaluated"] = jd_sent

    return {"data": result, "ok": True}


@router.post("/{client_id}/run")
async def run_matching(
    client_id: uuid.UUID,
    conn: asyncpg.Connection = Depends(get_conn),
):
    """Manual/admin re-score over the free pool. Does NOT trigger Path 1/2."""
    await _ensure_columns(conn)
    client = await _load_client(client_id, conn)

    evaluated = await _free_pool_evaluated(client_id, conn)
    distances = await _fetch_road_distances(client, evaluated)
    passed, rejected = filter_candidates(evaluated, client, distances)
    scored = score_candidates(passed, client)
    dnd_ids = await _persist_scores(client_id, passed, rejected, scored, conn)

    high_quality = sum(1 for s in scored if s["mms_score"] >= MMS_GATE and not s["dnd_flagged"])
    return {
        "data": {
            "total_candidates":  len(evaluated),
            "passed_filter":     len(passed),
            "rejected_filter":   len(rejected),
            "scored":            len(scored),
            "high_quality":      high_quality,
            "dnd_applied":       len(dnd_ids),
            "slot_request_threshold": SLOT_REQUEST_THRESHOLD,
            "pool_cap":          POOL_CAP,
            "candidates":        [_slim(s) for s in scored],
        },
        "ok": True,
    }


@router.post("/{client_id}/on-slots-submitted")
async def on_slots_submitted(
    client_id: uuid.UUID,
    conn: asyncpg.Connection = Depends(get_conn),
):
    """
    Fan-out triggered when the client submits interview slots.
    Only sends slot booking links to candidates already in 'invited_for_interview' stage.
    No JD blast — JD is sent earlier in the flow; slot link is sent at invite time.
    """
    await _ensure_columns(conn)

    client_row = await conn.fetchrow(
        "SELECT id, company_name, job_title FROM clients WHERE id = $1", client_id
    )
    client_dict = dict(client_row) if client_row else {"id": client_id, "company_name": None, "job_title": None}

    invited = await conn.fetch(
        """
        SELECT ccm.id AS mapping_id, ccm.candidate_id,
               c.name AS candidate_name, c.phone AS candidate_phone
        FROM client_candidate_mappings ccm
        JOIN candidates c ON c.id = ccm.candidate_id
        WHERE ccm.client_id         = $1
          AND ccm.stage             = 'invited_for_interview'::mapping_stage
          AND ccm.slot_booking_sent = false
        """,
        client_id,
    )

    slot_sent = 0
    for row in invited:
        await _send_slot_message_to_candidate(
            row["mapping_id"],
            row["candidate_id"],
            row["candidate_phone"] or "",
            row["candidate_name"] or "",
            client_dict,
            conn,
        )
        slot_sent += 1

    return {
        "data": {"slot_messages_sent": slot_sent},
        "ok": True,
    }


# ── event: AI evaluation completed for a candidate ────────────────────────────

@router.post("/on-evaluation-complete/{candidate_id}")
async def on_evaluation_complete(
    candidate_id: uuid.UUID,
    conn: asyncpg.Connection = Depends(get_conn),
):
    """
    Fire when the external eval system finishes scoring a candidate.
    For each client this candidate is mapped to:
      • Re-run decision-point (free pool just got a new entry).
      • If candidate already passes (mms > 70) for any mapped client, send the
        slot-selection or waiting message immediately (passed-but-waiting case).
    """
    await _ensure_columns(conn)

    mappings = await conn.fetch(
        """
        SELECT ccm.id AS mapping_id, ccm.client_id,
               ccm.slot_booking_sent, ccm.waiting_message_sent,
               c.name AS candidate_name, c.phone
        FROM client_candidate_mappings ccm
        JOIN candidates c ON c.id = ccm.candidate_id
        JOIN clients    cl ON cl.id = ccm.client_id
        WHERE ccm.candidate_id = $1
          AND ccm.stage NOT IN (
                'not_interested'::mapping_stage,
                'rejected'::mapping_stage,
                'placed'::mapping_stage,
                'interview_done'::mapping_stage,
                'interview_scheduled'::mapping_stage,
                'client_closed'::mapping_stage
              )
          AND cl.stage NOT IN ('churned'::client_stage, 'disqualified'::client_stage, 'deal_won'::client_stage)
        """,
        candidate_id,
    )

    slot_sent = waiting_sent = reruns = 0
    seen_clients: set = set()

    for m in mappings:
        cid = m["client_id"]
        client = await _load_client(cid, conn)
        # Re-score this client (refreshes the candidate's mms entry)
        evaluated = await _free_pool_evaluated(cid, conn)
        distances = await _fetch_road_distances(client, evaluated)
        passed, rejected = filter_candidates(evaluated, client, distances)
        scored = score_candidates(passed, client)
        await _persist_scores(cid, passed, rejected, scored, conn)

        # If the candidate now passes for this client → send slot / waiting message
        ms = await conn.fetchrow(
            """
            SELECT mms_score, filter_passed, dnd_flagged, filter_exclusion_reason FROM mms_scores
            WHERE client_id = $1 AND candidate_id = $2
            """,
            cid, candidate_id,
        )
        if ms and ms["filter_passed"] and not ms["dnd_flagged"] \
                and float(ms["mms_score"] or 0) >= MMS_GATE:
            phone = m["phone"] or ""
            name  = m["candidate_name"] or ""

            # Hard cap: count non-terminal Pass+MMS≥70 mappings already active for this client
            _terminal = ('placed', 'rejected_by_client', 'rejected', 'waitlisted')
            active_count = await conn.fetchval(
                """
                SELECT COUNT(*) FROM client_candidate_mappings ccm
                JOIN candidates c ON c.id = ccm.candidate_id
                LEFT JOIN mms_scores ms2
                       ON ms2.client_id = ccm.client_id AND ms2.candidate_id = ccm.candidate_id
                WHERE ccm.client_id = $1
                  AND ccm.stage::text != ALL($2::text[])
                  AND lower(c.evaluation_status) IN ('pass', 'passed')
                  AND COALESCE(ms2.mms_score, ccm.match_score, 0) >= $3
                """,
                cid, list(_terminal), MMS_GATE,
            ) or 0

            if active_count > POOL_CAP:
                # Slots are full — waitlist this candidate, unlock them for other clients
                await conn.execute(
                    """
                    UPDATE client_candidate_mappings
                    SET stage = 'waitlisted'::mapping_stage, updated_at = now()
                    WHERE id = $1
                    """,
                    m["mapping_id"],
                )
                await conn.execute(
                    """
                    UPDATE candidate_locks
                    SET unlocked_at = now(), unlock_reason = 'cap_reached'
                    WHERE candidate_id = $1 AND client_id = $2 AND unlocked_at IS NULL
                    """,
                    candidate_id, cid,
                )
                await _send_waitlisted_message(
                    m["mapping_id"], candidate_id, phone, name, client, conn,
                )
            else:
                # Move to approval queue regardless of whether client has slots yet —
                # RM reviews the candidate first, slots are collected separately.
                moved = await conn.execute(
                    """
                    UPDATE client_candidate_mappings
                    SET stage = 'client_approval_pending'::mapping_stage, updated_at = now()
                    WHERE id = $1
                      AND stage NOT IN (
                            'client_approval_pending'::mapping_stage,
                            'slot_sent_candidate'::mapping_stage,
                            'slot_booked'::mapping_stage
                          )
                    """,
                    m["mapping_id"],
                )
                slot_sent += 1
                if not m["waiting_message_sent"]:
                    if await _send_waiting_message(
                        m["mapping_id"], candidate_id, phone, name, client, conn,
                    ):
                        waiting_sent += 1
                if moved != "UPDATE 0":
                    try:
                        from services.flow_engine import notify_client_pending_review
                        await notify_client_pending_review(cid, name or "A candidate", conn)
                    except Exception as e:
                        print(f"[matching] client pending-review notify error for mapping {m['mapping_id']}: {e}")
        else:
            # Candidate's evaluation came back but they don't qualify for this client
            # (mms < 70 / filter failed / DND). Release them so other clients can match,
            # and let the candidate know we'll share other opportunities.
            await conn.execute(
                """
                UPDATE client_candidate_mappings
                SET stage = 'rejected'::mapping_stage, rejection_reason = 'filters_not_matched', updated_at = now()
                WHERE id = $1
                """,
                m["mapping_id"],
            )
            await conn.execute(
                """
                UPDATE candidate_locks
                SET unlocked_at = now(), unlock_reason = 'mms_below_gate'
                WHERE candidate_id = $1 AND client_id = $2 AND unlocked_at IS NULL
                """,
                candidate_id, cid,
            )
            await _send_role_not_fit_message(
                m["phone"] or "", m["candidate_name"] or "",
                client.get("company_name") or "", client.get("job_title") or "",
                reason=_candidate_facing_mismatch_reason(ms["filter_exclusion_reason"] if ms else None),
            )

        # Pool top-up — keep locking/sourcing toward POOL_CAP, regardless of matching_state,
        # and graduate path2 → path1 once SLOT_REQUEST_THRESHOLD is reached.
        if cid not in seen_clients:
            _pool_terminal = ('placed', 'rejected_by_client', 'rejected', 'waitlisted')
            total_active = await conn.fetchval(
                """
                SELECT COUNT(*) FROM client_candidate_mappings ccm
                JOIN candidates c ON c.id = ccm.candidate_id
                LEFT JOIN mms_scores ms2
                       ON ms2.client_id = ccm.client_id AND ms2.candidate_id = ccm.candidate_id
                WHERE ccm.client_id = $1
                  AND ccm.stage::text != ALL($2::text[])
                  AND lower(c.evaluation_status) IN ('pass', 'passed')
                  AND COALESCE(ms2.mms_score, ccm.match_score, 0) >= $3
                """,
                cid, list(_pool_terminal), MMS_GATE,
            ) or 0

            if total_active < POOL_CAP:
                # Free pool — candidates not yet mapped to this client but already
                # evaluated and passing filters (e.g. freed from another client).
                existing_rows = await conn.fetch(
                    "SELECT candidate_id FROM client_candidate_mappings WHERE client_id = $1", cid
                )
                already_mapped_ids = {str(r["candidate_id"]) for r in existing_rows}
                new_scored = [s for s in scored if str(s["id"]) not in already_mapped_ids]
                # on_evaluation_complete only maps already-evaluated free-pool
                # candidates here — it does NOT trigger Path 2 (null-eval
                # sourcing/JD outreach to new candidates). That stays exclusive
                # to decision_point (the original auto-matchmaking flow on
                # client onboarding / manual re-match).
                fill_result = await _fill_pool_and_route(cid, client, new_scored, total_active, conn, trigger_path2=False)
                # Queue (not send) the JD for whatever just got mapped into
                # 'matched' above — decision_point has its own wa_sent_at
                # IS NULL catch-up query, but this top-up path doesn't, so
                # without this these mappings sit in 'matched' forever.
                newly_matched = fill_result.get("newly_matched") or []
                if newly_matched:
                    await _send_jd_to_candidates(cid, client, newly_matched, conn)
            elif client.get("matching_state") in ("path2_a", "path2_b"):
                await _trigger_path1(cid, client, conn)
            reruns += 1
        seen_clients.add(cid)

    return {
        "data": {
            "mappings_processed":    len(mappings),
            "slot_messages_sent":    slot_sent,
            "waiting_messages_sent": waiting_sent,
            "decision_point_reruns": reruns,
        },
        "ok": True,
    }


# ── Inject sourced candidates ─────────────────────────────────────────────────

class SourcingCandidate(BaseModel):
    name: str
    phone: str
    location: str | None = None
    source: str | None = None
    source_id: str | None = None
    salary: float | None = None
    gender: str | None = None


@router.post("/{client_id}/inject-sourced")
async def inject_sourced(
    client_id: uuid.UUID,
    body: list[SourcingCandidate],
    conn: asyncpg.Connection = Depends(get_conn),
):
    """
    Insert externally sourced candidates and immediately send them the JD for this client.
    - Upserts each candidate (source='sourced', form_submitted=false, category from client job_title).
    - Skips candidates already mapped to this client.
    - Sends JD to newly added candidates.
    """
    client = await _load_client(client_id, conn)

    from routers.clients import geocode

    # Geocode unique locations up front
    import re as _re
    unique_locs = list({c.location for c in body if c.location})
    geo_cache: dict[str, tuple] = {}
    for loc in unique_locs:
        geo_cache[loc] = await geocode(loc)

    already_mapped = {
        str(r["candidate_id"])
        for r in await conn.fetch(
            "SELECT candidate_id FROM client_candidate_mappings WHERE client_id = $1",
            client_id,
        )
    }

    to_send: list[dict] = []
    for c in body:
        digits = _re.sub(r"\D", "", c.phone)
        if len(digits) == 12 and digits.startswith("91"):
            digits = digits[2:]
        phone_db = digits if len(digits) == 10 else c.phone

        lat, lng, city, state = geo_cache.get(c.location, (None, None, None, None)) if c.location else (None, None, None, None)
        source_val = c.source or "sourced"
        # Convert annual salary to monthly (Apna and most portals send annual figures)
        monthly_salary = (c.salary / 12) if c.salary and c.salary > 50000 else c.salary

        row = await conn.fetchrow(
            """
            INSERT INTO candidates (
                name, phone, source, source_id,
                current_location, location_lat, location_lng, city, state,
                current_salary, gender, form_submitted, is_active, created_at, updated_at
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, false, true, now(), now())
            ON CONFLICT (phone) DO UPDATE
                SET name             = EXCLUDED.name,
                    source           = EXCLUDED.source,
                    source_id        = COALESCE(EXCLUDED.source_id, candidates.source_id),
                    current_location = COALESCE(EXCLUDED.current_location, candidates.current_location),
                    location_lat     = COALESCE(EXCLUDED.location_lat, candidates.location_lat),
                    location_lng     = COALESCE(EXCLUDED.location_lng, candidates.location_lng),
                    city             = COALESCE(EXCLUDED.city, candidates.city),
                    state            = COALESCE(EXCLUDED.state, candidates.state),
                    current_salary   = CASE WHEN candidates.form_submitted
                                         THEN candidates.current_salary
                                         ELSE COALESCE(EXCLUDED.current_salary, candidates.current_salary)
                                     END,
                    gender           = COALESCE(EXCLUDED.gender, candidates.gender),
                    updated_at       = now()
            RETURNING id, name, phone
            """,
            c.name, phone_db, source_val, c.source_id,
            c.location, lat, lng, city, state, monthly_salary, c.gender,
        )
        if str(row["id"]) not in already_mapped:
            to_send.append({"id": row["id"], "name": row["name"], "phone": row["phone"]})

    sent = await _send_jd_to_candidates(client_id, client, to_send, conn)

    return {
        "data": {
            "injected": len(to_send),
            "jd_sent": sent,
            "skipped_already_mapped": len(body) - len(to_send),
        },
        "ok": True,
    }


@router.post("/inject-sourced")
async def inject_sourced_global(
    body: list[SourcingCandidate],
    conn: asyncpg.Connection = Depends(get_conn),
):
    """
    Insert externally sourced candidates straight into the free pool — no
    client involved, no JD sent. They sit exactly like any other sourced
    candidate (source='sourced', form_submitted=false) and only get matched to
    a client once the normal matching flow (decision_point / on_evaluation_complete)
    or a manual /inject-sourced/{client_id} call picks them up.
    """
    from routers.clients import geocode

    # Geocode unique locations up front
    import re as _re
    unique_locs = list({c.location for c in body if c.location})
    geo_cache: dict[str, tuple] = {}
    for loc in unique_locs:
        geo_cache[loc] = await geocode(loc)

    inserted: list[dict] = []
    for c in body:
        digits = _re.sub(r"\D", "", c.phone)
        if len(digits) == 12 and digits.startswith("91"):
            digits = digits[2:]
        phone_db = digits if len(digits) == 10 else c.phone

        lat, lng, city, state = geo_cache.get(c.location, (None, None, None, None)) if c.location else (None, None, None, None)
        source_val = c.source or "sourced"
        # Convert annual salary to monthly (Apna and most portals send annual figures)
        monthly_salary = (c.salary / 12) if c.salary and c.salary > 50000 else c.salary

        row = await conn.fetchrow(
            """
            INSERT INTO candidates (
                name, phone, source, source_id,
                current_location, location_lat, location_lng, city, state,
                current_salary, gender, form_submitted, is_active, created_at, updated_at
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, false, true, now(), now())
            ON CONFLICT (phone) DO UPDATE
                SET name             = EXCLUDED.name,
                    source           = EXCLUDED.source,
                    source_id        = COALESCE(EXCLUDED.source_id, candidates.source_id),
                    current_location = COALESCE(EXCLUDED.current_location, candidates.current_location),
                    location_lat     = COALESCE(EXCLUDED.location_lat, candidates.location_lat),
                    location_lng     = COALESCE(EXCLUDED.location_lng, candidates.location_lng),
                    city             = COALESCE(EXCLUDED.city, candidates.city),
                    state            = COALESCE(EXCLUDED.state, candidates.state),
                    current_salary   = CASE WHEN candidates.form_submitted
                                         THEN candidates.current_salary
                                         ELSE COALESCE(EXCLUDED.current_salary, candidates.current_salary)
                                     END,
                    gender           = COALESCE(EXCLUDED.gender, candidates.gender),
                    updated_at       = now()
            RETURNING id, name, phone
            """,
            c.name, phone_db, source_val, c.source_id,
            c.location, lat, lng, city, state, monthly_salary, c.gender,
        )
        inserted.append({"id": str(row["id"]), "name": row["name"], "phone": row["phone"]})

    return {
        "data": {
            "injected": len(inserted),
            "candidates": inserted,
        },
        "ok": True,
    }


# ── existing endpoints (preserved) ────────────────────────────────────────────

@router.get("/{client_id}/scores")
async def get_scores(
    client_id: uuid.UUID,
    min_mms: float = Query(0),
    limit: int = Query(50),
    conn: asyncpg.Connection = Depends(get_conn),
):
    rows = await conn.fetch(
        """
        SELECT ms.*, c.name, c.phone, c.category, c.current_salary,
               c.technical_evaluation_score, c.cv_url, c.final_evaluation_report_url,
               c.current_location, c.work_preference, c.other_details
        FROM mms_scores ms
        JOIN candidates c ON c.id = ms.candidate_id
        WHERE ms.client_id     = $1
          AND ms.filter_passed = true
          AND ms.mms_score    >= $2
          AND ms.dnd_flagged   = false
        ORDER BY ms.mms_score DESC
        LIMIT $3
        """,
        client_id, min_mms, limit,
    )
    def _with_display(r: dict) -> dict:
        d = dict(r)
        d["display_mms_score"] = round(min(float(d.get("mms_score") or 0), 100.0), 2)
        return d
    return {"data": [_with_display(r) for r in rows], "count": len(rows), "ok": True}


@router.get("/{client_id}/pool")
async def pool_depth(
    client_id: uuid.UUID,
    min_mms: float = Query(MMS_GATE),
    conn: asyncpg.Connection = Depends(get_conn),
):
    locked_for_us = await conn.fetchval(
        """
        SELECT COUNT(*) FROM candidate_locks
        WHERE client_id = $1
          AND unlocked_at IS NULL
          AND (expires_at IS NULL OR expires_at > now())
        """,
        client_id,
    )
    free = await conn.fetchval(
        """
        SELECT COUNT(*)
        FROM client_candidate_mappings ccm
        LEFT JOIN mms_scores ms
               ON ms.client_id = ccm.client_id AND ms.candidate_id = ccm.candidate_id
        WHERE ccm.client_id = $1
          AND COALESCE(ms.mms_score, ccm.match_score, 0) >= $2
          AND COALESCE(ms.filter_passed, true) = true
          AND COALESCE(ms.dnd_flagged, false) = false
          AND NOT EXISTS (
              SELECT 1 FROM candidate_locks cl
              WHERE cl.candidate_id = ccm.candidate_id
                AND cl.client_id   != $1
                AND cl.unlocked_at IS NULL
                AND (cl.expires_at IS NULL OR cl.expires_at > now())
          )
        """,
        client_id, min_mms,
    )
    locked_by_other = await conn.fetchval(
        """
        SELECT COUNT(DISTINCT ccm.candidate_id)
        FROM client_candidate_mappings ccm
        JOIN candidate_locks cl ON cl.candidate_id = ccm.candidate_id
        WHERE ccm.client_id = $1 AND ccm.match_score >= $2
          AND cl.unlocked_at IS NULL
          AND (cl.expires_at IS NULL OR cl.expires_at > now())
          AND cl.client_id != $1
        """,
        client_id, min_mms,
    )
    earliest_unlock = await conn.fetchval(
        """
        SELECT MIN(cl.expires_at)
        FROM client_candidate_mappings ccm
        JOIN candidate_locks cl ON cl.candidate_id = ccm.candidate_id
        WHERE ccm.client_id = $1
          AND cl.unlocked_at IS NULL
          AND (cl.expires_at IS NULL OR cl.expires_at > now())
          AND cl.client_id != $1
        """,
        client_id,
    )
    locked_for_us   = locked_for_us or 0
    free            = free or 0
    locked_by_other = locked_by_other or 0
    total           = locked_for_us + free + locked_by_other
    if total < POOL_CAP:
        pool_status = "trigger_sourcing"
    elif free < 5:
        pool_status = "schedule_message"
    else:
        pool_status = "proceed"
    return {
        "data": {
            "locked_count":    locked_for_us,
            "free_count":      free,
            "locked_by_other": locked_by_other,
            "total_count":     total,
            "pool_status":     pool_status,
            "earliest_unlock": earliest_unlock.isoformat() if earliest_unlock else None,
            "pool_cap":        POOL_CAP,
        },
        "ok": True,
    }


@router.post("/{client_id}/lock/{candidate_id}")
async def lock_candidate(
    client_id: uuid.UUID,
    candidate_id: uuid.UUID,
    interview_slot: datetime | None = None,
    conn: asyncpg.Connection = Depends(get_conn),
):
    expires_at = (interview_slot + timedelta(hours=24)) if interview_slot else None
    await conn.execute(
        """
        INSERT INTO candidate_locks (candidate_id, client_id, expires_at)
        VALUES ($1, $2, $3)
        ON CONFLICT (candidate_id, client_id)
        DO UPDATE SET locked_at = now(), expires_at = EXCLUDED.expires_at, unlocked_at = NULL, unlock_reason = NULL
        """,
        candidate_id, client_id, expires_at,
    )
    return {"ok": True}


@router.post("/{client_id}/unlock/{candidate_id}")
async def unlock_candidate(
    client_id: uuid.UUID,
    candidate_id: uuid.UUID,
    reason: str = Query("manual"),
    conn: asyncpg.Connection = Depends(get_conn),
):
    await conn.execute(
        """
        UPDATE candidate_locks
        SET unlocked_at = now(), unlock_reason = $3
        WHERE candidate_id = $1 AND client_id = $2 AND unlocked_at IS NULL
        """,
        candidate_id, client_id, reason,
    )
    return {"ok": True}


@router.get("/{client_id}/slots")
async def get_client_slots(
    client_id: uuid.UUID,
    conn: asyncpg.Connection = Depends(get_conn),
):
    row = await conn.fetchrow("SELECT recruiter_slots FROM clients WHERE id = $1", client_id)
    if not row:
        raise HTTPException(404, "Client not found")
    import json as _j
    slots = row["recruiter_slots"] or []
    if isinstance(slots, str):
        slots = _j.loads(slots)
    return {"data": slots, "ok": True}


@router.post("/{client_id}/candidate/{candidate_id}/book-slot")
async def book_slot(
    client_id: uuid.UUID,
    candidate_id: uuid.UUID,
    slot_iso: str,
    mapping_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    conn: asyncpg.Connection = Depends(get_conn),
):
    import json as _j
    try:
        requested_dt = datetime.fromisoformat(slot_iso)
    except ValueError:
        raise HTTPException(400, "Invalid ISO datetime for slot_iso")

    # Block booking if this candidate is still locked by another client — slot must be after their lock expires
    other_lock = await conn.fetchrow(
        """
        SELECT expires_at FROM candidate_locks
        WHERE candidate_id = $1
          AND client_id    != $2
          AND unlocked_at  IS NULL
          AND (expires_at IS NULL OR expires_at > now())
        ORDER BY expires_at DESC NULLS LAST
        LIMIT 1
        """,
        candidate_id, client_id,
    )
    if other_lock and other_lock["expires_at"]:
        earliest_allowed = other_lock["expires_at"]
        if requested_dt < earliest_allowed:
            raise HTTPException(409, {
                "reason": "candidate_locked",
                "earliest_allowed": earliest_allowed.isoformat(),
                "message": f"This candidate is busy until {earliest_allowed.strftime('%-d %b, %I:%M %p')}. Please pick a slot after that.",
            })

    conflict = await conn.fetchrow(
        """
        SELECT id FROM client_candidate_mappings
        WHERE candidate_id = $1
          AND interview_slot IS NOT NULL
          AND ABS(EXTRACT(EPOCH FROM (interview_slot - $2::timestamptz))) < 3600
          AND id != $3
        """,
        candidate_id, requested_dt, mapping_id,
    )
    if conflict:
        row = await conn.fetchrow("SELECT recruiter_slots FROM clients WHERE id = $1", client_id)
        all_slots = row["recruiter_slots"] or []
        if isinstance(all_slots, str):
            all_slots = _j.loads(all_slots)
        for s in all_slots:
            try:
                sdt = datetime.fromisoformat(s.get("iso", ""))
                c2 = await conn.fetchrow(
                    """
                    SELECT id FROM client_candidate_mappings
                    WHERE candidate_id = $1
                      AND interview_slot IS NOT NULL
                      AND ABS(EXTRACT(EPOCH FROM (interview_slot - $2::timestamptz))) < 3600
                    """,
                    candidate_id, sdt,
                )
                if not c2:
                    return {"data": {"conflict": True, "next_available_slot": s}, "ok": True}
            except Exception:
                continue
        return {"data": {"conflict": True, "next_available_slot": None}, "ok": True}

    await conn.execute(
        """
        UPDATE client_candidate_mappings
        SET interview_slot = $1, stage = 'slot_booked'::mapping_stage,
            slot_sent_at = COALESCE(slot_sent_at, now()), updated_at = now()
        WHERE id = $2
        """,
        requested_dt, mapping_id,
    )
    await conn.execute(
        """
        INSERT INTO candidate_locks (candidate_id, client_id, expires_at)
        VALUES ($1, $2, $3)
        ON CONFLICT (candidate_id, client_id)
        DO UPDATE SET locked_at = now(), expires_at = EXCLUDED.expires_at, unlocked_at = NULL
        """,
        candidate_id, client_id, requested_dt + timedelta(hours=24),
    )

    # ── Find slot label from client slots ────────────────────────────────────
    client_row = await conn.fetchrow(
        "SELECT recruiter_slots, available_slots FROM clients WHERE id = $1", client_id
    )
    slot_label = None
    for field in ["recruiter_slots", "available_slots"]:
        slots = client_row[field] or []
        if isinstance(slots, str):
            slots = _j.loads(slots)
        for s in slots:
            try:
                if abs((datetime.fromisoformat(s.get("iso", "")) - requested_dt).total_seconds()) < 60:
                    slot_label = s.get("label")
                    break
            except Exception:
                continue
        if slot_label:
            break
    if not slot_label:
        slot_label = requested_dt.strftime("%-d %b %Y, %I:%M %p")

    background_tasks.add_task(_send_interview_confirmed_to_candidate, str(mapping_id), slot_label)
    background_tasks.add_task(_send_interview_confirmation_to_client_bg, str(mapping_id), slot_label)

    return {"data": {"conflict": False, "interview_slot": slot_iso}, "ok": True}


# ── helpers ───────────────────────────────────────────────────────────────────

async def _send_interview_confirmed_to_candidate(mapping_id: str, slot_label: str) -> None:
    """Send interview confirmation WA to the candidate after RM books a slot."""
    import urllib.parse
    import services.msg91_service as msg91
    from database import get_pool

    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT ca.name, ca.phone, ca.id AS candidate_id,
                   cl.company_name, cl.job_location, cl.location_lat, cl.location_lng, cl.id AS client_id
            FROM client_candidate_mappings m
            JOIN candidates ca ON ca.id = m.candidate_id
            JOIN clients    cl ON cl.id = m.client_id
            WHERE m.id = $1
            """,
            uuid.UUID(mapping_id),
        )
        if not row or not row["phone"]:
            return

        name      = row["name"] or "there"
        phone     = row["phone"]
        company   = row["company_name"] or ""
        client_id = row["client_id"]
        cand_id   = row["candidate_id"]

        lat, lng = row.get("location_lat"), row.get("location_lng")
        if lat and lng:
            maps_url = f"https://maps.google.com/?q={lat},{lng}"
        elif row.get("job_location"):
            maps_url = f"https://maps.google.com/?q={urllib.parse.quote(row['job_location'])}"
        else:
            maps_url = None

        location_text = maps_url or row.get("job_location") or "Location TBD"

        template_name = WT.CANDIDATE_INTERVIEW_CONFIRM
        components = [
            {
                "type": "body",
                "parameters": [
                    {"type": "text", "text": name},
                    {"type": "text", "text": company},
                    {"type": "text", "text": slot_label},
                    {"type": "text", "text": location_text},
                ],
            }
        ]

        try:
            await msg91.send_template(phone, template_name, "en", components, sender="candidate")
            await conn.execute(
                """
                INSERT INTO whatsapp_messages
                    (client_id, candidate_id, mapping_id, message_type, direction, phone, status, sent_at)
                VALUES ($1, $2, $3::uuid, 'candidate_interview_confirmed', 'outbound', $4, 'sent', now())
                """,
                client_id, cand_id, mapping_id, phone,
            )
        except Exception as e:
            print(f"[book_slot] candidate confirmation failed for {mapping_id}: {e}")


async def _send_interview_confirmation_to_client_bg(mapping_id: str, slot_label: str) -> None:
    """Thin wrapper so schedule.py's function can be used as a background task."""
    from routers.schedule import _send_interview_confirmation_to_client
    await _send_interview_confirmation_to_client(mapping_id, slot_label)


def _slim(s: dict) -> dict:
    return {
        "candidate_id":         str(s.get("id", "")),
        "name":                 s.get("name"),
        "mms_score":            s.get("mms_score"),
        "display_mms_score":    s.get("display_mms_score"),
        "technical_component":  s.get("technical_component"),
        "proximity_component":  s.get("proximity_component"),
        "salary_component":     s.get("salary_component"),
        "skills_component":     s.get("skills_component"),
        "scoring_path":         s.get("scoring_path"),
        "classified_category":  s.get("classified_category"),
        "dnd_flagged":          s.get("dnd_flagged"),
        "travel_time_minutes":  s.get("travel_time_minutes"),
    }
