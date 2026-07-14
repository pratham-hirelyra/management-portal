import os
import uuid
import json
import math
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
import asyncpg
import httpx
from database import get_conn
from models.mapping import MappingCreate, MappingStageUpdate
from services.matching_service import (
    match_candidates, MIN_TO_NOTIFY, MMS_HIGH_QUALITY, TOP_N,
    haversine_km,
)
from services.mms_service import compute_score_breakdown, score_candidates as mms_score_candidates

router = APIRouter(prefix="/clients", tags=["mappings"])


def _fmt_phone(phone: str) -> str:
    phone = phone.strip().lstrip("+")
    if len(phone) == 10 and phone.isdigit():
        return "91" + phone
    return phone


CANDIDATE_FIELDS = """
    ccm.*,
    c.name               AS candidate_name,
    c.phone              AS candidate_phone,
    c.email              AS candidate_email,
    c.age_years          AS candidate_age,
    c.gender             AS candidate_gender,
    c.current_location,
    c.experience,
    c.category,
    c.form_submitted,
    c.call_status,
    c.evaluation_status,
    c.technical_evaluation_score,
    c.total_score,
    c.industry           AS candidate_industry,
    c.cv_url,
    c.voice_recording_url,
    c.evaluation_pdf_url,
    c.final_evaluation_report_url,
    c.other_details      AS candidate_other_details,
    c.work_preference,
    c.current_salary,
    c.expected_salary,
    c.interview_count    AS total_mapped_count,
    c.location_lat,
    c.location_lng,
    c.dnd_until          AS candidate_dnd_until,
    c.is_active          AS candidate_is_active
"""


@router.get("/{client_id}/mappings")
async def list_mappings(
    client_id: uuid.UUID,
    conn: asyncpg.Connection = Depends(get_conn),
):
    client = await conn.fetchrow("SELECT * FROM clients WHERE id = $1", client_id)
    client_dict = dict(client) if client else {}

    rows = await conn.fetch(
        f"""
        SELECT {CANDIDATE_FIELDS},
               c.working_radius   AS ov_working_radius,
               c.work_preference  AS ov_work_preference,
               c.job_type         AS ov_job_type,
               c.expected_salary  AS ov_expected_salary,
               c.is_active        AS is_active,
               (cl_own.candidate_id IS NOT NULL) AS is_locked,
               (
                   SELECT COUNT(*) FROM client_candidate_mappings m2
                   WHERE m2.candidate_id = ccm.candidate_id
                     AND m2.stage::text NOT IN ('placed','rejected','not_interested','declined_for_interview','waitlisted')
               ) AS active_mapping_count,
               (
                   SELECT string_agg(cl2.company_name, ', ' ORDER BY m2.created_at DESC)
                   FROM client_candidate_mappings m2
                   JOIN clients cl2 ON cl2.id = m2.client_id
                   WHERE m2.candidate_id = ccm.candidate_id
                     AND m2.stage::text NOT IN ('placed','rejected','not_interested','declined_for_interview','waitlisted')
               ) AS active_mapping_clients,
               EXISTS (
                   SELECT 1 FROM candidate_locks cl
                   WHERE cl.candidate_id = ccm.candidate_id
                     AND cl.client_id != ccm.client_id
                     AND cl.unlocked_at IS NULL
                     AND (cl.expires_at IS NULL OR cl.expires_at > now())
               ) AS locked_by_other,
               (
                   SELECT string_agg(lc.company_name, ', ' ORDER BY cl.locked_at DESC)
                   FROM candidate_locks cl
                   JOIN clients lc ON lc.id = cl.client_id
                   WHERE cl.candidate_id = ccm.candidate_id
                     AND cl.client_id != ccm.client_id
                     AND cl.unlocked_at IS NULL
                     AND (cl.expires_at IS NULL OR cl.expires_at > now())
               ) AS locked_by_company
        FROM client_candidate_mappings ccm
        JOIN candidates c ON c.id = ccm.candidate_id
        LEFT JOIN candidate_locks cl_own
               ON cl_own.candidate_id = ccm.candidate_id
              AND cl_own.client_id = ccm.client_id
              AND cl_own.unlocked_at IS NULL
              AND (cl_own.expires_at IS NULL OR cl_own.expires_at > now())
        WHERE ccm.client_id = $1
        ORDER BY ccm.match_score DESC NULLS LAST, ccm.created_at DESC
        """,
        client_id,
    )

    j_lat = float(client_dict.get('location_lat') or 0)
    j_lng = float(client_dict.get('location_lng') or 0)

    data = []
    for r in rows:
        row = dict(r)
        c_lat = float(row.get('location_lat') or 0)
        c_lng = float(row.get('location_lng') or 0)
        row['distance_km'] = (
            round(haversine_km(j_lat, j_lng, c_lat, c_lng), 1)
            if j_lat and j_lng and c_lat and c_lng else None
        )
        row['score_breakdown'] = compute_score_breakdown(row, client_dict)
        data.append(row)

    return {"data": data, "count": len(data), "ok": True}


async def _fetch_road_distances(client: dict, candidates: list[dict]) -> dict[str, dict]:
    """
    Returns {candidate_id_str: {"distance_km": float, "duration_min": int}}
    via Google Maps Distance Matrix API (driving mode).
    Pre-filters with Haversine ≤ 40 km to limit API elements.
    Falls back to empty dict if key is unset or call fails.
    """
    from services.gmaps_service import road_distances as _gmaps_road

    j_lat = float(client.get('location_lat') or 0)
    j_lng = float(client.get('location_lng') or 0)
    if not j_lat or not j_lng:
        return {}

    eligible = [
        c for c in candidates
        if c.get('location_lat') and c.get('location_lng')
        and haversine_km(j_lat, j_lng, float(c['location_lat']), float(c['location_lng'])) <= 40
    ]
    if not eligible:
        return {}

    destinations = [
        (str(c['id']), float(c['location_lat']), float(c['location_lng']))
        for c in eligible
    ]
    return await _gmaps_road(j_lat, j_lng, destinations)


async def run_auto_match_for_client(client_id: uuid.UUID, conn: asyncpg.Connection) -> dict:
    """
    Core matchmaking logic — callable from both the HTTP endpoint and background tasks.
    Returns the same dict the HTTP endpoint would return.
    """
    import os
    import services.msg91_service as msg91

    client = await conn.fetchrow("SELECT * FROM clients WHERE id = $1", client_id)
    if not client:
        return {"ok": False, "flag": "none", "message": "Client not found"}

    client_dict = dict(client)

    existing = await conn.fetch(
        "SELECT candidate_id, stage::text AS stage FROM client_candidate_mappings WHERE client_id = $1",
        client_id,
    )
    already_mapped = {str(r['candidate_id']) for r in existing}

    _TERMINAL = {'placed', 'rejected'}
    active_count = sum(1 for r in existing if r['stage'] not in _TERMINAL)
    slots_needed = max(0, TOP_N - active_count)

    if slots_needed == 0:
        # Pool is full — no new candidates needed
        rows = await conn.fetch(
            f"""
            SELECT {CANDIDATE_FIELDS}
            FROM client_candidate_mappings ccm
            JOIN candidates c ON c.id = ccm.candidate_id
            WHERE ccm.client_id = $1
            ORDER BY ccm.match_score DESC NULLS LAST
            """,
            client_id,
        )
        return {
            "data":               [dict(r) for r in rows],
            "count":              len(rows),
            "created":            0,
            "flag":               "ok",
            "high_quality_count": active_count,
            "need_more_sourcing": False,
            "ok":                 True,
            "message":            f"Pool full ({active_count} active mappings). No new candidates added.",
        }

    all_candidates = await conn.fetch(
        """
        SELECT
            c.id, c.name, c.phone, c.category, c.industry, c.gender, c.religion,
            c.age_years, c.work_preference, c.job_type,
            c.location_lat, c.location_lng, c.working_radius,
            c.current_salary, c.expected_salary,
            c.total_score, c.technical_evaluation_score, c.evaluation_status, c.interview_count,
            c.other_details,
            c.working_radius    AS ov_working_radius,
            c.work_preference   AS ov_work_preference,
            c.job_type          AS ov_job_type,
            c.expected_salary   AS ov_expected_salary,
            c.is_active         AS is_active
        FROM candidates c
        WHERE lower(c.evaluation_status) IN ('pass', 'passed')
        """
    )

    candidate_list = [dict(c) for c in all_candidates]

    # Enrich with real road distances (falls back silently to Haversine in matching)
    road_map = await _fetch_road_distances(client_dict, candidate_list)
    for c in candidate_list:
        cid = str(c.get('id', ''))
        if cid in road_map:
            c['_road_distance_km'] = road_map[cid]['distance_km']
            c['_commute_minutes']  = road_map[cid]['duration_min']

    matched, flag, high_quality_count = match_candidates(
        client_dict,
        candidate_list,
        already_mapped_ids=already_mapped,
        top_n=slots_needed,
    )

    if flag == 'none':
        await conn.execute(
            """
            UPDATE clients
            SET sourcing_needed = true,
                labels = array_append(array_remove(COALESCE(labels, '{}'), 'Sourcing Needed'), 'Sourcing Needed'),
                updated_at = now()
            WHERE id = $1
            """,
            client_id,
        )
        return {
            "data":               [],
            "count":              0,
            "high_quality_count": high_quality_count,
            "ok":                 True,
            "flag":               flag,
            "message":            "No candidates met the score threshold. Source more candidates.",
        }

    # Rescore with mms_service using haversine (no road distances) so the stored
    # score matches the hover breakdown — both use the same distance calculation.
    mms_scored = mms_score_candidates(matched, client_dict)
    mms_score_map = {str(s['id']): int(round(s['mms_score'])) for s in mms_scored}

    created_mappings = []
    for c in matched:
        if str(c['id']) in already_mapped:
            continue
        cand_uuid = uuid.UUID(str(c['id']))
        stored_score = mms_score_map.get(str(c['id']), int(round(c['_match_score'])))
        try:
            row = await conn.fetchrow(
                """
                INSERT INTO client_candidate_mappings (client_id, candidate_id, match_score)
                VALUES ($1, $2, $3)
                ON CONFLICT (client_id, candidate_id) DO NOTHING
                RETURNING *
                """,
                client_id,
                cand_uuid,
                stored_score,
            )
            if row:
                created_mappings.append(dict(row))
            cand_eval = await conn.fetchval(
                "SELECT evaluation_status FROM candidates WHERE id = $1", cand_uuid
            )
            if (cand_eval or "").lower() not in ("pass", "passed"):
                await conn.execute(
                    """
                    INSERT INTO candidate_locks (candidate_id, client_id)
                    VALUES ($1, $2)
                    ON CONFLICT (candidate_id, client_id)
                    DO UPDATE SET locked_at = now(), unlocked_at = NULL, unlock_reason = NULL
                    """,
                    cand_uuid, client_id,
                )
        except Exception:
            pass

    need_more = high_quality_count < 10  # Rule of 10

    await conn.execute(
        """
        UPDATE clients
        SET sourcing_needed = $1,
            labels = CASE
                WHEN $1 THEN array_append(array_remove(COALESCE(labels, '{}'), 'Sourcing Needed'), 'Sourcing Needed')
                ELSE array_remove(COALESCE(labels, '{}'), 'Sourcing Needed')
            END,
            updated_at = now()
        WHERE id = $2
        """,
        need_more, client_id,
    )

    rows = await conn.fetch(
        f"""
        SELECT {CANDIDATE_FIELDS}
        FROM client_candidate_mappings ccm
        JOIN candidates c ON c.id = ccm.candidate_id
        WHERE ccm.client_id = $1
        ORDER BY ccm.match_score DESC NULLS LAST
        """,
        client_id,
    )
    return {
        "data":               [dict(r) for r in rows],
        "count":              len(rows),
        "created":            len(created_mappings),
        "flag":               flag,
        "high_quality_count": high_quality_count,
        "need_more_sourcing": need_more,
        "ok":                 True,
    }


@router.get("/{client_id}/mappings/filter-preview")
async def filter_preview(
    client_id: uuid.UUID,
    conn: asyncpg.Connection = Depends(get_conn),
):
    """
    Returns all candidates who pass hard filters but have no existing mapping for this client.
    Scored with MMS (haversine). Read-only — nothing is written to the DB.
    """
    from services.filter_service import filter_candidates
    from services.mms_service import score_candidates as mms_score, haversine_km, compute_score_breakdown

    client = await conn.fetchrow("SELECT * FROM clients WHERE id = $1", client_id)
    if not client:
        raise HTTPException(404, "Client not found")
    client_dict = dict(client)

    existing = await conn.fetch(
        "SELECT candidate_id FROM client_candidate_mappings WHERE client_id = $1",
        client_id,
    )
    already_mapped = {str(r["candidate_id"]) for r in existing}

    all_candidates = await conn.fetch(
        """
        SELECT c.id, c.name, c.category, c.current_location,
               c.location_lat, c.location_lng, c.working_radius,
               c.current_salary, c.expected_salary,
               c.technical_evaluation_score, c.total_score,
               c.evaluation_status, c.experience, c.gender,
               c.work_preference, c.job_type, c.other_details,
               c.is_active,
               c.working_radius  AS ov_working_radius,
               c.work_preference AS ov_work_preference,
               c.job_type        AS ov_job_type
        FROM candidates c
        WHERE lower(c.evaluation_status) IN ('pass', 'passed')
          AND c.is_active = true
          AND (c.dnd_until IS NULL OR c.dnd_until < now())
        """
    )

    candidate_list = [dict(c) for c in all_candidates]

    # Fetch road distances so travel-radius filter matches decision_point exactly
    road_map = await _fetch_road_distances(client_dict, candidate_list)
    distances = {cid: v["distance_km"] for cid, v in road_map.items() if "distance_km" in v}

    passed, _ = filter_candidates(candidate_list, client_dict, distances)
    not_mapped = [c for c in passed if str(c["id"]) not in already_mapped]
    scored = mms_score(not_mapped, client_dict)

    # Fetch lock info — candidates locked by any client (all locks here are to OTHER clients)
    not_mapped_ids = [c["id"] for c in scored]
    lock_rows = await conn.fetch(
        """
        SELECT cl.candidate_id, lc.company_name AS locked_for_company
        FROM candidate_locks cl
        JOIN clients lc ON lc.id = cl.client_id
        WHERE cl.unlocked_at IS NULL
          AND cl.candidate_id = ANY($1::uuid[])
        """,
        not_mapped_ids,
    )
    locked_map: dict[str, str] = {str(r["candidate_id"]): r["locked_for_company"] for r in lock_rows}

    # Active (non-terminal) mapping count per candidate for the 3-position cap
    active_pos_rows = await conn.fetch(
        """
        SELECT candidate_id, COUNT(*) AS cnt
        FROM client_candidate_mappings
        WHERE candidate_id = ANY($1::uuid[])
          AND stage NOT IN ('placed','rejected','not_interested','declined_for_interview')
        GROUP BY candidate_id
        """,
        not_mapped_ids,
    )
    active_positions_map: dict[str, int] = {str(r["candidate_id"]): r["cnt"] for r in active_pos_rows}

    j_lat = float(client_dict.get("location_lat") or 0)
    j_lng = float(client_dict.get("location_lng") or 0)

    result = []
    for c in scored:
        cid_str = str(c["id"])
        c_lat = float(c.get("location_lat") or 0)
        c_lng = float(c.get("location_lng") or 0)
        dist = (
            round(distances[cid_str], 1) if cid_str in distances
            else round(haversine_km(j_lat, j_lng, c_lat, c_lng), 1)
            if j_lat and j_lng and c_lat and c_lng else None
        )
        result.append({
            "id":                          cid_str,
            "name":                        c.get("name"),
            "category":                    c.get("category"),
            "current_location":            c.get("current_location"),
            "current_salary":              c.get("current_salary"),
            "technical_evaluation_score":  c.get("technical_evaluation_score"),
            "mms_score":                   int(round(c["mms_score"])),
            "distance_km":                 dist,
            "score_breakdown":             compute_score_breakdown(c, client_dict),
            "is_locked":                   cid_str in locked_map,
            "locked_for_company":          locked_map.get(cid_str),
            "active_positions":            active_positions_map.get(cid_str, 0),
        })

    return {"data": result, "count": len(result), "ok": True}


# /auto before /{mapping_id} to avoid route shadowing
class AutoMatchOptions(BaseModel):
    include_muslim: bool = True

@router.post("/{client_id}/mappings/auto")
async def auto_match(
    client_id: uuid.UUID,
    body: AutoMatchOptions = AutoMatchOptions(),
    conn: asyncpg.Connection = Depends(get_conn),
):
    client = await conn.fetchrow("SELECT * FROM clients WHERE id = $1", client_id)
    if not client:
        raise HTTPException(404, "Client not found")

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

    from routers.matching import decision_point
    result = await decision_point(client_id, conn, include_muslim=body.include_muslim)

    filters = {"include_muslim": body.include_muslim}
    matched = 0
    try:
        matched = result.get("data", {}).get("active_total") or 0
    except Exception:
        pass
    await conn.execute(
        """INSERT INTO client_activity_log (client_id, event_type, details, created_by)
           VALUES ($1, 'auto_match', $2, 'rm')""",
        client_id, json.dumps({"filters": filters, "matched": matched}),
    )

    return result


@router.post("/{client_id}/mappings", status_code=201)
async def create_mapping(
    client_id: uuid.UUID,
    body: MappingCreate,
    conn: asyncpg.Connection = Depends(get_conn),
):
    active_count = await conn.fetchval(
        """
        SELECT COUNT(*) FROM client_candidate_mappings
        WHERE candidate_id = $1
          AND stage NOT IN ('placed','rejected','not_interested','declined_for_interview')
        """,
        body.candidate_id,
    )
    if active_count >= 3:
        raise HTTPException(409, "Candidate already has 3 active positions")

    try:
        row = await conn.fetchrow(
            """
            INSERT INTO client_candidate_mappings (client_id, candidate_id, match_score, notes)
            VALUES ($1, $2, $3, $4)
            RETURNING *
            """,
            client_id,
            body.candidate_id,
            body.match_score,
            body.notes,
        )
    except asyncpg.UniqueViolationError:
        raise HTTPException(409, "Candidate already mapped to this client")

    manual_eval = await conn.fetchval(
        "SELECT evaluation_status FROM candidates WHERE id = $1", body.candidate_id
    )
    if (manual_eval or "").lower() not in ("pass", "passed"):
        await conn.execute(
            """
            INSERT INTO candidate_locks (candidate_id, client_id)
            VALUES ($1, $2)
            ON CONFLICT DO NOTHING
            """,
            body.candidate_id,
            client_id,
        )

    # Compute and persist MMS score — bypass hard filters since RM manually chose this candidate
    try:
        from routers.matching import _load_client, _persist_scores, _fetch_road_distances

        client = await _load_client(client_id, conn)
        cand_row = await conn.fetchrow("SELECT * FROM candidates WHERE id = $1", body.candidate_id)
        if cand_row:
            cand = dict(cand_row)
            distances = await _fetch_road_distances(client, [cand])
            scored = mms_score_candidates([cand], client, distances)
            await _persist_scores(client_id, [], [], scored, conn)
    except Exception as _e:
        print(f"[create_mapping] MMS score compute failed: {_e}")

    return {"data": dict(row), "ok": True}


async def apply_placed_cascade(client_id: uuid.UUID, candidate_id: uuid.UUID, conn: asyncpg.Connection) -> None:
    """Post-processing when a mapping transitions to 'placed': mark the candidate hired,
    apply the 12-month DND, close their other active mappings (first hire wins), and
    notify waitlisted candidates. Shared by the internal stage-update endpoint below and
    the company-portal's "Hire Candidate" action (routers/company_portal.py)."""
    client_row = await conn.fetchrow(
        "SELECT company_name, job_title FROM clients WHERE id = $1", client_id
    )
    placement_entry = json.dumps([{
        "client_id": str(client_id),
        "company_name": client_row["company_name"] if client_row else None,
        "job_title": client_row["job_title"] if client_row else None,
        "placed_at": datetime.now(timezone.utc).isoformat(),
    }])
    await conn.execute(
        """
        UPDATE candidates
        SET evaluation_status = 'hired',
            other_details = jsonb_set(
                COALESCE(other_details, '{}'::jsonb),
                '{placements}',
                COALESCE(other_details->'placements', '[]'::jsonb) || $2::jsonb
            ),
            updated_at = now()
        WHERE id = $1
        """,
        candidate_id,
        placement_entry,
    )
    # Hired via JA → 1yr DND, auto re-engages after 12 months
    await conn.execute(
        "UPDATE candidates SET is_active = false, dnd_until = now() + interval '12 months', updated_at = now() WHERE id = $1",
        candidate_id,
    )

    # Close all other active mappings for this candidate (race model — first to hire wins)
    await conn.execute(
        """
        UPDATE client_candidate_mappings
        SET stage            = 'rejected'::mapping_stage,
            rejection_reason = 'placed_elsewhere',
            updated_at       = now()
        WHERE candidate_id = $1
          AND client_id   != $2
          AND stage NOT IN (
              'placed','rejected','not_interested',
              'declined_for_interview','waitlisted'
          )
        """,
        candidate_id, client_id,
    )

    # Notify waitlisted candidates that the position is now filled
    try:
        from routers.matching import _notify_waitlisted_position_closed
        client_full = await conn.fetchrow("SELECT * FROM clients WHERE id = $1", client_id)
        if client_full:
            await _notify_waitlisted_position_closed(client_id, dict(client_full), conn)
    except Exception as e:
        print(f"[mappings] waitlisted position-closed notify failed: {e}")


@router.patch("/{client_id}/mappings/{mapping_id}/stage")
async def update_mapping_stage(
    client_id: uuid.UUID,
    mapping_id: uuid.UUID,
    body: MappingStageUpdate,
    conn: asyncpg.Connection = Depends(get_conn),
):
    stage = body.stage.value
    row = await conn.fetchrow(
        """
        UPDATE client_candidate_mappings SET
            stage               = $1::mapping_stage,
            updated_at          = now(),
            notes               = CASE WHEN $5::text IS NOT NULL THEN $5::text ELSE notes END,
            wa_sent_at          = CASE WHEN $1 = 'intent_ask'    THEN now() ELSE wa_sent_at        END,
            intent_received_at  = CASE WHEN $1 IN ('interested','not_interested')
                                        THEN now() ELSE intent_received_at END,
            slot_sent_at        = CASE WHEN $1 = 'slot_booked'   THEN now() ELSE slot_sent_at      END,
            slot_link_sent_at   = CASE WHEN $1 = 'slot_sent_candidate' THEN now() ELSE slot_link_sent_at END,
            interview_slot      = CASE WHEN $1 = 'slot_booked' AND $2::timestamptz IS NOT NULL
                                        THEN $2::timestamptz ELSE interview_slot END,
            interview_done      = CASE WHEN $1 = 'interview_done' THEN true ELSE interview_done     END,
            interview_done_at   = CASE WHEN $1 = 'interview_done' THEN now() ELSE interview_done_at END,
            placement_confirmed = CASE WHEN $1 = 'placed'         THEN true ELSE placement_confirmed END
        WHERE id = $3 AND client_id = $4
        RETURNING *
        """,
        stage,
        body.interview_slot,
        mapping_id,
        client_id,
        body.notes,
    )
    if not row:
        raise HTTPException(404, "Mapping not found")

    if stage == 'placed':
        await apply_placed_cascade(client_id, row['candidate_id'], conn)

    if stage in ('not_interested', 'rejected'):
        await conn.execute(
            """
            UPDATE candidate_locks
            SET unlocked_at = now(), unlock_reason = $1
            WHERE candidate_id = $2 AND client_id = $3 AND unlocked_at IS NULL
            """,
            stage, row['candidate_id'], client_id,
        )

    return {"data": dict(row), "ok": True}


class ModifySlotBody(BaseModel):
    interview_slot: datetime


@router.post("/{client_id}/mappings/{mapping_id}/modify-slot")
async def modify_interview_slot(
    client_id: uuid.UUID,
    mapping_id: uuid.UUID,
    body: ModifySlotBody,
    conn: asyncpg.Connection = Depends(get_conn),
):
    row = await conn.fetchrow(
        """
        UPDATE client_candidate_mappings
        SET interview_slot           = $1,
            interview_reminder_sent  = false,
            reminder_t2_sent         = false,
            interview_done           = false,
            interview_done_at        = NULL,
            updated_at               = now()
        WHERE id = $2 AND client_id = $3
          AND stage IN ('slot_booked', 'interview_done')
        RETURNING *,
            (SELECT name  FROM candidates WHERE id = candidate_id) AS candidate_name,
            (SELECT phone FROM candidates WHERE id = candidate_id) AS candidate_phone
        """,
        body.interview_slot, mapping_id, client_id,
    )
    if not row:
        raise HTTPException(404, "Mapping not found or not in a modifiable stage")

    client_row = await conn.fetchrow(
        "SELECT company_name, job_title, poc_name, poc_phone FROM clients WHERE id = $1",
        client_id,
    )

    slot_str = body.interview_slot.strftime("%-d %b %Y at %-I:%M %p")

    import services.msg91_service as msg91

    cand_phone = _fmt_phone(row["candidate_phone"] or "")
    if cand_phone:
        try:
            await msg91.send_text(
                cand_phone,
                f"Hi {row['candidate_name'] or 'there'}, your interview with "
                f"{client_row['company_name']} has been rescheduled to {slot_str}. "
                f"Please make sure to be available. Good luck!",
                sender="candidate",
            )
        except Exception as e:
            print(f"[modify-slot] candidate WA failed: {e}")

    poc_phone = _fmt_phone(client_row["poc_phone"] or "")
    if poc_phone:
        try:
            await msg91.send_text(
                poc_phone,
                f"Hi {client_row['poc_name'] or 'there'}, the interview with "
                f"{row['candidate_name']} has been rescheduled to {slot_str}.",
                sender="client",
            )
        except Exception as e:
            print(f"[modify-slot] client WA failed: {e}")

    return {"data": dict(row), "ok": True}


@router.delete("/{client_id}/mappings/{mapping_id}")
async def delete_mapping(
    client_id: uuid.UUID,
    mapping_id: uuid.UUID,
    conn: asyncpg.Connection = Depends(get_conn),
):
    mapping = await conn.fetchrow(
        "SELECT candidate_id FROM client_candidate_mappings WHERE id = $1 AND client_id = $2",
        mapping_id, client_id,
    )
    if not mapping:
        raise HTTPException(404, "Mapping not found")

    await conn.execute(
        "DELETE FROM candidate_locks WHERE candidate_id = $1 AND client_id = $2",
        mapping["candidate_id"], client_id,
    )
    await conn.execute(
        "DELETE FROM client_candidate_mappings WHERE id = $1 AND client_id = $2",
        mapping_id, client_id,
    )
    return {"ok": True}
