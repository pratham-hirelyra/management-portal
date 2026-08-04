"""
Cron endpoints — called by Google Cloud Scheduler.

Cloud Scheduler must include the header:
    X-Cron-Secret: <value of CRON_SECRET env var>

Example schedule config:
    POST /cron/reachout?segment=active_job_post       (daily)
    POST /cron/reachout?segment=ca_network            (weekly)
    POST /cron/reachout?segment=employer_lead         (weekly)
    POST /cron/candidate-reachout                     (every 30 minutes)
    POST /cron/expire-offers                          (every 8 hours)
"""

import asyncio
import os
import re
import uuid
import random
import urllib.parse
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, HTTPException, Header, Query
from pydantic import BaseModel
import asyncpg
from database import get_conn, get_pool
import constants.whatsapp_templates as WT

_CLIENT_REACHOUT_TEMPLATE = WT.CLIENT_REACHOUT_3
_CANDIDATE_INTENT_TEMPLATE = WT.CANDIDATE_INTENT

router = APIRouter(prefix="/cron", tags=["cron"])


async def _send_client_reachout_step(
    client: asyncpg.Record,
    image_url: str,
    conn: asyncpg.Connection,
    phones: list[str],
) -> bool:
    """
    Send one reachout step to ALL provided phone numbers for a client.
    outreach_step increments once regardless of how many numbers are sent.
    Returns True if at least one send succeeded.
    """
    import services.msg91_service as msg91
    import re as _re
    from routers.whatsapp import _extract_msg_id

    client_id_str = str(client["id"])
    if not phones:
        return False

    next_step = int(client["outreach_step"] or 0) + 1

    # client_reachout_3 has 3 buttons: View Profile (URL, index 0) — now
    # re-approved as a DYNAMIC url (matches hl_cand_jd_img_v3's pattern, see
    # services/msg91_service.py:JD_V3_TEMPLATE), so the full destination URL
    # is sent as the button's {{1}} parameter, with client_id in ?ref= for
    # direct click-tracking (see _process_client_reachout_click's ref-param
    # path — phone-based fallback still applies if ref is ever missing). Not
    # Interested (quick_reply, index 1, same NOT_INTERESTED|<uuid> payload as
    # before); Stop (quick_reply, index 2, plain "STOP" payload — matches the
    # existing generic STOP opt-out handler).
    portal_url = os.environ.get("CLIENT_PORTAL_URL", "https://employer.justaccountants.in").rstrip("/")
    onboarding_url = f"{portal_url}/client-onboard?ref={client_id_str}"
    components = [
        {
            "type": "header",
            "parameters": [{"type": "image", "image": {"link": image_url}}],
        },
        {
            "type": "button",
            "sub_type": "url",
            "index": "0",
            "parameters": [{"type": "text", "text": onboarding_url}],
        },
        {
            "type": "button",
            "sub_type": "quick_reply",
            "index": "1",
            "parameters": [{"type": "payload", "payload": f"NOT_INTERESTED|{client_id_str}"}],
        },
        {
            "type": "button",
            "sub_type": "quick_reply",
            "index": "2",
            "parameters": [{"type": "payload", "payload": "STOP"}],
        },
    ]

    any_sent = False
    for phone in phones:
        digits = _re.sub(r'\D', '', phone)
        if len(digits) == 12 and digits.startswith('91'):
            digits = digits[2:]
        if len(digits) != 10 or digits[0] not in '6789':
            print(f"[cron/client-reachout] invalid mobile '{phone}' for {client_id_str} — skipping")
            continue
        try:
            result = await msg91.send_template(
                phone, _CLIENT_REACHOUT_TEMPLATE, "en", components, sender="client"
            )
            meta_msg_id = _extract_msg_id(result) if isinstance(result, dict) else None
            await conn.execute(
                """
                INSERT INTO whatsapp_messages
                    (client_id, message_type, direction, phone, status,
                     meta_msg_id, sent_at, used_template_name)
                VALUES ($1, 'client_reachout', 'outbound', $2, 'sent', $3, now(), $4)
                """,
                client["id"], phone, meta_msg_id, _CLIENT_REACHOUT_TEMPLATE,
            )
            any_sent = True
        except Exception as e:
            print(f"[cron/client-reachout] send failed for {phone}: {e}")
            try:
                from services.ops_alert_service import send_alert
                await send_alert("Client reachout MSG91 send failed", str(e), severity="ERROR", context={"phone": phone, "client_id": client_id_str})
            except Exception:
                pass
        await asyncio.sleep(1)

    if not any_sent:
        return False

    new_stage = "reachout_sent" if next_step == 1 else client["stage"]
    await conn.execute(
        """
        UPDATE clients
        SET outreach_step = $2, last_outreach_at = now(),
            stage = $3::client_stage, updated_at = now()
        WHERE id = $1
        """,
        client["id"], next_step, new_stage,
    )
    return True


@router.post("/client-reachout")
async def cron_client_reachout(
    segment: str = Query(..., description="ca_network | active_job_post | employer_lead"),
    batch: int = Query(20, ge=1, le=200, description="Max clients to contact per run"),
    x_cron_secret: str = Header(None, alias="X-Cron-Secret"),
    conn: asyncpg.Connection = Depends(get_conn),
):
    """
    Gradual reachout — called every 10 min per segment (staggered offsets, no overlap).
    Sends to at most `batch` clients per run (default 20).

    Cloud Scheduler config (timezone: Asia/Kolkata, 8 AM–9 PM IST):
      active_job_post → */10 8-20 * * *       (runs at :00, :10, :20, ...)
      ca_network      → 3-59/10 8-20 * * *    (runs at :03, :13, :23, ...)
      employer_lead   → 6-59/10 8-20 * * *    (runs at :06, :16, :26, ...)

    Cadence between steps per client:
      active_job_post → 24 h
      ca_network / employer_lead → 7 days
    """
    from datetime import timedelta
    from services import gcs_service

    expected = os.environ.get("CRON_SECRET", "")
    if expected and x_cron_secret != expected:
        raise HTTPException(401, "Invalid cron secret")

    valid = {"ca_network", "active_job_post", "employer_lead"}
    if segment not in valid:
        raise HTTPException(400, f"segment must be one of {sorted(valid)}")

    image_urls = await gcs_service.list_reachout_images()
    print(f"[cron/client-reachout] image_urls ({len(image_urls)}): {[u.split('/')[-1] for u in image_urls]}")
    if not image_urls:
        raise HTTPException(500, "No reachout images found in GCS bucket")

    max_steps = len(image_urls)
    is_daily = (segment == "active_job_post")
    cadence_delta = timedelta(hours=24) if is_daily else timedelta(days=7)
    cutoff_dt = datetime.now(timezone.utc) - cadence_delta

    clients = await conn.fetch(
        """
        SELECT *
        FROM clients
        WHERE segment     = $1
          AND is_dnd      = false
          AND COALESCE(is_bot, false) = false
          AND stage       IN ('scraped'::client_stage, 'reachout_sent'::client_stage)
          AND outreach_step < $2
          AND poc_phone   IS NOT NULL
          AND (last_outreach_at IS NULL OR last_outreach_at < $3)
        ORDER BY last_outreach_at ASC NULLS FIRST
        LIMIT $4
        """,
        segment, max_steps, cutoff_dt, batch,
    )

    sent = failed = skipped_dup = 0
    sent_client_ids: set[str] = set()
    for client in clients:
        cid_str = str(client["id"])
        if cid_str in sent_client_ids:
            continue

        # Build deduped phone list: all numbers from phone_numbers[], fallback to poc_phone
        import json as _json
        raw_numbers = client["phone_numbers"]
        if raw_numbers:
            all_phones = list(dict.fromkeys(
                p for p in (raw_numbers if isinstance(raw_numbers, list) else _json.loads(raw_numbers))
                if p
            ))
        else:
            all_phones = [client["poc_phone"]] if client["poc_phone"] else []

        if not all_phones:
            failed += 1
            continue

        step_idx = int(client["outreach_step"] or 0)
        image_url = image_urls[step_idx]
        ok = await _send_client_reachout_step(client, image_url, conn, all_phones)
        if ok:
            sent += 1
            sent_client_ids.add(cid_str)
        else:
            failed += 1
        await asyncio.sleep(2)

    await conn.execute(
        "UPDATE cron_schedules SET last_run_at = now() WHERE segment = $1",
        segment,
    )

    print(f"[cron/client-reachout] segment={segment} batch={batch} eligible={len(clients)} sent={sent} failed={failed} skipped_dup={skipped_dup}")
    return {
        "data": {
            "segment":     segment,
            "batch":       batch,
            "eligible":    len(clients),
            "sent":        sent,
            "failed":      failed,
            "skipped_dup": skipped_dup,
            "max_steps":   max_steps,
        },
        "ok": True,
    }


@router.get("/reachout-analytics")
async def reachout_analytics(
    conn: asyncpg.Connection = Depends(get_conn),
):
    """
    Snapshot of client reachout funnel.
    Returns stage breakdown, response rate, and last-24h send stats.
    """
    stage_rows = await conn.fetch(
        """
        SELECT stage::text, COUNT(*) AS count
        FROM clients
        WHERE source = ANY($1::text[])
          AND is_dnd = false
        GROUP BY stage
        ORDER BY count DESC
        """,
        list(_ENRICH_SOURCES),
    )
    by_stage = {r["stage"]: r["count"] for r in stage_rows}

    total_reached = await conn.fetchval(
        """
        SELECT COUNT(*) FROM clients
        WHERE outreach_step > 0
          AND is_dnd = false
          AND source = ANY($1::text[])
        """,
        list(_ENRICH_SOURCES),
    )

    # Responded = moved past reachout_sent (positive or negative)
    total_responded = await conn.fetchval(
        """
        SELECT COUNT(*) FROM clients
        WHERE stage NOT IN (
            'lead'::client_stage, 'scraping'::client_stage,
            'scraped'::client_stage, 'reachout_sent'::client_stage
        )
          AND is_dnd = false
          AND source = ANY($1::text[])
        """,
        list(_ENRICH_SOURCES),
    )

    response_rate = (
        round(total_responded / total_reached * 100, 1) if total_reached else 0.0
    )

    # Last 24h send stats from whatsapp_messages
    last_24h = await conn.fetchrow(
        """
        SELECT
            COUNT(*)                                                    AS total_sent,
            COUNT(*) FILTER (WHERE status = 'delivered')               AS delivered,
            COUNT(*) FILTER (WHERE status = 'read')                    AS read,
            COUNT(*) FILTER (WHERE status LIKE 'failed_%')             AS failed,
            COUNT(*) FILTER (WHERE status = 'failed_131049')           AS opted_out,
            COUNT(*) FILTER (WHERE status LIKE 'failed_%'
                               AND status != 'failed_131049')          AS bad_phone
        FROM whatsapp_messages
        WHERE message_type = 'client_reachout'
          AND direction    = 'outbound'
          AND sent_at      > now() - interval '24 hours'
        """
    )

    # All-time delivery breakdown
    all_time = await conn.fetchrow(
        """
        SELECT
            COUNT(*)                                                    AS total_sent,
            COUNT(*) FILTER (WHERE status = 'delivered')               AS delivered,
            COUNT(*) FILTER (WHERE status = 'read')                    AS read,
            COUNT(*) FILTER (WHERE status LIKE 'failed_%')             AS failed,
            COUNT(*) FILTER (WHERE status = 'failed_131049')           AS opted_out,
            COUNT(*) FILTER (WHERE status LIKE 'failed_%'
                               AND status != 'failed_131049')          AS bad_phone
        FROM whatsapp_messages
        WHERE message_type = 'client_reachout'
          AND direction    = 'outbound'
        """
    )

    return {
        "data": {
            "by_stage":          by_stage,
            "total_reached":     total_reached,
            "total_responded":   total_responded,
            "response_rate_pct": response_rate,
            "last_24h": {
                "sent":      last_24h["total_sent"],
                "delivered": last_24h["delivered"],
                "read":      last_24h["read"],
                "failed":    last_24h["failed"],
                "opted_out": last_24h["opted_out"],
                "bad_phone": last_24h["bad_phone"],
            },
            "all_time": {
                "sent":      all_time["total_sent"],
                "delivered": all_time["delivered"],
                "read":      all_time["read"],
                "failed":    all_time["failed"],
                "opted_out": all_time["opted_out"],
                "bad_phone": all_time["bad_phone"],
            },
        },
        "ok": True,
    }


# ── Candidate re-reachout ─────────────────────────────────────────────────────

_PORTAL_NAMES: dict[str, str] = {
    "workindia": "WorkIndia",
    "naukri": "Naukri",
    "apna": "Apna",
    "linkedin": "LinkedIn",
    "indeed": "Indeed",
    "shine": "Shine",
    "foundit": "Foundit",
    "monster": "Monster",
}


def _client_maps_url(client: dict) -> str:
    loc_text = client.get("job_location") or client.get("location") or ""
    lat, lng = client.get("location_lat"), client.get("location_lng")
    if lat and lng:
        return f"https://www.google.com/maps/search/?api=1&query={round(float(lat), 6)},{round(float(lng), 6)}"
    if loc_text:
        return f"https://www.google.com/maps/search/?api=1&query={urllib.parse.quote(loc_text)}"
    return ""


def _jd_intro(candidate: dict, client: dict) -> str:
    """Build the intro paragraph for the JD template ({{14}}).
    First-time candidates (form not yet submitted) get a portal-specific opener.
    Returning candidates get a plain intro."""
    if candidate.get("form_submitted"):
        return "I am sharing the job details for your review:"
    source = (candidate.get("source") or "").lower().strip()
    portal = _PORTAL_NAMES.get(source)
    job_title = client.get("job_title") or "Accountant"
    if portal:
        return (
            f"This is regarding your recent interest for the *{job_title}* position on *{portal}*. "
            "I am sharing the job details for your review:"
        )
    return "I am sharing the job details for your review:"


def _build_jd_components(client: dict) -> dict:
    """Build MSG91 bulk body components from a client row.

    Template param order (candidate_share_client_jd):
      1=company  2=industry  3=business_type  4=office_type  5=location_url
      6=employees  7=female_pct  8=skills  9=tools  10=salary  11=timings  12=other
    """
    _jt = (client.get("job_title") or "").lower()
    key_skills_str = (
        "Data entry (purchase/sales/bank), bank reco, petty cash, other admin work"
        if "junior" in _jt
        else "Accounting, GST, TDS, coordination with CA"
    )
    from services.mms_service import salary_ceiling

    # JD message shows the client's own minimum salary as-is (no matchmaking buffer applied).
    sal_min = float(client["min_salary"]) if client.get("min_salary") else None
    sal_max = salary_ceiling(client["max_salary"])
    if sal_min and sal_max:
        salary_str = f"₹{int(sal_min):,} – ₹{int(sal_max):,} in hand"
    elif sal_max:
        salary_str = f"up to ₹{int(sal_max):,} in hand"
    elif sal_min:
        salary_str = f"from ₹{int(sal_min):,} in hand"
    else:
        salary_str = ""

    diwali = str(client.get("diwali_bonus") or "").strip().lower()
    if diwali and diwali not in ("no", "false", "0", "n"):
        salary_str = (salary_str + " + 1 month salary Diwali Bonus").strip()

    female_pct = client.get("female_employee_pct")
    female_str = str(int(female_pct)) if female_pct is not None else ""

    loc_text = client.get("job_location") or client.get("location") or ""
    lat = client.get("location_lat")
    lng = client.get("location_lng")
    if lat and lng:
        # DB stores these as long-precision Decimals (e.g. 30 digits) — 6 decimal
        # places is ~11cm of accuracy, plenty for a map pin, and keeps the URL short
        maps_url = f"https://www.google.com/maps/search/?api=1&query={round(float(lat), 6)},{round(float(lng), 6)}"
    elif loc_text:
        maps_url = f"https://www.google.com/maps/search/?api=1&query={urllib.parse.quote(loc_text)}"
    else:
        maps_url = loc_text

    # Show the area name alongside the map link, not just a bare URL
    if loc_text and maps_url and maps_url != loc_text:
        location_value = f"{loc_text} — {maps_url}"
    else:
        location_value = maps_url or loc_text

    import re as _re
    # Strip newlines from other_details — WA template parameters cannot contain \n
    other_raw = client.get("other_details") or ""
    other = _re.sub(r'\s*\n+\s*', ' | ', other_raw.strip())

    job_type_raw = (client.get("job_type") or "").strip().lower()
    if "full" in job_type_raw:
        job_type_label = "FULL TIME"
    elif "part" in job_type_raw:
        job_type_label = "PART TIME"
    else:
        job_type_label = ""

    other_lines = []
    if job_type_label:
        other_lines.append(f"*Job Type:* *{job_type_label}*")
    other_lines.append(f"*Other Details:* {other}" if other else "*Other Details:* –")
    other_str = " | ".join(other_lines)

    _ot_map = {"corporate": "Corporate Office", "industrial": "Industrial Area"}
    raw_ot = (client.get("office_type") or "").strip()
    office_str = "CA Firm" if client.get("is_ca_firm") else _ot_map.get(raw_ot.lower(), raw_ot)

    return {
        "body_1":  {"type": "text", "value": client.get("company_name") or ""},
        "body_2":  {"type": "text", "value": client.get("industry") or ""},
        "body_3":  {"type": "text", "value": client.get("business_type") or ""},
        "body_4":  {"type": "text", "value": office_str},
        "body_5":  {"type": "text", "value": location_value},
        "body_6":  {"type": "text", "value": str(client.get("num_employees") or "")},
        "body_7":  {"type": "text", "value": female_str},
        "body_8":  {"type": "text", "value": key_skills_str},
        "body_9":  {"type": "text", "value": client.get("tools_requirements") or ""},
        "body_10": {"type": "text", "value": salary_str},
        "body_11": {"type": "text", "value": client.get("job_timings") or ""},
        "body_12": {"type": "text", "value": other_str},
    }


async def _do_candidate_reachout(conn: asyncpg.Connection, test_mode: bool = False) -> tuple[int, int]:
    if not test_mode and not _is_business_hours():
        return 0, 0

    import services.msg91_service as msg91

    divisor = 60 if test_mode else 3600
    thresholds = _REACHOUT_TEST_THRESHOLDS if test_mode else _REACHOUT_THRESHOLDS
    now = datetime.now(timezone.utc)
    sent = skipped = 0

    rows = await conn.fetch(
        """
        SELECT
            ccm.id          AS mapping_id,
            ccm.client_id,
            ccm.candidate_id,
            ccm.wa_sent_at,
            c.phone,
            c.name          AS candidate_name,
            (
                SELECT COUNT(*) FROM whatsapp_messages wm
                WHERE wm.mapping_id = ccm.id
                  AND wm.message_type = 'candidate_intent_ask'
                  AND wm.direction = 'outbound'
            ) AS send_count,
            COALESCE(
                (
                    SELECT MAX(wm.sent_at) FROM whatsapp_messages wm
                    WHERE wm.mapping_id = ccm.id
                      AND wm.message_type = 'candidate_intent_ask'
                      AND wm.direction = 'outbound'
                ),
                ccm.wa_sent_at
            ) AS last_sent_at
        FROM client_candidate_mappings ccm
        JOIN candidates c ON c.id = ccm.candidate_id
        WHERE ccm.stage = 'intent_ask'::mapping_stage
          AND ccm.wa_sent_at IS NOT NULL
          AND c.is_active = true
          AND (c.dnd_until IS NULL OR c.dnd_until < now())
        """
    )

    # Group by client_id to batch per-client DB fetches
    from collections import defaultdict
    by_client: dict = defaultdict(list)
    for row in rows:
        by_client[row["client_id"]].append(row)

    for client_id, mappings in by_client.items():
        client = await conn.fetchrow("SELECT * FROM clients WHERE id = $1", client_id)
        if not client:
            skipped += len(mappings)
            continue

        for row in mappings:
            send_count = int(row["send_count"])
            if send_count >= len(thresholds):
                skipped += 1
                continue

            # Measure elapsed from the last sent message, not from the initial wa_sent_at.
            # This ensures each follow-up fires N hours after the previous one regardless
            # of business hours delays.
            elapsed_hours = (now - row["last_sent_at"]).total_seconds() / divisor
            next_idx = send_count  # index into thresholds for the next due message

            if elapsed_hours < thresholds[next_idx]:
                skipped += 1
                continue

            phone = msg91._format_phone(row["phone"] or "")
            if not phone:
                skipped += 1
                continue

            mapping_id = row["mapping_id"]
            attempt = next_idx + 1  # 1-based attempt number

            re_template = _REACHOUT_TEMPLATES[next_idx]
            cand_name = (row.get("candidate_name") or "").split()[0] if row.get("candidate_name") else ""

            _RE_INTROS = [
                "You haven't responded to our earlier message. Here are the job details again:",
                "We're still shortlisting candidates. Here are the job details one more time:",
                "This is our final follow-up. Interview slots are filling up fast:",
            ]
            jd_components = _build_jd_components(dict(client))
            candidate_entry = {
                "phone": phone,
                "mapping_id": str(mapping_id),
                "name": cand_name,
                "intro": _RE_INTROS[next_idx],
                "maps_url": _client_maps_url(dict(client)),
            }

            try:
                from services.jd_card_service import generate_jd_card
                jd_image_url = client.get("jd_card_url") if isinstance(client, dict) else client["jd_card_url"]
                if not jd_image_url:
                    print(f"[cron] jd_card_url missing for re-reachout {mapping_id} — generating now")
                    try:
                        client_dict = dict(client) if not isinstance(client, dict) else client
                        jd_image_url = await generate_jd_card(client_dict)
                        await conn.execute(
                            "UPDATE clients SET jd_card_url = $1 WHERE id = $2",
                            jd_image_url, client_dict["id"],
                        )
                    except Exception as _img_err:
                        print(f"[cron] JD card generation failed for re-reachout {mapping_id}: {_img_err}")
                        jd_image_url = None
                if not jd_image_url:
                    print(f"[cron] Skipping re-reachout {mapping_id} — JD card image unavailable")
                    skipped += 1
                    continue
                await msg91.send_bulk_intent(
                    [candidate_entry],
                    jd_components,
                    template_name=re_template,
                    image_url=jd_image_url,
                )
            except Exception as e:
                print(f"[cron] candidate re-reachout attempt {attempt} failed for {phone} mapping {mapping_id}: {e}")
                try:
                    from services.ops_alert_service import send_alert
                    await send_alert("Candidate re-reachout send failed", str(e), severity="ERROR", context={"phone": phone, "mapping_id": str(mapping_id), "attempt": attempt})
                except Exception:
                    pass
                skipped += 1
                continue

            await conn.execute(
                """
                INSERT INTO whatsapp_messages
                    (candidate_id, mapping_id, message_type, direction, phone, status, sent_at, used_template_name)
                VALUES ($1, $2, 'candidate_intent_ask', 'outbound', $3, 'sent', now(), $4)
                """,
                row["candidate_id"], mapping_id, phone, re_template,
            )
            print(f"[cron] re-reachout attempt {attempt} sent to {phone} mapping {mapping_id} ({elapsed_hours:.1f}h since last send)")
            sent += 1

    return sent, skipped



@router.post("/re-engage")
async def cron_re_engage(
    x_cron_secret: str = Header(None, alias="X-Cron-Secret"),
    conn: asyncpg.Connection = Depends(get_conn),
):
    """
    Reactivates candidates whose DND period has expired.
    Run daily. Clears is_active, dnd_until so they re-enter matchmaking.
    """
    expected = os.environ.get("CRON_SECRET", "")
    if expected and x_cron_secret != expected:
        raise HTTPException(401, "Invalid cron secret")

    result = await conn.execute(
        """
        UPDATE candidates
        SET is_active = true, dnd_until = NULL, updated_at = now()
        WHERE dnd_until IS NOT NULL AND dnd_until <= now() AND is_active = false
        """
    )
    count = int(result.split()[-1]) if result else 0

    print(f"[cron] re-engage: {count} candidates reactivated")
    return {"data": {"reactivated": count}, "ok": True}


@router.post("/candidate-reachout")
async def cron_candidate_reachout(
    x_cron_secret: str = Header(None, alias="X-Cron-Secret"),
    test: bool = Query(False, description="Use minutes instead of hours for thresholds (testing only)"),
    conn: asyncpg.Connection = Depends(get_conn),
):
    """
    Re-sends JD to candidates who haven't responded at intent_ask stage.
    Schedule: run every 30 minutes.
    Sends at t+2h, t+3h, t+7h, t+12h, t+24h from initial send; max 5 follow-ups.
    Use ?test=true to fire on 2-minute intervals instead.
    """
    expected = os.environ.get("CRON_SECRET", "")
    if expected and x_cron_secret != expected:
        raise HTTPException(401, "Invalid cron secret")

    sent, skipped = await _do_candidate_reachout(conn, test_mode=test)
    return {"data": {"sent": sent, "skipped": skipped}, "ok": True}


# ── Reachout queue (admin view + manual trigger) ──────────────────────────────

@router.get("/reachout-queue")
async def get_reachout_queue(
    conn: asyncpg.Connection = Depends(get_conn),
):
    """Returns all clients currently in the 12-step outreach sequence."""
    from datetime import timedelta

    rows = await conn.fetch(
        """
        SELECT id, company_name, segment, stage, outreach_step,
               last_outreach_at, poc_phone
        FROM clients
        WHERE stage IN ('scraped'::client_stage, 'reachout_sent'::client_stage)
          AND outreach_step < 12
          AND is_dnd = false
          AND poc_phone IS NOT NULL
        ORDER BY last_outreach_at ASC NULLS FIRST
        """
    )

    now = datetime.now(timezone.utc)
    result = []
    for row in rows:
        d = dict(row)
        seg = d.get("segment") or ""
        last_at = d.get("last_outreach_at")

        if last_at is None:
            next_due_at = None
            is_overdue = True
        else:
            cadence = timedelta(hours=24) if seg == "active_job_post" else timedelta(days=7)
            next_due = last_at + cadence
            next_due_at = next_due.isoformat()
            is_overdue = now >= next_due

        d["id"] = str(d["id"])
        d["last_outreach_at"] = last_at.isoformat() if last_at else None
        d["next_due_at"] = next_due_at
        d["is_overdue"] = is_overdue
        result.append(d)

    return {"data": result, "count": len(result), "ok": True}


# ── Apify job scraping ────────────────────────────────────────────────────────

_APIFY_ACTORS = {
    "naukri":    "alpcnRV9YI9lYVPWk",
    "workindia": "23KtodpG4T4RFCLe4",
    "indeed":    "dJWj757pVx5untEuq",
    "apna":      "VCD9VVgpeCv0kjhUb",
}

_ACTOR_INPUTS = {
    "naukri": {
        "cities":       ["51"],
        "fetchDetails": False,
        "freshness":    "30",
        "keyword":      "Accountant",
        "maxJobs":      100,
        "postedBy":     ["1"],
        "roleCategory": ["1029", "1034"],
        "sortBy":       "date",
        "workMode":     ["office"],
    },
    "workindia": {
        "city":               "Ahmedabad",
        "includeDetails":     True,
        "keyword":            "Accountant",
        "max_pages":          100,
        "results_wanted":     100,
        "proxyConfiguration": {"useApifyProxy": False},
    },
    "indeed": {
        "country":      "India",
        "currency":     "INR",
        "distance":     50,
        "job_type":     "fulltime",
        "keyword":      "Accountant",
        "location":     "Ahmedabad",
        "max_results":  100,
        "posted_since": "30 days",
        "remote_only":  False,
    },
    "apna": {
        "keyword":            "accountant",
        "location":           "Ahmedabad",
        "max_pages":          10,
        "results_wanted":     100,
        "proxyConfiguration": {"useApifyProxy": False},
    },
}

# Date field name per portal (used for 15-day post-filter)
_DATE_FIELDS = {
    "naukri":    "postedDate",    # "2026-05-22T07:16:12.301Z"
    "workindia": "created_at",    # "2026-05-22T06:12:13Z"
    "indeed":    "posted_date",   # "2026-05-22T00:00:00.000"
    "apna":      "created_on",    # ISO string
}

# Job title keywords to keep when portal doesn't support keyword filtering
_ACCOUNTANT_KEYWORDS = frozenset([
    "accountant", "accounts", "accounting", "finance", "financial",
    "bookkeeper", "bookkeeping", "tally", "gst", "auditor", "audit",
    "ca ", "chartered", "taxation", "tax",
])


def _parse_salary(val) -> int | None:
    """Parse a salary value that may be a number or a string like '20000-30000'. Returns int."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return int(round(float(val)))
    s = str(val).replace(",", "").replace("Rs.", "").replace("₹", "").strip()
    if "-" in s:
        parts = s.split("-")
        try:
            return int(round(float(parts[0].strip())))
        except ValueError:
            return None
    try:
        return int(round(float(s)))
    except ValueError:
        return None


def _parse_salary_from_description(text: str | None) -> tuple[int | None, int | None]:
    """Extract min/max monthly salary from free-text job description (Indeed style)."""
    if not text:
        return None, None
    m = re.search(r'[₹Rs\.]+\s*([\d,]+)\s*[-–]\s*[₹Rs\.]*\s*([\d,]+)', text)
    if m:
        lo = int(m.group(1).replace(",", ""))
        hi = int(m.group(2).replace(",", ""))
        if 3000 <= lo <= 1_000_000 and 3000 <= hi <= 1_000_000:
            return lo, hi
    return None, None


def _extract_city_from_description(text: str | None) -> str | None:
    """Pull a city name from 'Location: <City>' patterns in job description text."""
    if not text:
        return None
    m = re.search(r'(?:Location|location)\s*[:\|]\s*([A-Za-z][A-Za-z\s]{2,30}?)(?:\n|,|\.|$)', text)
    if m:
        return m.group(1).strip().split(",")[0].strip()
    return None


def _map_naukri(item: dict) -> dict | None:
    job_id = item.get("jobId") or item.get("job_id")
    if not job_id:
        return None

    # skills is already a list in this actor
    skills_raw = item.get("skills") or []
    if isinstance(skills_raw, list):
        key_skills = [s.strip() for s in skills_raw if s]
    else:
        key_skills = [s.strip() for s in str(skills_raw).split(",") if s.strip()]

    min_sal = _parse_salary(item.get("salaryMin"))
    max_sal = _parse_salary(item.get("salaryMax"))
    # Naukri salaries are annual CTC — convert to monthly
    if min_sal and min_sal > 50000:
        min_sal = round(min_sal / 12)
    if max_sal and max_sal > 50000:
        max_sal = round(max_sal / 12)

    return {
        "source_job_id": f"naukri_{job_id}",
        "company_name":  item.get("companyName") or item.get("company_name"),
        "job_title":     item.get("title") or item.get("jobTitle"),
        "job_location":  item.get("location") or None,
        "key_skills":    key_skills,
        "min_salary":    min_sal,
        "max_salary":    max_sal,
        "poc_phone":     None,
        "poc_name":      None,
        "source":        "naukri",
        "segment":       "active_job_post",
    }


_WORKINDIA_PLATFORM_PHONES = {"6146092114"}  # WorkIndia's own HR line, not real employer contact


def _map_workindia(item: dict) -> dict | None:
    # WorkIndia confirmed output schema (actor ID 23KtodpG4T4RFCLe4)
    job_id = item.get("job_id") or item.get("id")
    if not job_id:
        return None

    contact = item.get("contact_detail") or {}
    poc_phone = contact.get("contact") if contact.get("contact_type") == "phone_no" else None
    if poc_phone in _WORKINDIA_PLATFORM_PHONES:
        poc_phone = None

    return {
        "source_job_id": f"workindia_{job_id}",
        "company_name":  item.get("branch_company_name"),
        "job_title":     item.get("profile_job_title"),
        "job_location":  item.get("branch_address") or None,
        "key_skills":    [],
        "min_salary":    _parse_salary(item.get("min_salary")),
        "max_salary":    _parse_salary(item.get("max_salary")),
        "poc_phone":     poc_phone,
        "poc_name":      item.get("branch_contact_person_name"),
        "source":        "workindia",
        "segment":       "active_job_post",
    }


def _map_indeed(item: dict) -> dict | None:
    # Indeed confirmed output schema (actor ID dJWj757pVx5untEuq / truefetch)
    # Use platform_url's jk param as unique ID
    platform_url = item.get("platform_url") or item.get("official_url") or ""
    job_id = None
    if "jk=" in platform_url:
        job_id = platform_url.split("jk=")[-1].split("&")[0]
    if not job_id:
        job_id = item.get("id") or item.get("job_id") or platform_url
    if not job_id:
        return None

    location = item.get("location") or ""

    # Salary: structured fields are always null — parse from description text
    min_sal = _parse_salary(item.get("salary_minimum"))
    max_sal = _parse_salary(item.get("salary_maximum"))
    if min_sal is None and max_sal is None:
        min_sal, max_sal = _parse_salary_from_description(item.get("description"))

    # Skills: structured field is empty string — not available from this actor
    phones = item.get("phones") or []
    poc_phone = phones[0] if phones else None

    return {
        "source_job_id": f"indeed_{job_id}",
        "company_name":  item.get("company_name"),
        "job_title":     item.get("title"),
        "job_location":  location or None,
        "key_skills":    [],
        "min_salary":    min_sal,
        "max_salary":    max_sal,
        "poc_phone":     poc_phone,
        "poc_name":      None,
        "source":        "indeed",
        "segment":       "active_job_post",
    }


_APNA_NON_SKILL_PATTERNS = re.compile(
    r'^(work from (office|home)|remote|hybrid|full[- ]?time|part[- ]?time|internship|'
    r'contract|freelance|any experience|no experience|min\.?\s*\d+\s*years?|'
    r'.*(basic|good|fluent|intermediate|advanced).*english.*|.*english.*)$',
    re.IGNORECASE,
)


def _map_apna(item: dict) -> dict | None:
    # Apna confirmed output schema (actor ID VCD9VVgpeCv0kjhUb / shahidirfan)
    job_id = item.get("job_id")
    if not job_id:
        return None

    ui_tags = item.get("ui_tags") or []
    key_skills = [
        str(t).strip() for t in (ui_tags if isinstance(ui_tags, list) else [])
        if t and not _APNA_NON_SKILL_PATTERNS.match(str(t).strip())
    ]

    return {
        "source_job_id": f"apna_{job_id}",
        "company_name":  item.get("company"),
        "job_title":     item.get("title"),
        "job_location":  item.get("job_address_line_1") or item.get("city") or item.get("location_name"),
        "key_skills":    key_skills,
        "min_salary":    _parse_salary(item.get("min_salary")),
        "max_salary":    _parse_salary(item.get("max_salary")),
        "poc_phone":     None,
        "poc_name":      None,
        "source":        "apna",
        "segment":       "active_job_post",
    }


_MAPPERS = {
    "naukri":    _map_naukri,
    "workindia": _map_workindia,
    "indeed":    _map_indeed,
    "apna":      _map_apna,
}


def _is_accountant_role(title: str | None) -> bool:
    if not title:
        return False
    t = title.lower()
    return any(kw in t for kw in _ACCOUNTANT_KEYWORDS)


def _is_within_15_days(item: dict, date_field: str) -> bool:
    """Return True if the item's date field is within the last 15 days. Missing date → include."""
    raw = item.get(date_field)
    if not raw:
        return True
    from datetime import datetime, timezone, timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(days=15)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ",
                "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.strptime(str(raw)[:26], fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt >= cutoff
        except ValueError:
            continue
    return True  # unparseable → include


async def _scrape_portal(portal: str, input_overrides: dict | None = None) -> list[dict]:
    """Run one Apify actor and return mapped records. Never raises — logs and returns []."""
    from services import apify_service
    # All portals now send keyword — title filter is a safety net for off-topic results
    needs_title_filter = True
    date_field = _DATE_FIELDS.get(portal, "")
    actor_input = {**_ACTOR_INPUTS[portal], **(input_overrides or {})}
    try:
        items = await apify_service.run_actor(
            _APIFY_ACTORS[portal],
            actor_input,
            timeout=360,
        )
        mapper = _MAPPERS[portal]
        results = []
        for item in (items or []):
            # Date filter — skip posts older than 15 days
            if date_field and not _is_within_15_days(item, date_field):
                continue
            mapped = mapper(item)
            if not mapped or not mapped.get("source_job_id") or not mapped.get("company_name"):
                continue
            if needs_title_filter and not _is_accountant_role(mapped.get("job_title")):
                continue
            results.append(mapped)
        freshness_note = actor_input.get("freshness") or actor_input.get("posted_since") or "no date filter"
        print(f"[cron/scrape-jobs] {portal} (window={freshness_note}): {len(items or [])} raw → {len(results)} matched")
        return results
    except Exception as e:
        print(f"[cron/scrape-jobs] {portal} failed: {e}")
        try:
            from services.ops_alert_service import alert_cron_failure
            await alert_cron_failure(f"scrape-jobs/{portal}", str(e))
        except Exception:
            pass
        return []


async def _find_duplicate_client(conn: asyncpg.Connection, phones: list[str], exclude_id=None) -> dict | None:
    """
    Return an existing client whose poc_phone/phone_numbers already contains one of `phones`
    (excluding `exclude_id`), or None. Used to stop the same contact being entered into the
    pipeline twice (e.g. a new job post for a company we already reached / onboarded).
    """
    phones = [p for p in (phones or []) if p]
    if not phones:
        return None
    row = await conn.fetchrow(
        """
        SELECT id, company_name, stage
        FROM clients
        WHERE id != COALESCE($2, '00000000-0000-0000-0000-000000000000'::uuid)
          AND (
              poc_phone = ANY($1::text[])
              OR EXISTS (
                  SELECT 1 FROM unnest($1::text[]) p
                  WHERE phone_numbers::text LIKE '%' || p || '%'
              )
          )
        LIMIT 1
        """,
        phones, exclude_id,
    )
    return dict(row) if row else None


async def _find_existing_client_by_name(conn: asyncpg.Connection, company_name: str | None) -> dict | None:
    """
    Return an existing active client with the same (normalized) company_name, or None.
    Used to stop a fresh job-portal posting from a company we're already tracking (no phone yet)
    from piling up as yet another duplicate lead row. Disqualified/churned clients are excluded
    so a new posting can re-open the pipeline for a company we'd previously dropped.
    """
    if not company_name:
        return None
    row = await conn.fetchrow(
        """
        SELECT id, company_name, stage
        FROM clients
        WHERE LOWER(TRIM(company_name)) = LOWER(TRIM($1))
          AND stage NOT IN ('disqualified'::client_stage, 'churned'::client_stage)
        LIMIT 1
        """,
        company_name,
    )
    return dict(row) if row else None


async def _insert_new_jobs(records: list[dict], conn: asyncpg.Connection) -> list[dict]:
    """
    Insert jobs that don't yet exist (dedup by source_job_id).
    Skips jobs whose poc_phone already belongs to another client (same contact,
    different/new job post) so we never create a second pipeline entry for them.
    Returns list of inserted client dicts (id, company_name, job_location, job_title).
    """
    if not records:
        return []

    inserted: list[dict] = []
    for rec in records:
        sjid = rec["source_job_id"]
        existing = await conn.fetchval(
            "SELECT id FROM clients WHERE source_job_id = $1 LIMIT 1", sjid
        )
        if existing:
            continue

        has_phone = bool(rec.get("poc_phone"))
        if has_phone:
            dup = await _find_duplicate_client(conn, [rec["poc_phone"]])
            if dup:
                print(f"[cron/scrape-jobs] skipping {rec.get('company_name')!r} (source_job_id={sjid}): "
                      f"phone {rec['poc_phone']} already belongs to client {dup['id']} "
                      f"({dup['company_name']!r}, stage={dup['stage']})")
                continue
        else:
            # No phone yet — if we're already tracking this company from an earlier
            # posting, don't pile up another duplicate "lead" row for it.
            dup = await _find_existing_client_by_name(conn, rec.get("company_name"))
            if dup:
                print(f"[cron/scrape-jobs] skipping {rec.get('company_name')!r} (source_job_id={sjid}): "
                      f"company already tracked as client {dup['id']} (stage={dup['stage']})")
                continue

        row = await conn.fetchrow(
            """
            INSERT INTO clients (
                company_name, job_title, job_location,
                key_skills, min_salary, max_salary,
                poc_phone, poc_name,
                source, source_job_id, segment,
                stage, enrich_status, city, state, created_at, updated_at
            ) VALUES (
                $1, $2, $3,
                $4, $5, $6,
                $7, $8,
                $9, $10, $11,
                $12::client_stage, $13, $14, $15, now(), now()
            ) RETURNING id, company_name, job_title, job_location
            """,
            rec.get("company_name"),
            rec.get("job_title"),
            rec.get("job_location"),
            rec.get("key_skills") or [],
            rec.get("min_salary"),
            rec.get("max_salary"),
            rec.get("poc_phone"),
            rec.get("poc_name"),
            rec.get("source"),
            sjid,
            rec.get("segment", "active_job_post"),
            "scraped" if has_phone else "lead",
            "enriched" if has_phone else "pending",
            rec.get("city"),
            rec.get("state"),
        )
        if row:
            inserted.append(dict(row))

    return inserted


async def _classify_new_clients(new_clients: list[dict]) -> tuple[int, int]:
    """
    Fast name-based filter: immediately marks obvious recruiters (no API call).
    Non-obvious clients are left with is_recruiter=NULL and enrich_status='pending'
    so the enrich cron classifies them (combined with phone-finding) in one API call.
    Returns (name_filtered_recruiters, deferred_count).
    """
    if not new_clients:
        return 0, 0

    from services import enrich_service

    name_recruiters = deferred = 0
    pool = get_pool()
    async with pool.acquire() as wconn:
        for c in new_clients:
            cid  = uuid.UUID(str(c["id"]))
            name = c.get("company_name") or ""
            if enrich_service.is_obvious_recruiter(name):
                await wconn.execute(
                    """
                    UPDATE clients SET
                        company_description = 'Recruitment consultancy — excluded from pipeline.',
                        is_recruiter        = true,
                        is_dnd              = true,
                        enrich_status       = 'not_found',
                        updated_at          = now()
                    WHERE id = $1
                    """,
                    cid,
                )
                name_recruiters += 1
            else:
                deferred += 1

    print(f"[cron/scrape-jobs] name-filter: obvious_recruiters={name_recruiters} deferred_to_enrich={deferred}")
    return name_recruiters, deferred


@router.post("/scrape-jobs")
async def cron_scrape_jobs(
    force_days: int | None = Query(None, ge=1, description="Force all portals to fetch jobs posted in the last N days, ignoring the dynamic window."),
    x_cron_secret: str = Header(None, alias="X-Cron-Secret"),
    conn: asyncpg.Connection = Depends(get_conn),
):
    """
    Daily cron: runs all 4 Apify job portal actors, maps output to client leads,
    inserts only records whose source_job_id is not already in the DB,
    then immediately classifies each new client as recruiter vs real employer.
    """
    expected = os.environ.get("CRON_SECRET", "")
    if expected and x_cron_secret != expected:
        raise HTTPException(401, "Invalid cron secret")

    # Per-portal input overrides: set date window to days since last scrape + 1 buffer day
    # so we only fetch genuinely new posts instead of re-scraping the same jobs.
    # force_days overrides this for one-off backfill runs.
    from datetime import datetime, timezone, timedelta
    from services import city_service
    portals = list(_APIFY_ACTORS.keys())
    date_overrides: dict[str, dict] = {}
    for portal in portals:
        if force_days:
            days = force_days
        else:
            last_scraped = await conn.fetchval(
                "SELECT MAX(created_at) FROM clients WHERE source = $1", portal
            )
            days = max(1, (datetime.now(timezone.utc) - last_scraped).days + 1) if last_scraped else 30
        if portal == "naukri":
            date_overrides[portal] = {"freshness": str(days)}
        elif portal == "indeed":
            date_overrides[portal] = {"posted_since": f"{days} days"}
        # workindia and apna have no date-filter param — no override possible

    # Loop portals x active_cities (from the active_cities table — activating a
    # new city later is a data change only, this loop picks it up automatically).
    cities = await city_service.active_cities(conn)
    tasks = []
    call_meta: list[tuple[str, dict]] = []
    for portal in portals:
        for city_row in cities:
            city_override = dict(date_overrides.get(portal, {}))
            if portal == "naukri":
                if not city_row["naukri_code"]:
                    print(f"[cron/scrape-jobs] skipping naukri for {city_row['city']!r} — no naukri_code set in active_cities")
                    continue
                city_override["cities"] = [city_row["naukri_code"]]
            elif portal == "workindia":
                city_override["city"] = city_row["city"]
            else:  # indeed, apna
                city_override["location"] = city_row["city"]
            tasks.append(_scrape_portal(portal, city_override))
            call_meta.append((portal, city_row))

    results = await asyncio.gather(*tasks)

    all_records: list[dict] = []
    portal_raw_counts: dict[str, int] = {}
    for (portal, city_row), recs in zip(call_meta, results):
        portal_raw_counts[portal] = portal_raw_counts.get(portal, 0) + len(recs)
        for rec in recs:
            rec["city"] = city_row["city"]
            rec["state"] = city_row["state"]
        all_records.extend(recs)

    new_clients = await _insert_new_jobs(all_records, conn)
    inserted = len(new_clients)
    print(f"[cron/scrape-jobs] total mapped={len(all_records)}, inserted={inserted}")

    # Name-filter pass: instantly mark obvious recruiters, defer the rest to enrich cron
    name_recruiters, deferred = await _classify_new_clients(new_clients)

    return {
        "data": {
            "portals":          portal_raw_counts,
            "total_mapped":     len(all_records),
            "inserted":         inserted,
            "name_recruiters":  name_recruiters,
            "deferred":         deferred,
        },
        "ok": True,
    }


# ── Client follow-up reminders ────────────────────────────────────────────────

_IST = timezone(timedelta(hours=5, minutes=30))

_ONBOARDING_THRESHOLDS  = [3, 8, 12]        # hours from last reminder (or onboarding_form_sent_at)
_AGREEMENT_THRESHOLDS   = [3, 8, 12]        # hours from last reminder (or agreement_sent_at, biz hrs only)
_SLOT_THRESHOLDS        = [3, 8, 12]        # hours from last reminder (or slot_requested_at)
_REACHOUT_THRESHOLDS    = [3, 8, 24] # hours from last sent message (candidate JD re-reachout)
_REACHOUT_TEMPLATES     = [WT.CANDIDATE_RE_REACHOUT_R1_IMG, WT.CANDIDATE_RE_REACHOUT_R2_IMG, WT.CANDIDATE_RE_REACHOUT_R3B_IMG]

_TEST_THRESHOLDS        = [2, 2, 2]         # minutes (used when test_mode=True, 3-step flows)
_REACHOUT_TEST_THRESHOLDS = [2, 2, 2]       # minutes (used when test_mode=True, 3-step reachout)

# Dropped-call retry: first attempt at 7pm IST; retries at +3h, +8h, +24h from last attempt
_DROP_RETRY_THRESHOLDS      = [3, 8, 24]    # hours after drop → retry 1, retry 1 → retry 2, retry 2 → retry 3
_DROP_RETRY_TEST_THRESHOLDS = [2, 4, 6]     # minutes (test mode)
_DROP_RETRY_MAX             = 3             # total retry attempts

# AI voice follow-up for clients stuck in 'reachout_sent' (WA reachout sent today, no progress)
_REACHOUT_AI_CALL_HOURS         = 3    # hours since today's client_reachout WA message was sent
_REACHOUT_AI_CALL_TEST_MINUTES  = 2    # minutes (test mode)
_REACHOUT_AI_CALL_BATCH         = 5    # max AI calls per cron run

# AI voice follow-up for clients stuck in 'interested'/'agreement_sent' (WA reminder sent today, no progress)
_FUNNEL_STUCK_AI_CALL_HOURS         = 3    # hours since today's client_followup WA reminder was sent
_FUNNEL_STUCK_AI_CALL_TEST_MINUTES  = 2    # minutes (test mode)
_FUNNEL_STUCK_AI_CALL_BATCH         = 5    # max AI calls per cron run

_ONBOARDING_TEMPLATES = [WT.CLIENT_ONBOARDING_R1, WT.CLIENT_ONBOARDING_R2, WT.CLIENT_ONBOARDING_R3]
_AGREEMENT_TEMPLATES  = [WT.CLIENT_AGREEMENT_REMINDER_R1, WT.CLIENT_AGREEMENT_REMINDER_R2, WT.CLIENT_AGREEMENT_REMINDER_R3]
_SLOT_TEMPLATES       = [WT.CLIENT_SLOT_REMINDER_R1, WT.CLIENT_SLOT_REMINDER_R2, WT.CLIENT_SLOT_REMINDER_R3]


async def _retry_dropped_calls(conn: asyncpg.Connection, test_mode: bool = False) -> dict:
    """
    Re-trigger RinggAI calls for candidates who dropped the call midway.

    Attempt 0 (first): fires only during 7pm IST hour (19:xx IST).
    Attempts 1–3    : fires when elapsed hours from last attempt >= thresholds [3, 8, 24].
    Stops after _DROP_RETRY_MAX total attempts or when call_status changes away from DROPPED.
    """
    import services.ringg_service as ringg

    divisor    = 60 if test_mode else 3600
    thresholds = _DROP_RETRY_TEST_THRESHOLDS if test_mode else _DROP_RETRY_THRESHOLDS
    now        = datetime.now(timezone.utc)

    rows = await conn.fetch(
        """
        SELECT id, name, phone,
               COALESCE(call_drop_retry_count, 0) AS retry_count,
               last_call_drop_retry_at,
               COALESCE(drop_final_sent, false) AS drop_final_sent,
               updated_at
        FROM candidates
        WHERE call_status IN ('DROPPED', 'NOT_PICKED')
          AND is_active   = true
          AND LOWER(COALESCE(evaluation_status, '')) NOT IN ('pass', 'fail', 'passed', 'failed')
        """,
    )

    if not test_mode and not _is_business_hours():
        return {"sent": 0, "skipped": 0}

    sent = skipped = 0
    for r in rows:
        retry_count     = int(r["retry_count"])
        last_at         = r["last_call_drop_retry_at"]
        drop_final_sent = r["drop_final_sent"]
        phone           = r["phone"] or ""
        name            = r["name"]  or ""

        # All retries exhausted — send the final "couldn't connect" WA message once
        if retry_count >= _DROP_RETRY_MAX:
            if drop_final_sent:
                skipped += 1
                continue
            if not last_at:
                skipped += 1
                continue
            elapsed = (now - last_at).total_seconds() / divisor
            if elapsed < thresholds[-1]:
                skipped += 1
                continue
            # Find the most recent interested mapping to embed in button payloads
            mapping_row = await conn.fetchrow(
                """
                SELECT id FROM client_candidate_mappings
                WHERE candidate_id = $1 AND stage = 'interested'::mapping_stage
                ORDER BY updated_at DESC LIMIT 1
                """,
                r["id"],
            )
            if not mapping_row:
                skipped += 1
                continue
            first_name = name.split()[0] if name else "there"
            try:
                import services.msg91_service as _msg91
                await _msg91.send_bulk_with_buttons(
                    [{"phone": _msg91._format_phone(phone), "mapping_id": str(mapping_row["id"])}],
                    WT.CANDIDATE_DROP_FINAL,
                    [first_name],
                )
                await conn.execute(
                    "UPDATE candidates SET drop_final_sent = true, updated_at = now() WHERE id = $1",
                    r["id"],
                )
                # Unreachable after every retry — release the lock and decline the
                # mapping so this candidate re-enters the pool for other clients.
                # Previously this never happened: the candidate stayed locked to
                # this one client forever with evaluation_status still null, so a
                # future Interested tap on a different opportunity couldn't
                # actually get them matched (or re-call them) again.
                await conn.execute(
                    """
                    UPDATE client_candidate_mappings
                    SET stage = 'not_interested'::mapping_stage, decline_reason = 'call_unreachable', updated_at = now()
                    WHERE id = $1
                    """,
                    mapping_row["id"],
                )
                await conn.execute(
                    """
                    UPDATE candidate_locks
                    SET unlocked_at = now(), unlock_reason = 'call_unreachable'
                    WHERE candidate_id = $1 AND unlocked_at IS NULL
                    """,
                    r["id"],
                )
                print(f"[cron/retry-dropped] final drop message sent to {phone}, candidate unlocked")
                sent += 1
            except Exception as e:
                print(f"[cron/retry-dropped] final drop message failed for {phone}: {e}")
                try:
                    from services.ops_alert_service import send_alert
                    await send_alert("Retry dropped call final message failed", str(e), severity="ERROR", context={"phone": phone, "cand_id": str(r["id"])})
                except Exception:
                    pass
                skipped += 1
            continue

        # All retries use elapsed-time gating: retry 0 measures from updated_at (drop time),
        # subsequent retries measure from last_call_drop_retry_at.
        if retry_count == 0:
            ref_time = r["updated_at"]
        else:
            ref_time = last_at
        threshold = thresholds[retry_count]

        if ref_time is None:
            skipped += 1
            continue
        elapsed = (now - ref_time).total_seconds() / divisor
        if elapsed < threshold:
            skipped += 1
            continue

        try:
            await ringg.trigger_ai_call(r["id"], phone, name, retry_on_no_pickup=False)
            await conn.execute(
                """
                UPDATE candidates SET
                    call_drop_retry_count    = COALESCE(call_drop_retry_count, 0) + 1,
                    last_call_drop_retry_at  = now(),
                    updated_at               = now()
                WHERE id = $1
                """,
                r["id"],
            )
            print(f"[cron/retry-dropped] attempt {retry_count + 1}/{_DROP_RETRY_MAX} triggered for {phone}")
            sent += 1
        except Exception as e:
            print(f"[cron/retry-dropped] trigger failed for {phone}: {e}")
            try:
                from services.ops_alert_service import send_alert
                await send_alert("Retry dropped call RinggAI trigger failed", str(e), severity="ERROR", context={"phone": phone, "cand_id": str(r["id"]), "retry_count": retry_count})
            except Exception:
                pass
            skipped += 1

    return {"sent": sent, "skipped": skipped}


def _is_business_hours() -> bool:
    """9am–9pm IST."""
    now_ist = datetime.now(_IST)
    return 9 <= now_ist.hour < 21


async def _followup_reachout_ai_call(conn: asyncpg.Connection, test_mode: bool = False) -> dict:
    """
    Place an AI voice call (existing client-reachout RinggAI agent) to clients
    who are still stuck in 'reachout_sent' after today's WhatsApp client-reachout
    message has gone unanswered for > 3h.

    - Only considers clients whose `client_reachout` WA message was sent TODAY (IST).
    - Skips clients ever AI-called before (call_type='reachout') — one AI call per
      client, ever.
    - Capped at 5 calls per run.

    Note: the no_pickup/busy/failed retry (1 retry, 1h later) is handled by RinggAI
    itself via `call_retry_config` in trigger_client_ai_call(), not here.
    """
    import services.ringg_service as ringg
    from routers.whatsapp import _phones_for_client
    from routers.clients import _SOURCE_TO_PORTAL_NAME

    # Client reachout AI calls: 9am-8pm IST only (narrower than the general 9-21 business hours)
    if not test_mode and not (9 <= datetime.now(_IST).hour < 20):
        return {"sent": 0, "skipped": 0, "note": "outside_business_hours"}

    threshold_secs = (_REACHOUT_AI_CALL_TEST_MINUTES * 60) if test_mode else (_REACHOUT_AI_CALL_HOURS * 3600)

    rows = await conn.fetch(
        """
        SELECT c.*, wm.phone AS reachout_phone, wm.sent_at AS reachout_sent_at
        FROM clients c
        JOIN LATERAL (
            SELECT phone, sent_at FROM whatsapp_messages
            WHERE client_id = c.id
              AND message_type = 'client_reachout'
              AND direction = 'outbound'
              AND sent_at >= date_trunc('day', now() AT TIME ZONE 'Asia/Kolkata') AT TIME ZONE 'Asia/Kolkata'
            ORDER BY sent_at DESC LIMIT 1
        ) wm ON true
        WHERE c.stage  = 'reachout_sent'::client_stage
          AND c.is_dnd = false
          AND wm.sent_at < now() - make_interval(secs => $1)
          AND NOT EXISTS (
              SELECT 1 FROM client_ai_calls ac
              WHERE ac.client_id  = c.id
                AND ac.call_type  = 'reachout'
          )
        ORDER BY wm.sent_at ASC
        LIMIT $2
        """,
        float(threshold_secs), _REACHOUT_AI_CALL_BATCH,
    )

    sent = skipped = 0
    for c in rows:
        phones = _phones_for_client(c)
        phone = c["reachout_phone"] or (phones[0] if phones else None)
        if not phone:
            skipped += 1
            continue

        job_portal_name = _SOURCE_TO_PORTAL_NAME.get((c["source"] or "").lower(), "the job portal")

        call_row = await conn.fetchrow(
            "INSERT INTO client_ai_calls (client_id, phone, status, call_type) VALUES ($1, $2, 'queued', 'reachout') RETURNING *",
            c["id"], phone,
        )
        try:
            resp = await ringg.trigger_client_ai_call(c["id"], phone, c["company_name"] or "", job_portal_name)
            ringg_call_id = str(resp.get("call_id") or resp.get("id") or "") or None
            await conn.execute(
                "UPDATE client_ai_calls SET status='calling', ringg_call_id=$1 WHERE id=$2",
                ringg_call_id, call_row["id"],
            )
            print(f"[followup/reachout-ai-call] AI call triggered for client {c['id']} ({phone})")
            sent += 1
        except Exception as e:
            print(f"[followup/reachout-ai-call] trigger failed for client {c['id']}: {e}")
            await conn.execute(
                "UPDATE client_ai_calls SET status='failed', classification_reason=$1 WHERE id=$2",
                str(e)[:500], call_row["id"],
            )
            try:
                from services.ops_alert_service import send_alert
                await send_alert("Reachout AI call trigger failed", str(e), severity="ERROR", context={"client_id": str(c["id"]), "phone": phone})
            except Exception:
                pass
            skipped += 1
        await asyncio.sleep(2)

    return {"sent": sent, "skipped": skipped, "eligible": len(rows)}


async def _followup_funnel_stuck_ai_call(conn: asyncpg.Connection, test_mode: bool = False) -> dict:
    """
    Place an AI voice call (Funnel Stuck RinggAI agent) to clients who are still
    stuck in 'interested' (onboarding form) or 'agreement_sent' (agreement) after
    today's client_followup WhatsApp reminder has gone unanswered for > 3h.

    - Only considers clients whose `client_followup` WA reminder was sent TODAY (IST).
    - Skips clients ever AI-called before (call_type='funnel_stuck') — one AI call
      per client, ever.
    - Capped at 5 calls per run.
    """
    import services.ringg_service as ringg
    from routers.whatsapp import _phones_for_client
    from routers.clients import _FUNNEL_STUCK_STAGE_LABELS

    if not test_mode and not _is_business_hours():
        return {"sent": 0, "skipped": 0, "note": "outside_business_hours"}

    threshold_secs = (_FUNNEL_STUCK_AI_CALL_TEST_MINUTES * 60) if test_mode else (_FUNNEL_STUCK_AI_CALL_HOURS * 3600)

    rows = await conn.fetch(
        """
        SELECT c.*, wm.phone AS followup_phone, wm.sent_at AS followup_sent_at
        FROM clients c
        JOIN LATERAL (
            SELECT phone, sent_at FROM whatsapp_messages
            WHERE client_id = c.id
              AND message_type = 'client_followup'
              AND direction = 'outbound'
              AND sent_at >= date_trunc('day', now() AT TIME ZONE 'Asia/Kolkata') AT TIME ZONE 'Asia/Kolkata'
            ORDER BY sent_at DESC LIMIT 1
        ) wm ON true
        WHERE c.stage IN ('interested'::client_stage, 'agreement_sent'::client_stage)
          AND c.is_dnd = false
          AND wm.sent_at < now() - make_interval(secs => $1)
          AND NOT EXISTS (
              SELECT 1 FROM client_ai_calls ac
              WHERE ac.client_id  = c.id
                AND ac.call_type  = 'funnel_stuck'
          )
        ORDER BY wm.sent_at ASC
        LIMIT $2
        """,
        float(threshold_secs), _FUNNEL_STUCK_AI_CALL_BATCH,
    )

    sent = skipped = 0
    for c in rows:
        stage_label = _FUNNEL_STUCK_STAGE_LABELS.get(c["stage"])
        if not stage_label:
            skipped += 1
            continue

        phones = _phones_for_client(c)
        phone = c["followup_phone"] or (phones[0] if phones else None)
        if not phone:
            skipped += 1
            continue

        callee_name = c["poc_name"] or c["company_name"] or ""

        call_row = await conn.fetchrow(
            "INSERT INTO client_ai_calls (client_id, phone, status, call_type) VALUES ($1, $2, 'queued', 'funnel_stuck') RETURNING *",
            c["id"], phone,
        )
        try:
            resp = await ringg.trigger_funnel_stuck_call(c["id"], phone, callee_name, stage_label)
            ringg_call_id = str(resp.get("call_id") or resp.get("id") or "") or None
            await conn.execute(
                "UPDATE client_ai_calls SET status='calling', ringg_call_id=$1 WHERE id=$2",
                ringg_call_id, call_row["id"],
            )
            print(f"[followup/funnel-stuck-ai-call] AI call triggered for client {c['id']} ({phone})")
            sent += 1
        except Exception as e:
            print(f"[followup/funnel-stuck-ai-call] trigger failed for client {c['id']}: {e}")
            await conn.execute(
                "UPDATE client_ai_calls SET status='failed', classification_reason=$1 WHERE id=$2",
                str(e)[:500], call_row["id"],
            )
            try:
                from services.ops_alert_service import send_alert
                await send_alert("Funnel stuck AI call trigger failed", str(e), severity="ERROR", context={"client_id": str(c["id"]), "phone": phone})
            except Exception:
                pass
            skipped += 1
        await asyncio.sleep(2)

    return {"sent": sent, "skipped": skipped, "eligible": len(rows)}


async def _followup_onboarding(conn: asyncpg.Connection, test_mode: bool = False) -> dict:
    """
    Remind interested clients who haven't filled the onboarding form.
    Thresholds: t+12h, t+24h, t+48h from onboarding_form_sent_at.
    Stops automatically once client moves past 'interested' stage.
    """
    import services.msg91_service as msg91

    if not test_mode and not _is_business_hours():
        return {"sent": 0, "skipped": 0, "note": "outside_business_hours"}

    divisor = 60 if test_mode else 3600
    thresholds = _TEST_THRESHOLDS if test_mode else _ONBOARDING_THRESHOLDS
    frontend_url = os.environ.get("FRONTEND_URL", "http://localhost:5173").rstrip("/")
    now = datetime.now(timezone.utc)
    clients = await conn.fetch(
        """
        SELECT id, poc_phone, poc_name, job_title, company_name,
               onboarding_form_sent_at, onboarding_reminder_count,
               last_onboarding_reminder_at
        FROM clients
        WHERE stage     = 'interested'::client_stage
          AND is_dnd    = false
          AND poc_phone IS NOT NULL
          AND onboarding_form_sent_at IS NOT NULL
          AND (onboarding_reminder_count IS NULL OR onboarding_reminder_count < 3)
        """
    )

    from collections import defaultdict
    by_phone: dict = defaultdict(list)
    for c in clients:
        by_phone[c["poc_phone"]].append(c)

    sent = skipped = 0
    for phone, group in by_phone.items():
        count = min(int(c["onboarding_reminder_count"] or 0) for c in group)
        ref = next(c for c in group if int(c["onboarding_reminder_count"] or 0) == count)
        ref_time  = ref["last_onboarding_reminder_at"] or ref["onboarding_form_sent_at"]
        elapsed_h = (now - ref_time).total_seconds() / divisor
        if elapsed_h < thresholds[count]:
            skipped += len(group)
            continue

        template = _ONBOARDING_TEMPLATES[count]
        onboarding_url = f"{frontend_url}/client-onboard"
        components = [{"type": "body", "parameters": [
            {"type": "text", "text": onboarding_url},
        ]}]
        try:
            await msg91.send_template(phone, template, "en", components, sender="client")
            for c in group:
                await conn.execute(
                    """
                    UPDATE clients SET
                        onboarding_reminder_count   = COALESCE(onboarding_reminder_count, 0) + 1,
                        last_onboarding_reminder_at = now(),
                        updated_at                  = now()
                    WHERE id = $1
                    """,
                    c["id"],
                )
                await conn.execute(
                    """
                    INSERT INTO whatsapp_messages
                        (client_id, message_type, direction, phone, status, sent_at, used_template_name)
                    VALUES ($1, 'client_followup', 'outbound', $2, 'sent', now(), $3)
                    """,
                    c["id"], phone, template,
                )
            sent += 1
        except Exception as e:
            print(f"[followup/onboarding] {phone}: {e}")
            try:
                from services.ops_alert_service import send_alert
                await send_alert("Onboarding reminder send failed", str(e), severity="ERROR", context={"phone": phone, "template": template, "reminder_count": count})
            except Exception:
                pass
            skipped += len(group)

    return {"sent": sent, "skipped": skipped}


async def _followup_agreement(conn: asyncpg.Connection, test_mode: bool = False) -> dict:
    """
    Remind agreement_sent clients who haven't signed. Business hours (9–21 IST) only.
    Thresholds: t+5h, t+24h, t+48h from agreement_sent_at.
    Stops automatically once client moves to onboarded/disqualified.
    """
    import services.msg91_service as msg91

    if not test_mode and not _is_business_hours():
        return {"sent": 0, "skipped": 0, "note": "outside_business_hours"}

    divisor = 60 if test_mode else 3600
    thresholds = _TEST_THRESHOLDS if test_mode else _AGREEMENT_THRESHOLDS
    now = datetime.now(timezone.utc)
    clients = await conn.fetch(
        """
        SELECT id, poc_phone, poc_name, job_title, company_name,
               agreement_sent_at, agreement_reminder_count,
               last_agreement_reminder_at
        FROM clients
        WHERE stage     = 'agreement_sent'::client_stage
          AND is_dnd    = false
          AND poc_phone IS NOT NULL
          AND agreement_sent_at IS NOT NULL
          AND (agreement_reminder_count IS NULL OR agreement_reminder_count < 3)
        """
    )

    # Group by poc_phone so multiple positions for the same client get one message
    from collections import defaultdict
    by_phone: dict = defaultdict(list)
    for c in clients:
        by_phone[c["poc_phone"]].append(c)

    sent = skipped = 0
    for phone, group in by_phone.items():
        # Use the minimum reminder count across positions as the reference
        count = min(int(c["agreement_reminder_count"] or 0) for c in group)
        ref = next(c for c in group if int(c["agreement_reminder_count"] or 0) == count)
        ref_time  = ref["last_agreement_reminder_at"] or ref["agreement_sent_at"]
        elapsed_h = (now - ref_time).total_seconds() / divisor
        if elapsed_h < thresholds[count]:
            skipped += len(group)
            continue

        template = _AGREEMENT_TEMPLATES[count]
        poc = ref["poc_name"] or ref["company_name"] or "there"
        job_titles = [c["job_title"] for c in group if c["job_title"]]
        job = " & ".join(job_titles) if job_titles else "the position"
        components = [{"type": "body", "parameters": [
            {"type": "text", "text": poc},
            {"type": "text", "text": job},
        ]}]
        try:
            await msg91.send_template(phone, template, "en", components, sender="client")
            for c in group:
                await conn.execute(
                    """
                    UPDATE clients SET
                        agreement_reminder_count   = COALESCE(agreement_reminder_count, 0) + 1,
                        last_agreement_reminder_at = now(),
                        updated_at                 = now()
                    WHERE id = $1
                    """,
                    c["id"],
                )
                await conn.execute(
                    """
                    INSERT INTO whatsapp_messages
                        (client_id, message_type, direction, phone, status, sent_at, used_template_name)
                    VALUES ($1, 'client_followup', 'outbound', $2, 'sent', now(), $3)
                    """,
                    c["id"], phone, template,
                )
            sent += 1
        except Exception as e:
            print(f"[followup/agreement] {phone}: {e}")
            try:
                from services.ops_alert_service import send_alert
                await send_alert("Agreement reminder send failed", str(e), severity="ERROR", context={"phone": phone, "template": template, "reminder_count": count})
            except Exception:
                pass
            skipped += len(group)

    return {"sent": sent, "skipped": skipped}


async def _followup_slot(conn: asyncpg.Connection, test_mode: bool = False) -> dict:
    """
    Remind clients who haven't responded to a slot request.
    Thresholds: t+3h, t+8h, t+24h from slot_requested_at.
    Grouped by poc_phone — one message per client per cron run regardless of how
    many candidate mappings are waiting. All mappings in the group advance together.
    Stops automatically once mappings move past slot_requested_client.
    """
    import services.msg91_service as msg91
    from collections import defaultdict

    if not test_mode and not _is_business_hours():
        return {"sent": 0, "skipped": 0, "note": "outside_business_hours"}

    divisor = 60 if test_mode else 3600
    thresholds = _TEST_THRESHOLDS if test_mode else _SLOT_THRESHOLDS
    now = datetime.now(timezone.utc)
    rows = await conn.fetch(
        """
        SELECT
            ccm.id              AS mapping_id,
            ccm.client_id,
            ccm.slot_requested_at,
            ccm.last_slot_reminder_at,
            COALESCE(ccm.slot_reminder_count, 0) AS slot_reminder_count,
            c.poc_phone, c.poc_name, c.job_title, c.company_name
        FROM client_candidate_mappings ccm
        JOIN clients c ON c.id = ccm.client_id
        WHERE ccm.stage             = 'slot_requested_client'::mapping_stage
          AND c.is_dnd              = false
          AND c.poc_phone           IS NOT NULL
          AND ccm.slot_requested_at IS NOT NULL
          AND COALESCE(ccm.slot_reminder_count, 0) < 3
        """
    )

    # Group by poc_phone — one message per client, all their mappings advance together
    by_phone: dict = defaultdict(list)
    for r in rows:
        by_phone[r["poc_phone"]].append(r)

    sent = skipped = 0
    for phone, group in by_phone.items():
        # Use the mapping with the lowest reminder count as reference
        count = min(int(r["slot_reminder_count"]) for r in group)
        ref   = next(r for r in group if int(r["slot_reminder_count"]) == count)

        ref_time  = ref["last_slot_reminder_at"] or ref["slot_requested_at"]
        elapsed_h = (now - ref_time).total_seconds() / divisor
        if elapsed_h < thresholds[count]:
            skipped += len(group)
            continue

        template = _SLOT_TEMPLATES[count]
        poc = ref["poc_name"] or ref["company_name"] or "there"
        job = ref["job_title"] or "the position"
        components = [{"type": "body", "parameters": [
            {"type": "text", "text": poc},
            {"type": "text", "text": job},
        ]}]
        try:
            await msg91.send_template(phone, template, "en", components, sender="client")
            # Advance ALL mappings for this client together
            for r in group:
                await conn.execute(
                    """
                    UPDATE client_candidate_mappings SET
                        slot_reminder_count   = COALESCE(slot_reminder_count, 0) + 1,
                        last_slot_reminder_at = now()
                    WHERE id = $1
                    """,
                    r["mapping_id"],
                )
                await conn.execute(
                    """
                    INSERT INTO whatsapp_messages
                        (client_id, mapping_id, message_type, direction, phone, status, sent_at, used_template_name)
                    VALUES ($1, $2, 'client_followup', 'outbound', $3, 'sent', now(), $4)
                    """,
                    r["client_id"], r["mapping_id"], phone, template,
                )
            sent += 1
        except Exception as e:
            print(f"[followup/slot] phone {phone}: {e}")
            skipped += len(group)

    return {"sent": sent, "skipped": skipped}


@router.post("/client-followups")
async def cron_client_followups(
    x_cron_secret: str = Header(None, alias="X-Cron-Secret"),
    test: bool = Query(False, description="Use minutes instead of hours for all thresholds (testing only)"),
    conn: asyncpg.Connection = Depends(get_conn),
):
    """
    Runs every 30 minutes. Single endpoint handling all client pipeline follow-ups:

      interested       → onboarding form not filled  → client_onboarding_r1/2/3  (t+12/24/48h)
      agreement_sent   → agreement not signed         → client_agreement_r1/2/3   (t+5/24/48h, 9–21 IST)
      slot_requested   → no slot response from client → client_slot_r1/2/3        (t+2/5/12h)
      reachout_sent    → stuck after today's WA reachout, >3h unanswered → AI voice
                         call via the client-reachout RinggAI agent (max 5/run)
      interested /
      agreement_sent   → stuck after today's WA reminder, >3h unanswered → AI voice
                         call via the Funnel Stuck RinggAI agent (max 5/run)

    Reminders for a stage stop automatically once the client moves to the next stage.
    New DB columns added idempotently on first run.
    """
    from datetime import timedelta

    expected = os.environ.get("CRON_SECRET", "")
    if expected and x_cron_secret != expected:
        raise HTTPException(401, "Invalid cron secret")

    onboarding           = await _followup_onboarding(conn, test_mode=test)
    agreement            = await _followup_agreement(conn, test_mode=test)
    reachout_ai_call     = {"sent": 0, "skipped": 0, "note": "disabled"}
    funnel_stuck_ai_call = {"sent": 0, "skipped": 0, "note": "disabled"}

    # Interviews whose slot time has passed → mark interview_done + request
    # feedback from the client (candidate is asked separately, later, only if
    # the client likes them — see candidate-followups). This was previously a
    # standalone endpoint (outreach.cron_feedback_requests) that was never
    # wired to any scheduled job, so it never ran — riding it on this
    # already-scheduled cron instead of provisioning new infrastructure for it.
    from routers.outreach import cron_feedback_requests as _cron_feedback_requests
    feedback_requests_result = await _cron_feedback_requests(conn=conn)
    feedback_requests = {"sent": feedback_requests_result["data"]["feedback_requests_sent"]}

    # Every 2h (gated internally): re-remind clients who still haven't given
    # feedback on one or more interview_done candidates.
    feedback_pending = await _followup_feedback_pending(conn)

    # Every 2h (gated internally): re-remind clients who still haven't reviewed
    # one or more shortlisted candidates sitting in client_approval_pending.
    review_pending = await _followup_review_pending(conn)

    # Customer Executive auto-assignment sweep — no messages sent, just assigns
    # newly-eligible clients to a CE's call queue. See ce_service.sweep_assign.
    from services.ce_service import sweep_assign
    ce_assignment = await sweep_assign(conn)

    # System-detected tickets — client relationships where the reminder crons
    # above have already nudged (or, for review_pending, never stop nudging)
    # with no result, plus placements ready to be finalized with nobody
    # nudged toward it. Turns "stuck/ready with no result" into a claimable
    # ticket instead of silence. See ticket_service.sweep_stuck_in_pipeline /
    # sweep_ready_to_advance / sweep_client_closure.
    from services.ticket_service import sweep_stuck_in_pipeline, sweep_ready_to_advance, sweep_client_closure
    stuck_tickets = await sweep_stuck_in_pipeline(conn)
    ready_tickets = await sweep_ready_to_advance(conn)
    closure_tickets = await sweep_client_closure(conn)

    total_sent = (
        onboarding["sent"] + agreement["sent"]
        + reachout_ai_call["sent"] + funnel_stuck_ai_call["sent"]
        + feedback_requests["sent"] + feedback_pending["sent"] + review_pending["sent"]
    )
    print(f"[cron/client-followups] total_sent={total_sent} onboarding={onboarding} agreement={agreement} reachout_ai_call={reachout_ai_call} funnel_stuck_ai_call={funnel_stuck_ai_call} feedback_requests={feedback_requests} feedback_pending={feedback_pending} review_pending={review_pending} ce_assignment={ce_assignment} stuck_tickets={stuck_tickets} ready_tickets={ready_tickets} closure_tickets={closure_tickets}")

    return {
        "data": {
            "onboarding":           onboarding,
            "agreement":            agreement,
            "reachout_ai_call":     reachout_ai_call,
            "funnel_stuck_ai_call": funnel_stuck_ai_call,
            "feedback_requests":    feedback_requests,
            "feedback_pending":     feedback_pending,
            "review_pending":       review_pending,
            "ce_assignment":        ce_assignment,
            "stuck_tickets":        stuck_tickets,
            "ready_tickets":        ready_tickets,
            "closure_tickets":      closure_tickets,
            "total_sent":           total_sent,
        },
        "ok": True,
    }


# ── Candidate follow-up reminders ────────────────────────────────────────────

_CAND_SLOT_THRESHOLDS       = [3, 8, 12]    # hours from last reminder (or slot_sent_at)
_CAND_SLOT_TEST_THRESHOLDS  = [2, 2, 2]     # minutes (test mode)
_CAND_SLOT_TEMPLATES        = [WT.CANDIDATE_SLOT_R1_V3, WT.CANDIDATE_SLOT_R2_V3, WT.CANDIDATE_SLOT_R3_V3]
_CAND_DOCS_REMINDER_THRESHOLDS = [24, 48]   # hours from documents_requested_at

# Slot-link expiry: candidate received the interview-slot link but never booked
_SLOT_LINK_EXPIRY_HOURS        = 48   # hours from slot_sent_at
_SLOT_LINK_EXPIRY_TEST_MINUTES = 10   # minutes (test mode)

_SLOT_AI_CALL_MINUTES      = 30   # minutes after slot_link_sent_at before AI call fires
_SLOT_AI_CALL_TEST_MINUTES = 2    # minutes (test mode)

_PASSIVE_FORM_THRESHOLDS      = [3, 8, 24]  # hours from passive_form_sent_at (absolute, not rolling)
_PASSIVE_FORM_TEST_THRESHOLDS = [2, 4, 6]   # minutes (test mode)
_PASSIVE_FORM_TEMPLATES       = [WT.CANDIDATE_PASSIVE_FORM_R1, WT.CANDIDATE_PASSIVE_FORM_R2, WT.CANDIDATE_PASSIVE_FORM_R3]

_INTERESTED_FORM_THRESHOLDS      = [3, 8, 24]
_INTERESTED_FORM_TEST_THRESHOLDS = [2, 4, 6]
_INTERESTED_FORM_TEMPLATES       = [WT.CANDIDATE_INTERESTED_FORM_R1, WT.CANDIDATE_INTERESTED_FORM_R2, WT.CANDIDATE_INTERESTED_FORM_R3]

_FRONTEND_BASE = os.environ.get("FRONTEND_URL", "http://localhost:5173")


def _fmt_ist(dt) -> str:
    """Format a UTC datetime as '10:00 AM, Mon 26 May' in IST."""
    if dt is None:
        return ""
    return dt.astimezone(_IST).strftime("%-I:%M %p, %a %-d %b")


async def _followup_interested_form(conn: asyncpg.Connection, test_mode: bool = False) -> dict:
    """
    Remind candidates who tapped Interested Role or Not Interested Role on
    hl_cand_jd_img_v3 and got stamped with interested_form_sent_at (see
    flow_engine._handle_interested_response / _handle_not_interested_role_response)
    but haven't submitted the form yet. Not Interested Role only qualifies when
    decline_reason='not_interested_role' (declined this role, still open to
    others) — NOT_LOOKING_FOR_JOB declines the candidate entirely and shouldn't
    be nudged.
    Thresholds are absolute from interested_form_sent_at: T+3h, T+8h, T+24h.
    """
    import services.msg91_service as msg91

    divisor    = 60 if test_mode else 3600
    thresholds = _INTERESTED_FORM_TEST_THRESHOLDS if test_mode else _INTERESTED_FORM_THRESHOLDS
    now = datetime.now(timezone.utc)
    frontend_url = os.environ.get("FRONTEND_URL", "http://localhost:5173").strip().rstrip("/")
    form_url = f"{frontend_url}/apply"

    rows = await conn.fetch(
        """
        SELECT
            ccm.id              AS mapping_id,
            ccm.interested_form_sent_at,
            COALESCE(ccm.interested_form_reminder_count, 0) AS reminder_count,
            c.phone, c.name     AS cand_name
        FROM client_candidate_mappings ccm
        JOIN candidates c ON c.id = ccm.candidate_id
        WHERE ccm.interested_form_sent_at IS NOT NULL
          AND COALESCE(c.form_submitted, false) = false
          AND (
              ccm.stage = 'interested'::mapping_stage
              OR (ccm.stage = 'not_interested'::mapping_stage AND ccm.decline_reason = 'not_interested_role')
          )
          AND COALESCE(ccm.interested_form_reminder_count, 0) < 3
          AND c.is_active = true
        """
    )

    sent = skipped = 0
    for r in rows:
        count   = int(r["reminder_count"])
        elapsed = (now - r["interested_form_sent_at"]).total_seconds() / divisor
        if elapsed < thresholds[count]:
            skipped += 1
            continue

        cand_name = (r["cand_name"] or "").split()[0] if r["cand_name"] else "there"
        template  = _INTERESTED_FORM_TEMPLATES[count]
        components = [{"type": "body", "parameters": [
            {"type": "text", "text": cand_name},
            {"type": "text", "text": form_url},
        ]}]
        try:
            await msg91.send_template(r["phone"], template, "en", components, sender="candidate")
            await conn.execute(
                """
                UPDATE client_candidate_mappings SET
                    interested_form_reminder_count = COALESCE(interested_form_reminder_count, 0) + 1,
                    updated_at                     = now()
                WHERE id = $1
                """,
                r["mapping_id"],
            )
            sent += 1
        except Exception as e:
            print(f"[followup/interested_form] {r['mapping_id']}: {e}")
            skipped += 1

    return {"sent": sent, "skipped": skipped}


async def _followup_passive_form(conn: asyncpg.Connection, test_mode: bool = False) -> dict:
    """
    Remind candidates who tapped NOT_INTERESTED_ROLE and received the form link
    but haven't submitted the form yet.
    Thresholds are absolute from passive_form_sent_at: T+3h, T+8h, T+24h.
    """
    import services.msg91_service as msg91

    divisor    = 60 if test_mode else 3600
    thresholds = _PASSIVE_FORM_TEST_THRESHOLDS if test_mode else _PASSIVE_FORM_THRESHOLDS
    now = datetime.now(timezone.utc)
    frontend_url = os.environ.get("FRONTEND_URL", "http://localhost:5173").strip().rstrip("/")
    form_url = f"{frontend_url}/apply?intent=passive"

    rows = await conn.fetch(
        """
        SELECT
            ccm.id              AS mapping_id,
            ccm.passive_form_sent_at,
            COALESCE(ccm.passive_form_reminder_count, 0) AS reminder_count,
            c.phone, c.name     AS cand_name
        FROM client_candidate_mappings ccm
        JOIN candidates c ON c.id = ccm.candidate_id
        WHERE ccm.passive_form_sent_at IS NOT NULL
          AND COALESCE(c.form_submitted, false) = false
          AND COALESCE(ccm.passive_form_reminder_count, 0) < 3
          AND c.is_active = true
        """
    )

    sent = skipped = 0
    for r in rows:
        count    = int(r["reminder_count"])
        elapsed  = (now - r["passive_form_sent_at"]).total_seconds() / divisor
        if elapsed < thresholds[count]:
            skipped += 1
            continue

        cand_name = (r["cand_name"] or "").split()[0] if r["cand_name"] else "there"
        template   = _PASSIVE_FORM_TEMPLATES[count]
        components = [{"type": "body", "parameters": [
            {"type": "text", "text": cand_name},
            {"type": "text", "text": form_url},
        ]}]
        try:
            await msg91.send_template(r["phone"], template, "en", components, sender="candidate")
            await conn.execute(
                """
                UPDATE client_candidate_mappings SET
                    passive_form_reminder_count   = COALESCE(passive_form_reminder_count, 0) + 1,
                    updated_at                    = now()
                WHERE id = $1
                """,
                r["mapping_id"],
            )
            sent += 1
        except Exception as e:
            print(f"[followup/passive_form] {r['mapping_id']}: {e}")
            skipped += 1

    return {"sent": sent, "skipped": skipped}


async def _followup_slot_ai_call(conn: asyncpg.Connection, test_mode: bool = False) -> dict:
    """
    Trigger a RinggAI slot-reminder call to candidates who have been sent
    interview slot options but haven't booked after 30 minutes.
    Fires once per mapping (tracked via slot_ai_call_triggered).
    """
    import services.ringg_service as ringg

    if not test_mode and not _is_business_hours():
        return {"sent": 0, "skipped": 0, "note": "outside_business_hours"}

    threshold_mins = _SLOT_AI_CALL_TEST_MINUTES if test_mode else _SLOT_AI_CALL_MINUTES
    now = datetime.now(timezone.utc)

    import services.msg91_service as msg91

    portal_url = os.environ.get("CANDIDATE_PORTAL_URL", os.environ.get("FRONTEND_URL", "http://localhost:5174")).rstrip("/")
    slot_template = WT.CANDIDATE_SLOT_SELECT

    rows = await conn.fetch(
        """
        SELECT
            ccm.id           AS mapping_id,
            ccm.candidate_id,
            ccm.client_id,
            ccm.slot_link_sent_at,
            c.phone, c.name  AS cand_name,
            cl.company_name, cl.job_title
        FROM client_candidate_mappings ccm
        JOIN candidates c  ON c.id  = ccm.candidate_id
        JOIN clients    cl ON cl.id = ccm.client_id
        WHERE ccm.stage               = 'slot_sent_candidate'::mapping_stage
          AND ccm.slot_link_sent_at   IS NOT NULL
          AND COALESCE(ccm.slot_ai_call_triggered, false) = false
          AND c.is_active             = true
          AND (c.dnd_until IS NULL OR c.dnd_until < now())
        """
    )

    sent = skipped = 0
    for r in rows:
        elapsed_mins = (now - r["slot_link_sent_at"]).total_seconds() / 60
        if elapsed_mins < threshold_mins:
            skipped += 1
            continue

        phone = r["phone"] or ""
        if not phone:
            skipped += 1
            continue

        first_name = (r["cand_name"] or "").split()[0] if r["cand_name"] else ""
        slot_url = f"{portal_url}/c/{r['candidate_id']}"

        try:
            await ringg.trigger_slot_reminder_call(
                r["mapping_id"],
                phone,
                callee_name=first_name,
                company_name=r["company_name"] or "",
            )
            await conn.execute(
                "UPDATE client_candidate_mappings SET slot_ai_call_triggered = true WHERE id = $1",
                r["mapping_id"],
            )
            print(f"[cron/slot-ai-call] triggered for mapping {r['mapping_id']} ({phone}, {elapsed_mins:.1f}m since link sent)")
            sent += 1
        except Exception as e:
            print(f"[cron/slot-ai-call] failed for mapping {r['mapping_id']}: {e}")
            try:
                from services.ops_alert_service import send_alert
                await send_alert("Slot reminder AI call failed", str(e), severity="ERROR", context={"mapping_id": str(r["mapping_id"]), "phone": phone})
            except Exception:
                pass
            skipped += 1
            continue

        # Re-send slot link via WhatsApp after triggering the call
        try:
            if slot_template:
                components = [{"type": "body", "parameters": [
                    {"type": "text", "text": r["cand_name"] or ""},
                    {"type": "text", "text": r["company_name"] or ""},
                    {"type": "text", "text": r["job_title"] or ""},
                    {"type": "text", "text": slot_url},
                ]}]
                await msg91.send_template(phone, slot_template, "en", components, sender="candidate")
            else:
                await msg91.send_text(
                    phone,
                    f"Hi {first_name or 'there'}, here is your interview slot selection link for "
                    f"*{r['company_name'] or 'the company'}*:\n{slot_url}",
                    sender="candidate",
                )
            await conn.execute(
                """
                INSERT INTO whatsapp_messages
                    (client_id, candidate_id, mapping_id, message_type, direction, phone, status, sent_at, used_template_name)
                VALUES ($1, $2, $3, 'candidate_slot', 'outbound', $4, 'sent', now(), $5)
                """,
                r["client_id"], r["candidate_id"], r["mapping_id"], phone, slot_template or None,
            )
            print(f"[cron/slot-ai-call] slot link re-sent to {phone} for mapping {r['mapping_id']}")
        except Exception as e:
            print(f"[cron/slot-ai-call] slot link re-send failed for {phone}: {e}")

    return {"sent": sent, "skipped": skipped}


async def _followup_candidate_slot(conn: asyncpg.Connection, test_mode: bool = False) -> dict:
    """
    Remind candidates who haven't selected an interview slot.
    Thresholds: t+3h, t+8h, t+24h from slot_sent_at (t+2m each in test mode).
    Stops once mapping moves past slot_sent_candidate.
    """
    import services.msg91_service as msg91

    # v3 templates carry the schedule link as a 4th body param; v4 moves it to
    # a URL button instead (matching the round-1/round-2 invite templates —
    # every slot-booking message uses the same button pattern). The old
    # r1/r2/r3 templates are approved for 3 params/no button and would reject
    # a mismatched payload (same failure mode fixed in matching.py's
    # slot-send functions), so the format is only switched once
    # _CAND_SLOT_TEMPLATES actually points at the new names.
    _V3_SLOT_TEMPLATES = {WT.CANDIDATE_SLOT_R1_V3, WT.CANDIDATE_SLOT_R2_V3, WT.CANDIDATE_SLOT_R3_V3}
    _V4_SLOT_TEMPLATES = {WT.CANDIDATE_SLOT_R1_V4, WT.CANDIDATE_SLOT_R2_V4, WT.CANDIDATE_SLOT_R3_V4}
    portal_url = os.environ.get("CANDIDATE_PORTAL_URL", os.environ.get("FRONTEND_URL", "http://localhost:5174")).rstrip("/")

    divisor    = 60 if test_mode else 3600
    thresholds = _CAND_SLOT_TEST_THRESHOLDS if test_mode else _CAND_SLOT_THRESHOLDS
    now = datetime.now(timezone.utc)
    rows = await conn.fetch(
        """
        SELECT
            ccm.id              AS mapping_id,
            ccm.candidate_id,
            ccm.slot_link_sent_at,
            ccm.last_slot_selection_reminder_at,
            COALESCE(ccm.slot_selection_reminder_count, 0) AS reminder_count,
            c.phone, c.name     AS cand_name,
            cl.job_title, cl.company_name
        FROM client_candidate_mappings ccm
        JOIN candidates c  ON c.id  = ccm.candidate_id
        JOIN clients    cl ON cl.id = ccm.client_id
        WHERE ccm.stage    = 'slot_sent_candidate'::mapping_stage
          AND ccm.slot_link_sent_at IS NOT NULL
          AND COALESCE(ccm.slot_selection_reminder_count, 0) < 3
        """
    )

    sent = skipped = 0
    for r in rows:
        count     = int(r["reminder_count"])
        ref_time  = r["last_slot_selection_reminder_at"] or r["slot_link_sent_at"]
        elapsed_h = (now - ref_time).total_seconds() / divisor
        if elapsed_h < thresholds[count]:
            skipped += 1
            continue

        template = _CAND_SLOT_TEMPLATES[count]
        params = [
            {"type": "text", "text": r["cand_name"] or ""},
            {"type": "text", "text": r["job_title"]  or ""},
            {"type": "text", "text": r["company_name"] or ""},
        ]
        if template in _V3_SLOT_TEMPLATES:
            params.append({"type": "text", "text": f"{portal_url}/c/{r['candidate_id']}"})
            components = [{"type": "body", "parameters": params}]
        elif template in _V4_SLOT_TEMPLATES:
            components = [
                {"type": "body", "parameters": params},
                {"type": "button", "sub_type": "url", "index": "0", "parameters": [
                    {"type": "text", "text": f"c/{r['candidate_id']}"},
                ]},
            ]
        else:
            components = [{"type": "body", "parameters": params}]
        try:
            await msg91.send_template(r["phone"], template, "en", components, sender="candidate")
            await conn.execute(
                """
                UPDATE client_candidate_mappings SET
                    slot_selection_reminder_count   = COALESCE(slot_selection_reminder_count, 0) + 1,
                    last_slot_selection_reminder_at = now()
                WHERE id = $1
                """,
                r["mapping_id"],
            )
            sent += 1
        except Exception as e:
            print(f"[followup/cand_slot] {r['mapping_id']}: {e}")
            skipped += 1

    return {"sent": sent, "skipped": skipped}


async def _expire_unbooked_slot_links(conn: asyncpg.Connection, test_mode: bool = False) -> dict:
    """
    Closes out candidates who received an interview-slot link but never booked
    a slot within 48h of slot_link_sent_at: moves the mapping to 'rejected' (removing
    them from this client's pool), unlocks the candidate so they re-enter the
    matching pool for other clients, and sends a closure WhatsApp message
    explaining that this opportunity is closed and newer ones will follow.
    """
    import services.msg91_service as msg91

    divisor      = 60 if test_mode else 3600
    cutoff_value = _SLOT_LINK_EXPIRY_TEST_MINUTES if test_mode else _SLOT_LINK_EXPIRY_HOURS
    now          = datetime.now(timezone.utc)
    template     = WT.CANDIDATE_SLOT_LINK_EXPIRED

    rows = await conn.fetch(
        """
        SELECT ccm.id AS mapping_id, ccm.candidate_id, ccm.client_id, ccm.slot_link_sent_at,
               c.phone, c.name AS candidate_name,
               cl.company_name, cl.job_title
        FROM client_candidate_mappings ccm
        JOIN candidates c  ON c.id  = ccm.candidate_id
        JOIN clients    cl ON cl.id = ccm.client_id
        WHERE ccm.stage = 'slot_sent_candidate'::mapping_stage
          AND ccm.slot_link_sent_at IS NOT NULL
        """
    )

    sent = skipped = 0
    for r in rows:
        elapsed = (now - r["slot_link_sent_at"]).total_seconds() / divisor
        if elapsed < cutoff_value:
            skipped += 1
            continue

        mapping_id, candidate_id, client_id = r["mapping_id"], r["candidate_id"], r["client_id"]
        name, company, job_title = r["candidate_name"] or "", r["company_name"] or "", r["job_title"] or ""
        phone = r["phone"] or ""

        await conn.execute(
            """
            UPDATE client_candidate_mappings
            SET stage = 'rejected'::mapping_stage, rejection_reason = 'slot_expired', updated_at = now()
            WHERE id = $1
            """,
            mapping_id,
        )
        await conn.execute(
            """
            UPDATE candidate_locks
            SET unlocked_at = now(), unlock_reason = 'slot_link_expired'
            WHERE candidate_id = $1 AND client_id = $2 AND unlocked_at IS NULL
            """,
            candidate_id, client_id,
        )

        if phone:
            try:
                if template:
                    components = [{"type": "body", "parameters": [
                        {"type": "text", "text": name},
                        {"type": "text", "text": job_title},
                        {"type": "text", "text": company},
                    ]}]
                    await msg91.send_template(phone, template, "en", components, sender="candidate")
                else:
                    await msg91.send_text(
                        phone,
                        f"Hi {name or 'there'}, we did not receive your interview slot selection for "
                        f"*{company or 'this opportunity'}* in time, so it has now been closed on our end.\n\n"
                        f"No worries — we'll keep sharing newer matching opportunities with you. Stay tuned! 🙏",
                        sender="candidate",
                    )
                await conn.execute(
                    """
                    INSERT INTO whatsapp_messages
                        (candidate_id, mapping_id, message_type, direction, phone, status, sent_at, used_template_name)
                    VALUES ($1, $2, 'candidate_slot_link_expired', 'outbound', $3, 'sent', now(), $4)
                    """,
                    candidate_id, mapping_id, phone, template or None,
                )
            except Exception as e:
                print(f"[cron/expire-slot-link] notify failed for mapping {mapping_id}: {e}")

        print(f"[cron/expire-slot-link] mapping {mapping_id} closed + unlocked ({elapsed:.1f}{'m' if test_mode else 'h'} since link sent)")
        sent += 1

    return {"sent": sent, "skipped": skipped}


async def _followup_interview_confirm_check(conn: asyncpg.Connection) -> dict:
    """
    One day before a scheduled interview, remind the CLIENT (not the
    candidate) it's coming up. Plain reminder only, per the WhatsApp
    reminder-only rule — no Yes/No decision is asked here. If the client
    wants to reschedule or cancel, the existing per-candidate Reschedule /
    Cancel actions in the portal's Interviews tab (company_portal.
    reschedule_interview / cancel_interview) are the mechanism, same as any
    other day. Grouped per client so someone with several interviews
    tomorrow gets one message, not one per candidate. Gated by
    interview_confirm_check_sent per mapping so a re-run of this cron on the
    same day doesn't re-send.
    """
    import services.msg91_service as msg91
    from services.flow_engine import client_portal_deep_link

    rows = await conn.fetch(
        """
        SELECT ccm.id AS mapping_id, ccm.client_id, cl.poc_name, cl.poc_phone
        FROM client_candidate_mappings ccm
        JOIN clients cl ON cl.id = ccm.client_id
        WHERE ccm.stage = 'slot_booked'::mapping_stage
          AND ccm.interview_done = false
          AND ccm.interview_slot IS NOT NULL
          AND (ccm.interview_slot AT TIME ZONE 'Asia/Kolkata')::date
              = ((now() AT TIME ZONE 'Asia/Kolkata')::date + 1)
          AND COALESCE(ccm.interview_confirm_check_sent, false) = false
          AND cl.poc_phone IS NOT NULL
        """
    )

    by_client: dict = {}
    for r in rows:
        entry = by_client.setdefault(r["client_id"], {
            "poc_name": r["poc_name"], "poc_phone": r["poc_phone"], "mapping_ids": [],
        })
        entry["mapping_ids"].append(r["mapping_id"])

    template = WT.CLIENT_INTERVIEW_CONFIRM_REMINDER
    portal_url = os.environ.get("CLIENT_PORTAL_URL", "https://employer.justaccountants.in").rstrip("/")
    sent = skipped = 0
    for client_id, info in by_client.items():
        count = len(info["mapping_ids"])
        deep_link = client_portal_deep_link(client_id, "interviews")
        try:
            if template:
                components = [
                    {"type": "body", "parameters": [
                        {"type": "text", "text": info["poc_name"] or "there"},
                        {"type": "text", "text": str(count)},
                    ]},
                    {"type": "button", "sub_type": "url", "index": "0", "parameters": [
                        {"type": "text", "text": deep_link},
                    ]},
                ]
                await msg91.send_template(info["poc_phone"], template, "en", components, sender="client")
            else:
                await msg91.send_text(
                    info["poc_phone"],
                    f"Hi {info['poc_name'] or 'there'}, you have {count} interview(s) scheduled for "
                    f"tomorrow. If you'd like to reschedule or cancel any of them, please visit your "
                    f"hiring portal: {portal_url}/{deep_link}",
                    sender="client",
                )
            await conn.execute(
                "UPDATE client_candidate_mappings SET interview_confirm_check_sent = true WHERE id = ANY($1::uuid[])",
                info["mapping_ids"],
            )
            sent += count
        except Exception as e:
            print(f"[followup/interview_confirm_check] client {client_id}: {e}")
            skipped += count

    return {"sent": sent, "skipped": skipped}


async def _followup_interview_reminders(conn: asyncpg.Connection) -> dict:
    """
    24h-before and 8am-day-of interview reminders to candidates.
    Uses interview_reminder_sent (24h) and interview_day_reminder_sent (day-of).
    """
    import services.msg91_service as msg91

    now     = datetime.now(timezone.utc)
    now_ist = now.astimezone(_IST)
    sent = skipped = 0

    # ── 24h reminder ──────────────────────────────────────────────────────────
    rows_24h = await conn.fetch(
        """
        SELECT ccm.id, ccm.interview_slot, c.phone, c.name AS cand_name,
               cl.company_name, cl.job_title
        FROM client_candidate_mappings ccm
        JOIN candidates c  ON c.id  = ccm.candidate_id
        JOIN clients    cl ON cl.id = ccm.client_id
        WHERE ccm.stage = 'slot_booked'::mapping_stage
          AND ccm.interview_done  = false
          AND ccm.interview_slot IS NOT NULL
          AND ccm.interview_slot  > now()
          AND ccm.interview_slot <= now() + interval '25 hours'
          AND COALESCE(ccm.interview_reminder_sent, false) = false
        """
    )
    for r in rows_24h:
        components = [{"type": "body", "parameters": [
            {"type": "text", "text": r["cand_name"]    or ""},
            {"type": "text", "text": r["company_name"] or ""},
            {"type": "text", "text": r["job_title"]    or ""},
            {"type": "text", "text": _fmt_ist(r["interview_slot"])},
        ]}]
        try:
            await msg91.send_template(r["phone"], WT.CANDIDATE_IV_24H, "en", components, sender="candidate")
            await conn.execute(
                "UPDATE client_candidate_mappings SET interview_reminder_sent = true WHERE id = $1",
                r["id"],
            )
            sent += 1
        except Exception as e:
            print(f"[followup/iv_24h] {r['id']}: {e}")
            skipped += 1

    # ── 8am day-of reminder ───────────────────────────────────────────────────
    if now_ist.hour >= 8:
        rows_day = await conn.fetch(
            """
            SELECT ccm.id, ccm.interview_slot, c.phone, c.name AS cand_name,
                   cl.company_name, cl.job_title
            FROM client_candidate_mappings ccm
            JOIN candidates c  ON c.id  = ccm.candidate_id
            JOIN clients    cl ON cl.id = ccm.client_id
            WHERE ccm.stage = 'slot_booked'::mapping_stage
              AND ccm.interview_done  = false
              AND ccm.interview_slot IS NOT NULL
              AND ccm.interview_slot  > now()
              AND (ccm.interview_slot AT TIME ZONE 'Asia/Kolkata')::date
                  = (now() AT TIME ZONE 'Asia/Kolkata')::date
              AND COALESCE(ccm.interview_day_reminder_sent, false) = false
            """
        )
        for r in rows_day:
            components = [{"type": "body", "parameters": [
                {"type": "text", "text": r["cand_name"]    or ""},
                {"type": "text", "text": r["company_name"] or ""},
                {"type": "text", "text": r["job_title"]    or ""},
                {"type": "text", "text": _fmt_ist(r["interview_slot"])},
            ]}]
            try:
                await msg91.send_template(r["phone"], WT.CANDIDATE_IV_DAY, "en", components, sender="candidate")
                await conn.execute(
                    "UPDATE client_candidate_mappings SET interview_day_reminder_sent = true WHERE id = $1",
                    r["id"],
                )
                sent += 1
            except Exception as e:
                print(f"[followup/iv_day] {r['id']}: {e}")
                skipped += 1

    # ── 3h reminder ───────────────────────────────────────────────────────────
    rows_3h = await conn.fetch(
        """
        SELECT ccm.id, ccm.interview_slot, c.phone, c.name AS cand_name,
               cl.company_name, cl.job_title, cl.poc_phone
        FROM client_candidate_mappings ccm
        JOIN candidates c  ON c.id  = ccm.candidate_id
        JOIN clients    cl ON cl.id = ccm.client_id
        WHERE ccm.stage = 'slot_booked'::mapping_stage
          AND ccm.interview_done  = false
          AND ccm.interview_slot IS NOT NULL
          AND ccm.interview_slot  > now()
          AND ccm.interview_slot <= now() + interval '3 hours 30 minutes'
          AND COALESCE(ccm.interview_3h_reminder_sent, false) = false
        """
    )
    for r in rows_3h:
        poc = (r["poc_phone"] or "").strip()
        poc_display = f"+91 {poc}" if poc and not poc.startswith("+") else poc or "HR team"
        components = [{"type": "body", "parameters": [
            {"type": "text", "text": (r["cand_name"] or "").split()[0] or "there"},
            {"type": "text", "text": r["company_name"] or ""},
            {"type": "text", "text": r["job_title"]    or ""},
            {"type": "text", "text": _fmt_ist(r["interview_slot"])},
            {"type": "text", "text": poc_display},
        ]}]
        try:
            await msg91.send_template(r["phone"], WT.CANDIDATE_IV_3H, "en", components, sender="candidate")
            await conn.execute(
                "UPDATE client_candidate_mappings SET interview_3h_reminder_sent = true WHERE id = $1",
                r["id"],
            )
            sent += 1
        except Exception as e:
            print(f"[followup/iv_3h] {r['id']}: {e}")
            skipped += 1

    # ── 1h reminder ───────────────────────────────────────────────────────────
    rows_1h = await conn.fetch(
        """
        SELECT ccm.id, ccm.interview_slot, c.phone, c.name AS cand_name,
               cl.company_name, cl.job_title, cl.poc_phone
        FROM client_candidate_mappings ccm
        JOIN candidates c  ON c.id  = ccm.candidate_id
        JOIN clients    cl ON cl.id = ccm.client_id
        WHERE ccm.stage = 'slot_booked'::mapping_stage
          AND ccm.interview_done  = false
          AND ccm.interview_slot IS NOT NULL
          AND ccm.interview_slot  > now()
          AND ccm.interview_slot <= now() + interval '1 hour 30 minutes'
          AND COALESCE(ccm.interview_1h_reminder_sent, false) = false
        """
    )
    for r in rows_1h:
        poc = (r["poc_phone"] or "").strip()
        poc_display = f"+91 {poc}" if poc and not poc.startswith("+") else poc or "HR team"
        components = [{"type": "body", "parameters": [
            {"type": "text", "text": (r["cand_name"] or "").split()[0] or "there"},
            {"type": "text", "text": r["company_name"] or ""},
            {"type": "text", "text": r["job_title"]    or ""},
            {"type": "text", "text": _fmt_ist(r["interview_slot"])},
            {"type": "text", "text": poc_display},
        ]}]
        try:
            await msg91.send_template(r["phone"], WT.CANDIDATE_IV_1H, "en", components, sender="candidate")
            await conn.execute(
                "UPDATE client_candidate_mappings SET interview_1h_reminder_sent = true WHERE id = $1",
                r["id"],
            )
            sent += 1
        except Exception as e:
            print(f"[followup/iv_1h] {r['id']}: {e}")
            skipped += 1

    return {"sent": sent, "skipped": skipped}


async def _followup_candidate_documents(conn: asyncpg.Connection, test_mode: bool = False) -> dict:
    """
    Remind candidates who accepted the join-intent ask (see
    candidate_portal.respond_to_join_intent) and are in documents_requested
    but haven't submitted all 3 documents yet.
    Thresholds are absolute from documents_requested_at: T+24h, T+48h (see
    _CAND_DOCS_REMINDER_THRESHOLDS).
    """
    import services.msg91_service as msg91
    from services.flow_engine import candidate_portal_deep_link

    divisor    = 60 if test_mode else 3600
    thresholds = [2, 4] if test_mode else _CAND_DOCS_REMINDER_THRESHOLDS
    now = datetime.now(timezone.utc)
    portal_url = os.environ.get("CANDIDATE_PORTAL_URL", "https://careers.justaccountants.in").rstrip("/")
    template = WT.CANDIDATE_DOCS_REMINDER

    rows = await conn.fetch(
        """
        SELECT ccm.id AS mapping_id, ccm.documents_requested_at,
               COALESCE(ccm.documents_reminder_count, 0) AS reminder_count,
               c.phone, c.name AS cand_name, cl.company_name, cl.job_title
        FROM client_candidate_mappings ccm
        JOIN candidates c  ON c.id  = ccm.candidate_id
        JOIN clients    cl ON cl.id = ccm.client_id
        WHERE ccm.stage = 'documents_requested'::mapping_stage
          AND ccm.documents_requested_at IS NOT NULL
          AND ccm.documents_submitted_at IS NULL
          AND COALESCE(ccm.documents_reminder_count, 0) < $1
        """,
        len(thresholds),
    )

    sent = skipped = 0
    for r in rows:
        count   = int(r["reminder_count"])
        elapsed = (now - r["documents_requested_at"]).total_seconds() / divisor
        if elapsed < thresholds[count]:
            skipped += 1
            continue

        cand_name = (r["cand_name"] or "").split()[0] if r["cand_name"] else "there"
        deep_link = candidate_portal_deep_link(r["mapping_id"], "documents")
        try:
            if template:
                components = [
                    {"type": "body", "parameters": [
                        {"type": "text", "text": cand_name},
                        {"type": "text", "text": r["company_name"] or ""},
                        {"type": "text", "text": r["job_title"] or ""},
                    ]},
                    {"type": "button", "sub_type": "url", "index": "0", "parameters": [
                        {"type": "text", "text": deep_link},
                    ]},
                ]
                await msg91.send_template(r["phone"], template, "en", components, sender="candidate")
            else:
                await msg91.send_text(
                    r["phone"],
                    f"Hi {cand_name}, a reminder to upload your documents for the {r['job_title']} role "
                    f"at {r['company_name']}: {portal_url}/{deep_link}",
                    sender="candidate",
                )
            await conn.execute(
                """
                UPDATE client_candidate_mappings SET
                    documents_reminder_count   = COALESCE(documents_reminder_count, 0) + 1,
                    last_documents_reminder_at = now(),
                    updated_at                 = now()
                WHERE id = $1
                """,
                r["mapping_id"],
            )
            sent += 1
        except Exception as e:
            print(f"[followup/documents] {r['mapping_id']}: {e}")
            skipped += 1

    return {"sent": sent, "skipped": skipped}


async def _followup_post_interview_candidate(conn: asyncpg.Connection) -> dict:
    """
    2h after interview_done_at, once the client has already liked/selected this
    candidate: send the candidate a feedback page link. There's nothing to ask
    the candidate if the client hasn't chosen them yet (or rejected them) — see
    outreach.cron_feedback_requests, which only asks the client at interview_done
    time. Generates a unique token (stored in feedback_token_candidate) for the
    web page.
    """
    import services.msg91_service as msg91

    rows = await conn.fetch(
        """
        SELECT ccm.id, c.phone, c.name AS cand_name,
               cl.company_name, cl.job_title,
               ccm.feedback_token_candidate
        FROM client_candidate_mappings ccm
        JOIN candidates c  ON c.id  = ccm.candidate_id
        JOIN clients    cl ON cl.id = ccm.client_id
        WHERE ccm.stage            = 'interview_done'::mapping_stage
          AND ccm.interview_done   = true
          AND ccm.interview_done_at IS NOT NULL
          AND ccm.interview_done_at <= now() - interval '2 hours'
          AND ccm.feedback_client   = 'liked'
          AND COALESCE(ccm.post_interview_feedback_sent, false) = false
          AND (ccm.feedback_candidate IS NULL OR ccm.feedback_candidate = '')
        """
    )

    sent = skipped = 0
    for r in rows:
        # Generate token if not already set
        token = r["feedback_token_candidate"] or str(uuid.uuid4())
        feedback_url = f"{_FRONTEND_BASE}/candidate-feedback/{token}"

        components = [{"type": "body", "parameters": [
            {"type": "text", "text": r["cand_name"]    or ""},
            {"type": "text", "text": r["company_name"] or ""},
            {"type": "text", "text": r["job_title"]    or ""},
            {"type": "text", "text": feedback_url},
        ]}]
        try:
            await msg91.send_template(r["phone"], WT.CANDIDATE_POST_IV, "en", components, sender="candidate")
            await conn.execute(
                """
                UPDATE client_candidate_mappings SET
                    post_interview_feedback_sent  = true,
                    feedback_token_candidate      = $2,
                    feedback_requested_at         = now()
                WHERE id = $1
                """,
                r["id"], token,
            )
            sent += 1
        except Exception as e:
            print(f"[followup/post_iv_cand] {r['id']}: {e}")
            skipped += 1

    return {"sent": sent, "skipped": skipped}


async def _followup_feedback_pending(conn: asyncpg.Connection) -> dict:
    """
    Remind clients who still have one or more candidates sitting in
    interview_done with no feedback submitted yet. One message per client,
    aggregating the count across all their pending candidates, with a button
    deep-linking into the portal's feedback tab.

    Cadence is every 2 hours per client, gated via last_feedback_reminder_at —
    this function runs on every 30-min cron tick but only actually messages a
    client once that gate has elapsed. This is also the only place that sends
    the initial "please share your feedback" ask (outreach.cron_feedback_requests
    just transitions the mapping to interview_done and leaves feedback_client
    NULL, so the very next tick of this function picks it up as "pending" too).

    Restricted to business hours (9am-9pm IST) like every other
    candidate/client-facing send in these crons — real clients were previously
    getting this at any hour since neither this function nor its caller had a
    time gate.
    """
    if not _is_business_hours():
        return {"sent": 0, "skipped": 0}

    import services.msg91_service as msg91
    from services.flow_engine import client_portal_deep_link

    rows = await conn.fetch(
        """
        SELECT cl.id AS client_id, cl.poc_phone, cl.poc_name, cl.job_title,
               COUNT(*) AS pending_count
        FROM clients cl
        JOIN client_candidate_mappings ccm ON ccm.client_id = cl.id
        WHERE ccm.stage = 'interview_done'::mapping_stage
          AND (ccm.feedback_client IS NULL OR ccm.feedback_client = '')
          AND cl.poc_phone IS NOT NULL
          AND COALESCE(cl.is_dnd, false) = false
          AND cl.stage = 'onboarded'::client_stage
          AND (cl.last_feedback_reminder_at IS NULL
               OR cl.last_feedback_reminder_at < now() - interval '2 hours')
        GROUP BY cl.id, cl.poc_phone, cl.poc_name, cl.job_title
        """
    )

    sent = skipped = 0
    template = WT.CLIENT_PENDING_FEEDBACK
    portal_url = os.environ.get("CLIENT_PORTAL_URL", "https://employer.justaccountants.in").rstrip("/")

    for r in rows:
        poc_name = r["poc_name"] or "there"
        role = r["job_title"] or "your role"
        count = int(r["pending_count"])
        plural = "s" if count != 1 else ""
        verb = "are" if count != 1 else "is"
        try:
            if template:
                components = [
                    {"type": "body", "parameters": [
                        {"type": "text", "text": poc_name},
                        {"type": "text", "text": str(count)},
                        {"type": "text", "text": role},
                    ]},
                    {"type": "button", "sub_type": "url", "index": "0", "parameters": [
                        {"type": "text", "text": client_portal_deep_link(r["client_id"], "feedback")},
                    ]},
                ]
                await msg91.send_template(r["poc_phone"], template, "en", components, sender="client")
            else:
                await msg91.send_text(
                    r["poc_phone"],
                    f"Hi {poc_name}, {count} candidate{plural} for {role} {verb} still pending your feedback.\n\n"
                    f"Share feedback: {portal_url}/{client_portal_deep_link(r['client_id'], 'feedback')}",
                    sender="client",
                )
            await conn.execute(
                "UPDATE clients SET last_feedback_reminder_at = now() WHERE id = $1",
                r["client_id"],
            )
            sent += 1
        except Exception as e:
            print(f"[followup/feedback_pending] {r['client_id']}: {e}")
            skipped += 1

    return {"sent": sent, "skipped": skipped}


async def _followup_review_pending(conn: asyncpg.Connection) -> dict:
    """
    Remind clients who still have one or more candidates sitting in
    client_approval_pending (i.e. shortlisted, awaiting the client's review)
    that haven't been acted on. Same shape as _followup_feedback_pending:
    one aggregated message per client, gated to every 2h via
    last_review_reminder_at.

    notify_client_pending_review (flow_engine.py) already sends a reactive,
    one-time, per-candidate ping the moment a candidate first enters this
    stage — but nothing ever re-reminds if the client doesn't act on it. This
    is that missing re-reachout, riding on the same already-scheduled cron.

    Uses a separate template (CLIENT_REVIEW_REMINDER_TEMPLATE) from the
    reactive nudge — that one names a single newly-added candidate ("One new
    candidate {{2}} pending review..."), which doesn't fit a backlog reminder
    that may cover several accumulated candidates, so this one is count-based
    instead ("you still have {{2}} candidate(s) pending review...").
    notify_client_pending_review also stamps last_review_reminder_at on its
    reactive send, so this reminder correctly waits a full 2h from whichever
    "pending review" message the client most recently received.
    """
    import services.msg91_service as msg91
    from services.flow_engine import client_portal_deep_link

    rows = await conn.fetch(
        """
        SELECT cl.id AS client_id, cl.poc_phone, cl.poc_name, cl.job_title,
               COUNT(*) AS pending_count
        FROM clients cl
        JOIN client_candidate_mappings ccm ON ccm.client_id = cl.id
        WHERE ccm.stage = 'client_approval_pending'::mapping_stage
          AND cl.poc_phone IS NOT NULL
          AND COALESCE(cl.is_dnd, false) = false
          AND cl.stage = 'onboarded'::client_stage
          AND (cl.last_review_reminder_at IS NULL
               OR cl.last_review_reminder_at < now() - interval '2 hours')
        GROUP BY cl.id, cl.poc_phone, cl.poc_name, cl.job_title
        """
    )

    sent = skipped = 0
    template = WT.CLIENT_REVIEW_REMINDER
    portal_url = os.environ.get("CLIENT_PORTAL_URL", "https://employer.justaccountants.in").rstrip("/")

    for r in rows:
        poc_name = r["poc_name"] or "there"
        role = r["job_title"] or "your role"
        count = int(r["pending_count"])
        plural = "s" if count != 1 else ""
        verb = "are" if count != 1 else "is"
        try:
            if template:
                components = [
                    {"type": "body", "parameters": [
                        {"type": "text", "text": poc_name},
                        {"type": "text", "text": str(count)},
                        {"type": "text", "text": role},
                    ]},
                    {"type": "button", "sub_type": "url", "index": "0", "parameters": [
                        {"type": "text", "text": client_portal_deep_link(r["client_id"], "review")},
                    ]},
                ]
                await msg91.send_template(r["poc_phone"], template, "en", components, sender="client")
            else:
                await msg91.send_text(
                    r["poc_phone"],
                    f"Hi {poc_name}, {count} candidate{plural} for {role} {verb} still pending your review.\n\n"
                    f"Review now: {portal_url}/{client_portal_deep_link(r['client_id'], 'review')}",
                    sender="client",
                )
            await conn.execute(
                "UPDATE clients SET last_review_reminder_at = now() WHERE id = $1",
                r["client_id"],
            )
            sent += 1
        except Exception as e:
            print(f"[followup/review_pending] {r['client_id']}: {e}")
            skipped += 1

    return {"sent": sent, "skipped": skipped}


async def _expire_offers(conn: asyncpg.Connection) -> dict:
    """
    Auto-expires offer_sent mappings whose 6h response window (see
    company_portal.final_hire) lapsed without the candidate accepting or
    declining (candidate_portal.respond_to_offer). Moves the mapping to
    'rejected', frees the candidate's lock so they're eligible for other
    clients again, and pings both the candidate and client so neither is
    left assuming the offer is still open.
    """
    import services.msg91_service as msg91
    from services.flow_engine import notify_client_update

    rows = await conn.fetch(
        """
        SELECT ccm.id, ccm.client_id, ccm.candidate_id,
               c.name AS cand_name, c.phone AS phone,
               cl.company_name, cl.job_title
        FROM client_candidate_mappings ccm
        JOIN candidates c  ON c.id  = ccm.candidate_id
        JOIN clients    cl ON cl.id = ccm.client_id
        WHERE ccm.stage = 'offer_sent'::mapping_stage
          AND ccm.offer_expires_at IS NOT NULL
          AND ccm.offer_expires_at < now()
        """
    )

    expired = skipped = 0
    for r in rows:
        try:
            await conn.execute(
                """
                UPDATE client_candidate_mappings
                SET stage = 'rejected'::mapping_stage, rejection_reason = 'offer_expired', updated_at = now()
                WHERE id = $1
                """,
                r["id"],
            )
            await conn.execute(
                """
                UPDATE candidate_locks
                SET unlocked_at = now(), unlock_reason = 'offer_expired'
                WHERE candidate_id = $1 AND client_id = $2 AND unlocked_at IS NULL
                """,
                r["candidate_id"], r["client_id"],
            )

            first_name = (r["cand_name"] or "").split()[0] if r["cand_name"] else "there"
            role = r["job_title"] or "the role"
            if r["phone"]:
                template = WT.CANDIDATE_OFFER_EXPIRED
                if template:
                    components = [{"type": "body", "parameters": [
                        {"type": "text", "text": first_name},
                        {"type": "text", "text": role},
                        {"type": "text", "text": r["company_name"] or ""},
                    ]}]
                    await msg91.send_template(r["phone"], template, "en", components, sender="candidate")
                else:
                    await msg91.send_text(
                        r["phone"],
                        f"Hi {first_name}, your offer window for the {role} position at "
                        f"{r['company_name']} has closed since we didn't hear back in time. "
                        f"Wishing you the best in your job search.",
                        sender="candidate",
                    )

            await notify_client_update(
                r["client_id"],
                f"{r['cand_name'] or 'The candidate'}'s offer for the {role} role expired without a response.",
                "offer",
                conn,
            )
            expired += 1
        except Exception as e:
            print(f"[cron/expire_offers] {r['id']}: {e}")
            skipped += 1

    return {"expired": expired, "skipped": skipped}


# ── Stale lock timeouts ───────────────────────────────────────────────────────
# Thresholds (hours) for auto-unlocking candidates stuck in early funnel stages.
_INTENT_ASK_LOCK_TIMEOUT_H  = 72    # no reply to JD after 3 days → unlock
_INTERESTED_SAFETY_NET_H    = 168   # 7 days hard cap even if reminders haven't fired

async def _expire_stale_locks(conn: asyncpg.Connection, test_mode: bool = False) -> dict:
    """
    Auto-unlock candidates who are stuck in early funnel stages with no progress:

    1. intent_ask + no reply for > 72h  → unlock (they ignored the JD)
    2. interested + form not filled + all 3 reminders sent + >24h since last one → unlock
    3. interested + form not filled + locked > 7 days (safety net) → unlock

    Leaves the mapping stage untouched so the history is preserved.
    Sets unlock_reason = 'funnel_timeout' so we can audit later.
    """
    divisor  = 60 if test_mode else 3600
    timeout  = 2 if test_mode else _INTENT_ASK_LOCK_TIMEOUT_H
    safety   = 5 if test_mode else _INTERESTED_SAFETY_NET_H
    now      = datetime.now(timezone.utc)
    unlocked = 0

    # ── Rule 1: intent_ask with no reply ─────────────────────────────────────
    intent_ask_rows = await conn.fetch(
        """
        SELECT cl.id AS lock_id, cl.candidate_id, cl.client_id, cl.locked_at,
               c.name, c.phone
        FROM candidate_locks cl
        JOIN candidates c ON c.id = cl.candidate_id
        JOIN client_candidate_mappings ccm
             ON ccm.candidate_id = cl.candidate_id AND ccm.client_id = cl.client_id
        WHERE cl.unlocked_at IS NULL
          AND (cl.expires_at IS NULL OR cl.expires_at > now())
          AND ccm.stage = 'intent_ask'::mapping_stage
          AND cl.locked_at < now() - make_interval(hours => $1)
        """,
        timeout,
    )
    for r in intent_ask_rows:
        await conn.execute(
            "UPDATE candidate_locks SET unlocked_at = now(), unlock_reason = 'funnel_timeout' WHERE id = $1",
            r["lock_id"],
        )
        print(f"[stale-locks] intent_ask timeout: {r['name']} ({r['phone']}) locked {((now - r['locked_at']).total_seconds() / divisor):.1f}h ago")
        unlocked += 1

    # ── Rule 2: interested, form not filled, all reminders done + 24h elapsed ─
    interested_reminded_rows = await conn.fetch(
        """
        SELECT cl.id AS lock_id, cl.candidate_id, cl.client_id, cl.locked_at,
               ccm.interested_form_reminder_count,
               c.name, c.phone
        FROM candidate_locks cl
        JOIN candidates c ON c.id = cl.candidate_id
        JOIN client_candidate_mappings ccm
             ON ccm.candidate_id = cl.candidate_id AND ccm.client_id = cl.client_id
        WHERE cl.unlocked_at IS NULL
          AND (cl.expires_at IS NULL OR cl.expires_at > now())
          AND ccm.stage = 'interested'::mapping_stage
          AND COALESCE(c.form_submitted, false) = false
          AND COALESCE(ccm.interested_form_reminder_count, 0) >= 3
          AND ccm.updated_at < now() - '24 hours'::interval
""",
    )
    for r in interested_reminded_rows:
        await conn.execute(
            "UPDATE candidate_locks SET unlocked_at = now(), unlock_reason = 'funnel_timeout' WHERE id = $1",
            r["lock_id"],
        )
        print(f"[stale-locks] interested+3reminders timeout: {r['name']} ({r['phone']})")
        unlocked += 1

    # ── Rule 3: interested safety net (7 days hard cap) ──────────────────────
    safety_rows = await conn.fetch(
        """
        SELECT cl.id AS lock_id, cl.candidate_id, cl.client_id, cl.locked_at,
               c.name, c.phone
        FROM candidate_locks cl
        JOIN candidates c ON c.id = cl.candidate_id
        JOIN client_candidate_mappings ccm
             ON ccm.candidate_id = cl.candidate_id AND ccm.client_id = cl.client_id
        WHERE cl.unlocked_at IS NULL
          AND (cl.expires_at IS NULL OR cl.expires_at > now())
          AND ccm.stage = 'interested'::mapping_stage
          AND COALESCE(c.form_submitted, false) = false
          AND cl.locked_at < now() - make_interval(hours => $1)
        """,
        safety,
    )
    already_handled = {r["lock_id"] for r in interested_reminded_rows}
    for r in safety_rows:
        if r["lock_id"] in already_handled:
            continue
        await conn.execute(
            "UPDATE candidate_locks SET unlocked_at = now(), unlock_reason = 'funnel_timeout' WHERE id = $1",
            r["lock_id"],
        )
        print(f"[stale-locks] 7-day safety net: {r['name']} ({r['phone']})")
        unlocked += 1

    return {"unlocked": unlocked}


@router.post("/candidate-followups")
async def cron_candidate_followups(
    x_cron_secret: str = Header(None, alias="X-Cron-Secret"),
    test: bool = Query(False, description="Use minutes instead of hours for thresholds (testing only)"),
    conn: asyncpg.Connection = Depends(get_conn),
):
    """
    Runs every 30 minutes. All candidate-side follow-ups in one pass:

      slot_sent_candidate  → slot not selected      → hl_cand_slot_r1/2/3  (t+3/8/24h, t+2m each in test)
      slot_sent_candidate  → no slot booked in 48h  → close mapping + unlock candidate + hl_cand_slot_link_expired
      interview_scheduled  → 24h before             → hl_cand_iv_24h
      interview_scheduled  → 8am day-of (IST)       → hl_cand_iv_day
      documents_requested  → docs not submitted     → hl_cand_docs_reminder_v1  (t+24/48h from documents_requested_at)
      interview_done       → 2h after               → hl_cand_post_iv (feedback link)
    """
    expected = os.environ.get("CRON_SECRET", "")
    if expected and x_cron_secret != expected:
        raise HTTPException(401, "Invalid cron secret")

    if not test and not _is_business_hours():
        return {"data": {"skipped": "outside_business_hours"}, "ok": True}

    try:
        interested_form = await _followup_interested_form(conn, test_mode=test)
        # passive_form re-reachout disabled — form is no longer sent on NOT_INTERESTED_ROLE
        passive_form    = {"sent": 0, "skipped": 0}
        slot_ai_call  = await _followup_slot_ai_call(conn, test_mode=test)
        interview     = await _followup_interview_reminders(conn)
        documents     = await _followup_candidate_documents(conn, test_mode=test)
        post_iv       = await _followup_post_interview_candidate(conn)
        dropped       = await _retry_dropped_calls(conn, test_mode=test)
        stale_locks   = await _expire_stale_locks(conn, test_mode=test)
        redact        = await _redact_cvs(conn, batch=5)
        religion      = await _classify_religions(conn, batch=50)
    except Exception as e:
        from services.ops_alert_service import alert_cron_failure
        import traceback
        await alert_cron_failure("candidate-followups", traceback.format_exc())
        raise

    total_sent = interested_form["sent"] + passive_form["sent"] + slot_ai_call["sent"] + interview["sent"] + documents["sent"] + post_iv["sent"] + dropped["sent"]
    print(f"[cron/candidate-followups] total_sent={total_sent} interested_form={interested_form} passive_form={passive_form} slot_ai_call={slot_ai_call} interview={interview} documents={documents} post_iv={post_iv} dropped={dropped} stale_locks={stale_locks} redact={redact} religion={religion}")

    return {
        "data": {
            "interested_form": interested_form,
            "passive_form":   passive_form,
            "slot_ai_call":   slot_ai_call,
            "interview":    interview,
            "documents":    documents,
            "post_iv":      post_iv,
            "dropped":      dropped,
            "stale_locks":  stale_locks,
            "redact_cvs":   redact,
            "total_sent": total_sent,
        },
        "ok": True,
    }


@router.post("/expire-offers")
async def cron_expire_offers(
    x_cron_secret: str = Header(None, alias="X-Cron-Secret"),
    conn: asyncpg.Connection = Depends(get_conn),
):
    """
    Auto-expires offer_sent mappings whose 6h response window has lapsed
    without the candidate accepting/declining.

    Schedule: run every 8 hours (Cloud Scheduler). Coarser than the 6h offer
    window itself is fine — this pass is cleanup + notification, not the
    deadline enforcement; candidate_portal.respond_to_offer already rejects
    a response once offer_expires_at has passed regardless of when this
    cron next runs.
    """
    expected = os.environ.get("CRON_SECRET", "")
    if expected and x_cron_secret != expected:
        raise HTTPException(401, "Invalid cron secret")

    result = await _expire_offers(conn)
    print(f"[cron/expire-offers] expired={result['expired']} skipped={result['skipped']}")
    return {"data": result, "ok": True}


@router.post("/interview-confirm-check")
async def cron_interview_confirm_check(
    x_cron_secret: str = Header(None, alias="X-Cron-Secret"),
    conn: asyncpg.Connection = Depends(get_conn),
):
    """
    Reminds each client with interview(s) scheduled for tomorrow — plain
    reminder + portal link, no decision requested over WhatsApp (see
    _followup_interview_confirm_check). Reschedule/cancel happens in the
    portal's Interviews tab, same as any other day.

    Schedule: run once daily, e.g. 6pm IST (Cloud Scheduler) — needs to fire
    after the day's interviews are locked in but with enough runway for the
    client to reschedule if needed.
    """
    expected = os.environ.get("CRON_SECRET", "")
    if expected and x_cron_secret != expected:
        raise HTTPException(401, "Invalid cron secret")

    result = await _followup_interview_confirm_check(conn)
    print(f"[cron/interview-confirm-check] sent={result['sent']} skipped={result['skipped']}")
    return {"data": result, "ok": True}


@router.post("/retry-dropped-calls")
async def cron_retry_dropped_calls(
    x_cron_secret: str = Header(None, alias="X-Cron-Secret"),
    test: bool = Query(False, description="Use minutes instead of hours; fires regardless of IST hour"),
    conn: asyncpg.Connection = Depends(get_conn),
):
    """
    Re-triggers RinggAI calls for candidates who dropped the AI call midway.

    Schedule: run every 30 minutes (Cloud Scheduler).
    First attempt fires only during 7pm IST. Retries at +3h, +8h, +24h from last attempt.
    Stops after 4 total attempts or when evaluation completes.
    """
    expected = os.environ.get("CRON_SECRET", "")
    if expected and x_cron_secret != expected:
        raise HTTPException(401, "Invalid cron secret")

    result = await _retry_dropped_calls(conn, test_mode=test)
    print(f"[cron/retry-dropped-calls] sent={result['sent']} skipped={result['skipped']}")
    return {"data": result, "ok": True}


@router.get("/candidate-analytics")
async def candidate_analytics(conn: asyncpg.Connection = Depends(get_conn)):
    """Candidate pipeline snapshot — mapping stage breakdown + response rates."""

    stage_rows = await conn.fetch(
        """
        SELECT stage::text, COUNT(*) AS count
        FROM client_candidate_mappings
        GROUP BY stage ORDER BY count DESC
        """
    )
    by_stage = {r["stage"]: r["count"] for r in stage_rows}

    total_jd_sent = await conn.fetchval(
        "SELECT COUNT(*) FROM client_candidate_mappings WHERE wa_sent_at IS NOT NULL"
    )
    total_intent_positive = await conn.fetchval(
        "SELECT COUNT(*) FROM client_candidate_mappings WHERE stage NOT IN ('matched'::mapping_stage, 'wa_sent'::mapping_stage) AND stage IS NOT NULL"
    )
    total_interviews = await conn.fetchval(
        "SELECT COUNT(*) FROM client_candidate_mappings WHERE interview_slot IS NOT NULL"
    )
    total_done = await conn.fetchval(
        "SELECT COUNT(*) FROM client_candidate_mappings WHERE interview_done = true"
    )
    total_placed = await conn.fetchval(
        "SELECT COUNT(*) FROM client_candidate_mappings WHERE stage = 'placed'::mapping_stage"
    )
    intent_rate = round(total_intent_positive / total_jd_sent * 100, 1) if total_jd_sent else 0.0

    return {
        "data": {
            "by_stage":            by_stage,
            "total_jd_sent":       total_jd_sent,
            "total_intent_positive": total_intent_positive,
            "intent_rate_pct":     intent_rate,
            "total_interviews":    total_interviews,
            "interviews_done":     total_done,
            "total_placed":        total_placed,
        },
        "ok": True,
    }


# ── Client enrichment ─────────────────────────────────────────────────────────

_ENRICH_SOURCES = ("naukri", "workindia", "indeed", "apna", "candidate_resume", "sourcing_resume")


async def _gmaps_phones(name: str, location: str | None) -> tuple[list[str], str | None, float | None, float | None]:
    """
    Query Google Places API for a company and return (phones, website, lat, lng).
    Normalises the phone to a 10-digit Indian mobile; returns empty list if not found or landline.
    Never raises.
    """
    import re as _re
    from services.gmaps_service import find_place_id, get_place_details
    from services.enrich_service import normalise_phone
    try:
        place_id = await find_place_id(name, location)
        if not place_id:
            return [], None, None, None
        details = await get_place_details(place_id)
        if not details:
            return [], None, None, None
        raw_phone = details.get("phone") or ""
        phone = normalise_phone(raw_phone)
        phones = [phone] if phone and phone[0] in "6789" else []
        return phones, details.get("website"), details.get("lat"), details.get("lng")
    except Exception as e:
        print(f"[enrich/gmaps] {name}: {e}")
        return [], None, None, None


async def _enrich_one(client: dict, sem: asyncio.Semaphore) -> dict:
    """
    Classify + enrich a single client using both Google Places API and AI web search in parallel.
    - Name filter: obvious recruiters exit instantly (no API call)
    - Google Places: finds phone from Google Business Profile directly
    - AI web search: classifies recruiter/company + finds additional phones
    - Results merged and deduped
    Returns {"id", "status", "phones", "website", "lat", "lng", "is_recruiter", "description"}
    status: "recruiter" | "enriched" | "not_found" | "error"
    """
    from services import enrich_service

    cid       = str(client["id"])
    name      = client["company_name"] or ""
    location  = client["job_location"] or client.get("location") or None
    job_title = client.get("job_title")
    website   = client.get("company_website")

    # Free name-based check — no API call
    if enrich_service.is_obvious_recruiter(name):
        print(f"[enrich] {cid} ({name}) → recruiter (name filter)")
        return {
            "id": cid, "status": "recruiter",
            "phones": [], "website": website, "lat": None, "lng": None,
            "is_recruiter": True,
            "description": "Recruitment consultancy — excluded from pipeline.",
        }

    # Run Google Places and AI web search in parallel
    async with sem:
        gmaps_task = asyncio.create_task(_gmaps_phones(name, location))
        try:
            ai_result = await enrich_service.classify_and_find_phones(name, location, job_title)
        except Exception as e:
            print(f"[enrich] {cid} ({name}) AI error: {e}")
            asyncio.create_task(
                __import__("services.ops_alert_service", fromlist=["send_alert"]).send_alert(
                    title=f"Enrich AI Error: {name}",
                    detail=f"{type(e).__name__}: {e}",
                    severity="ERROR",
                    context={"client_id": cid, "company": name},
                )
            )
            ai_result = {"is_recruiter": False, "client_type": "company", "description": "", "phones": []}

        gmaps_phones, gmaps_website, gmaps_lat, gmaps_lng = await gmaps_task

    if ai_result["is_recruiter"]:
        print(f"[enrich] {cid} ({name}) → recruiter (AI)")
        return {
            "id": cid, "status": "recruiter",
            "phones": [], "website": website, "lat": None, "lng": None,
            "is_recruiter": True,
            "description": ai_result.get("description") or "Recruitment consultancy — excluded from pipeline.",
        }

    # Merge phones from both sources, deduped, gmaps first (higher confidence)
    seen: set[str] = set()
    phones: list[str] = []
    for p in gmaps_phones + ai_result.get("phones", []):
        if p and p not in seen:
            seen.add(p)
            phones.append(p)

    status = "enriched" if phones else "not_found"
    print(f"[enrich] {cid} ({name}) → {status} | phones={phones} (gmaps={gmaps_phones} ai={ai_result.get('phones', [])})")
    return {
        "id": cid, "status": status,
        "phones": phones,
        "website": website or gmaps_website,
        "lat": gmaps_lat,
        "lng": gmaps_lng,
        "is_recruiter": False,
        "description": ai_result.get("description", ""),
    }


async def _apply_enrich_results(results: list[dict], pool) -> tuple[int, int, int, int]:
    """
    Write combined classify + enrich results back to clients.
    - "enriched":  phone found, is_recruiter=false → poc_phone set, stage→scraped
                   (unless the phone already belongs to another client — see "duplicate" below)
    - "not_found": no phone, is_recruiter=false → enrich_status=not_found, classification saved
    - "recruiter": is_recruiter=true → is_dnd=true, enrich_status=not_found
    - "error":     API failure → leave enrich_status=pending so it retries next run
    - duplicate (found within "enriched"): the enriched phone already belongs to another
      client → stage=disqualified, is_dnd=true, never retried. Avoids reaching out to a
      contact we already have an active record for (e.g. a new job post for an existing client).
    Returns (enriched_count, not_found_count, recruiter_count, duplicate_count).
    """
    enriched_count = not_found_count = recruiter_count = duplicate_count = 0

    async with pool.acquire() as conn:
        for r in results:
            cid         = uuid.UUID(r["id"])
            status      = r["status"]
            phones      = r["phones"]
            website     = r["website"]
            lat         = r["lat"]
            lng         = r["lng"]
            description = r.get("description") or ""

            if status == "recruiter":
                await conn.execute(
                    """
                    UPDATE clients SET
                        company_description = $1,
                        is_recruiter        = true,
                        is_dnd              = true,
                        enrich_status       = 'not_found',
                        updated_at          = now()
                    WHERE id = $2
                    """,
                    description or "Recruitment consultancy — excluded from pipeline.",
                    cid,
                )
                recruiter_count += 1

            elif status == "enriched":
                dup = await _find_duplicate_client(conn, phones, exclude_id=cid)
                if dup:
                    # Still disqualify/DND (we don't want to message a number that
                    # belongs to another client) but persist the phone we found —
                    # previously it was discarded entirely, leaving staff with no
                    # way to see or verify the number behind the duplicate match.
                    best_phone = phones[0]
                    await conn.execute(
                        """
                        UPDATE clients SET
                            stage               = 'disqualified'::client_stage,
                            is_dnd              = true,
                            enrich_status       = 'not_found',
                            poc_phone           = COALESCE(poc_phone, $1),
                            phone_numbers       = (
                                SELECT COALESCE(jsonb_agg(DISTINCT x.val), '[]'::jsonb)
                                FROM (
                                    SELECT jsonb_array_elements_text(
                                        COALESCE(phone_numbers, '[]'::jsonb)
                                    ) AS val
                                    UNION ALL
                                    SELECT unnest($2::text[]) AS val
                                ) x
                                WHERE x.val IS NOT NULL AND x.val <> ''
                            ),
                            company_description = COALESCE(NULLIF(company_description, ''), $3),
                            updated_at          = now()
                        WHERE id = $4
                        """,
                        best_phone, phones,
                        f"Duplicate — phone already belongs to client {dup['id']} "
                        f"({dup['company_name']!r}, stage={dup['stage']}).",
                        cid,
                    )
                    duplicate_count += 1
                    continue

                best_phone = phones[0]
                await conn.execute(
                    """
                    UPDATE clients SET
                        poc_phone           = COALESCE(poc_phone, $1),
                        phone_numbers       = (
                            SELECT COALESCE(jsonb_agg(DISTINCT x.val), '[]'::jsonb)
                            FROM (
                                SELECT jsonb_array_elements_text(
                                    COALESCE(phone_numbers, '[]'::jsonb)
                                ) AS val
                                UNION ALL
                                SELECT unnest($2::text[]) AS val
                            ) x
                            WHERE x.val IS NOT NULL AND x.val <> ''
                        ),
                        company_description = COALESCE(NULLIF(company_description, ''), $3),
                        is_recruiter        = false,
                        company_website     = COALESCE(company_website, $4),
                        location_lat        = COALESCE(location_lat, $5),
                        location_lng        = COALESCE(location_lng, $6),
                        enrich_status       = 'enriched',
                        stage               = 'scraped'::client_stage,
                        updated_at          = now()
                    WHERE id = $7
                    """,
                    best_phone, phones, description, website, lat, lng, cid,
                )
                enriched_count += 1

            elif status == "not_found":
                await conn.execute(
                    """
                    UPDATE clients SET
                        company_description = COALESCE(NULLIF(company_description, ''), $1),
                        is_recruiter        = false,
                        company_website     = COALESCE(company_website, $2),
                        location_lat        = COALESCE(location_lat, $3),
                        location_lng        = COALESCE(location_lng, $4),
                        enrich_status       = 'not_found',
                        updated_at          = now()
                    WHERE id = $5
                    """,
                    description, website, lat, lng, cid,
                )
                not_found_count += 1
            # status == "error": leave enrich_status = 'pending' — retries next run

    return enriched_count, not_found_count, recruiter_count, duplicate_count


@router.post("/enrich-clients")
async def cron_enrich_clients(
    batch: int = Query(50, ge=1),
    x_cron_secret: str = Header(None, alias="X-Cron-Secret"),
    conn: asyncpg.Connection = Depends(get_conn),
):
    """
    Enriches scraped client leads with phone numbers via:
      1. Google Maps (findplacefromtext → place details)
      2. Website AI agent (httpx fetch → gpt-5-mini extraction)
    Both sources always run. All found numbers stored in phone_numbers[].
    Clients with any phone found → stage = scraped (picked up by reachout cron).
    Clients with no phone found → enrich_status = not_found (never retried).
    """
    expected = os.environ.get("CRON_SECRET", "")
    if expected and x_cron_secret != expected:
        raise HTTPException(401, "Invalid cron secret")

    # Fetch pending clients — includes unclassified (is_recruiter IS NULL) so the
    # combined prompt can classify + enrich them in a single web-search call.
    clients = await conn.fetch(
        """
        SELECT id, company_name, job_location, location, job_title, company_website
        FROM clients
        WHERE source = ANY($1::text[])
          AND (enrich_status IS NULL OR enrich_status = 'pending')
          AND (poc_phone IS NULL OR poc_phone = '')
          AND COALESCE(is_dnd, false) = false
        ORDER BY created_at DESC
        LIMIT $2
        """,
        list(_ENRICH_SOURCES),
        batch,
    )

    if not clients:
        return {"data": {"total": 0, "enriched": 0, "not_found": 0, "recruiters": 0}, "ok": True}

    sem = asyncio.Semaphore(3)  # GPT-5 series: 200k tokens/min — 3 concurrent keeps us under limit
    try:
        results = await asyncio.gather(*[_enrich_one(dict(c), sem) for c in clients])
        enriched, not_found, recruiters, duplicates = await _apply_enrich_results(list(results), get_pool())
    except Exception as e:
        from services.ops_alert_service import alert_cron_failure
        await alert_cron_failure("enrich-clients", f"{type(e).__name__}: {e}")
        raise

    print(f"[cron/enrich-clients] total={len(clients)} enriched={enriched} not_found={not_found} "
          f"recruiters={recruiters} duplicates={duplicates}")
    return {
        "data": {
            "total":      len(clients),
            "enriched":   enriched,
            "not_found":  not_found,
            "recruiters": recruiters,
            "duplicates": duplicates,
        },
        "ok": True,
    }


# ── Reachout queue (admin view + manual trigger) ──────────────────────────────

@router.post("/reachout-queue/{client_id}/send-next")
async def send_next_reachout(
    client_id: str,
    conn: asyncpg.Connection = Depends(get_conn),
):
    """Manually trigger the next outreach step for a specific client."""
    import uuid as _uuid
    from services import gcs_service

    try:
        cid = _uuid.UUID(client_id)
    except ValueError:
        raise HTTPException(400, "Invalid client_id")

    image_urls = await gcs_service.list_reachout_images()
    if not image_urls:
        raise HTTPException(500, "No reachout images found in GCS bucket")

    client = await conn.fetchrow(
        """
        SELECT * FROM clients WHERE id = $1
          AND stage IN ('scraped'::client_stage, 'reachout_sent'::client_stage)
          AND outreach_step < $2
          AND is_dnd = false
        """,
        cid, len(image_urls),
    )
    if not client:
        raise HTTPException(404, "Client not found or not eligible for outreach")

    import json as _json
    raw_numbers = client["phone_numbers"]
    if raw_numbers:
        all_phones = list(dict.fromkeys(
            p for p in (raw_numbers if isinstance(raw_numbers, list) else _json.loads(raw_numbers))
            if p
        ))
    else:
        all_phones = [client["poc_phone"]] if client["poc_phone"] else []
    if not all_phones:
        raise HTTPException(400, "Client has no phone numbers")

    step_idx = int(client["outreach_step"] or 0)
    image_url = image_urls[step_idx]
    sent = await _send_client_reachout_step(client, image_url, conn, all_phones)
    if not sent:
        raise HTTPException(400, "Send failed — check phone number and MSG91 config")

    return {"data": {"sent": 1, "step": int(client["outreach_step"] or 0) + 1}, "ok": True}


class BulkReachoutRequest(BaseModel):
    client_ids: list[str]


@router.post("/reachout-queue/bulk-send")
async def bulk_send_reachout(
    body: BulkReachoutRequest,
    conn: asyncpg.Connection = Depends(get_conn),
):
    """Bulk trigger the next outreach step for a list of clients."""
    from services import gcs_service

    image_urls = await gcs_service.list_reachout_images()
    if not image_urls:
        raise HTTPException(500, "No reachout images found in GCS bucket")

    results = {"sent": 0, "failed": 0, "skipped": 0}
    pool = get_pool()

    async def _send_one(client_id_str: str):
        try:
            cid = uuid.UUID(client_id_str)
        except ValueError:
            results["skipped"] += 1
            return

        async with pool.acquire() as wconn:
            client = await wconn.fetchrow(
                """
                SELECT * FROM clients WHERE id = $1
                  AND stage IN ('scraped'::client_stage, 'reachout_sent'::client_stage)
                  AND outreach_step < $2
                  AND is_dnd = false
                """,
                cid, len(image_urls),
            )
            if not client:
                results["skipped"] += 1
                return

            import json as _json
            raw_numbers = client["phone_numbers"]
            if raw_numbers:
                all_phones = list(dict.fromkeys(
                    p for p in (raw_numbers if isinstance(raw_numbers, list) else _json.loads(raw_numbers))
                    if p
                ))
            else:
                all_phones = [client["poc_phone"]] if client["poc_phone"] else []
            if not all_phones:
                results["skipped"] += 1
                return

            step_idx = int(client["outreach_step"] or 0)
            ok = await _send_client_reachout_step(client, image_urls[step_idx], wconn, all_phones)
            if ok:
                results["sent"] += 1
            else:
                results["failed"] += 1

    await asyncio.gather(*[_send_one(cid) for cid in body.client_ids])
    return {"data": results, "ok": True}


@router.post("/flush-jd-queue")
async def flush_jd_queue(
    x_cron_secret: str = Header(None, alias="X-Cron-Secret"),
    conn: asyncpg.Connection = Depends(get_conn),
):
    """
    Pick up pending JD batches whose scheduled_at <= now and send them.
    Run every 5 minutes via Cloud Scheduler.
    """
    if x_cron_secret != os.environ.get("CRON_SECRET"):
        raise HTTPException(403, "Forbidden")

    import json as _json
    import services.msg91_service as msg91

    # Claim due batches atomically — skip locked rows to avoid double-send
    due = await conn.fetch(
        """
        UPDATE jd_send_queue SET status = 'sending'
        WHERE id IN (
            SELECT id FROM jd_send_queue
            WHERE status = 'pending' AND scheduled_at <= now()
            ORDER BY scheduled_at
            LIMIT 10
            FOR UPDATE SKIP LOCKED
        )
        RETURNING *
        """
    )

    if not due:
        return {"data": {"processed": 0}, "ok": True}

    sent_total = 0
    failed_total = 0

    # JD card image is generated at match time and stored on clients.jd_card_url.
    # If missing, generate it now, persist it, and proceed.
    from services.jd_card_service import generate_jd_card
    _client_cache: dict[str, asyncpg.Record | None] = {}

    async def _get_client(cid) -> asyncpg.Record | None:
        key = str(cid)
        if key not in _client_cache:
            _client_cache[key] = await conn.fetchrow("SELECT * FROM clients WHERE id = $1", cid)
        return _client_cache[key]

    async def _ensure_jd_image(cid) -> str | None:
        client_row = await _get_client(cid)
        if not client_row:
            return None
        url = client_row["jd_card_url"]
        if url:
            return url
        print(f"[flush-jd-queue] jd_card_url missing for {cid} — generating now")
        try:
            url = await generate_jd_card(dict(client_row))
            await conn.execute("UPDATE clients SET jd_card_url = $1 WHERE id = $2", url, cid)
            # refresh cache
            _client_cache[str(cid)] = await conn.fetchrow("SELECT * FROM clients WHERE id = $1", cid)
            return url
        except Exception as _e:
            print(f"[flush-jd-queue] JD card generation failed for {cid}: {_e}")
            return None

    for row in due:
        mapping_rows = row["mapping_rows"] if isinstance(row["mapping_rows"], (list, dict)) else _json.loads(row["mapping_rows"])
        components   = row["components"]   if isinstance(row["components"],   (list, dict)) else _json.loads(row["components"])
        queue_id     = row["id"]
        client_id    = row["client_id"]

        try:
            # Skip candidates who already received the JD or are no longer active (DND)
            eligible = []
            for m in mapping_rows:
                skip = await conn.fetchval(
                    """
                    SELECT 1 FROM client_candidate_mappings ccm
                    JOIN candidates c ON c.id = ccm.candidate_id
                    WHERE ccm.id = $1
                      AND ccm.wa_sent_at IS NULL
                      AND c.is_active = true
                      AND (c.dnd_until IS NULL OR c.dnd_until <= now())
                    """,
                    uuid.UUID(m["mapping_id"]),
                )
                if skip:
                    eligible.append(m)
                else:
                    print(f"[flush-jd-queue] Skipping mapping {m['mapping_id']} — already sent or candidate inactive/DND")

            if not eligible:
                await conn.execute("UPDATE jd_send_queue SET status='sent', sent_at=now() WHERE id=$1", queue_id)
                continue

            # FF_TEMPLATE (form-already-filled candidates) has no header image —
            # only block/retry this batch on a missing JD card if at least one
            # eligible candidate actually resolves to an image-based template.
            needs_image = any(
                m.get("template_name", msg91.JD_V3_TEMPLATE) != msg91.FF_TEMPLATE for m in eligible
            )
            jd_image_url = await _ensure_jd_image(client_id) if needs_image else None
            if needs_image and not jd_image_url:
                print(f"[flush-jd-queue] Skipping queue {queue_id} — JD card image unavailable, will retry next cycle")
                failed_total += 1
                continue
            client_row = await _get_client(client_id)
            maps_url = _client_maps_url(dict(client_row)) if client_row else ""
            await msg91.send_bulk_intent(
                [{"phone": m["phone"], "mapping_id": m["mapping_id"], "name": m.get("name", ""),
                  "intro": m.get("intro", ""), "maps_url": maps_url,
                  "area": m.get("area", ""), "salary": m.get("salary", ""),
                  "company_name": m.get("company_name", ""),
                  "role": m.get("role", ""),
                  "template_name": m.get("template_name", msg91.JD_V3_TEMPLATE)} for m in eligible],
                components,
                image_url=jd_image_url,
            )
            mids = [uuid.UUID(m["mapping_id"]) for m in eligible]
            await conn.execute(
                """
                UPDATE client_candidate_mappings
                SET stage = 'intent_ask'::mapping_stage, wa_sent_at = now(), updated_at = now()
                WHERE id = ANY($1::uuid[])
                """,
                mids,
            )
            mapping_rows = eligible  # update reference for the whatsapp_messages inserts below
            for m in mapping_rows:
                await conn.execute(
                    """
                    INSERT INTO whatsapp_messages
                        (candidate_id, mapping_id, message_type, direction, phone, status, sent_at, used_template_name)
                    VALUES ($1, $2, 'candidate_intent_ask', 'outbound', $3, 'sent', now(), $4)
                    ON CONFLICT DO NOTHING
                    """,
                    uuid.UUID(m["candidate_id"]), uuid.UUID(m["mapping_id"]), m["phone"],
                    m.get("template_name", _CANDIDATE_INTENT_TEMPLATE),
                )
                await conn.execute(
                    "SELECT record_candidate_jd_sent($1, $2)",
                    uuid.UUID(m["candidate_id"]), uuid.UUID(m["mapping_id"]),
                )
            await conn.execute(
                "UPDATE jd_send_queue SET status='sent', sent_at=now() WHERE id=$1",
                queue_id,
            )
            print(f"[flush-jd-queue] Sent batch {row['batch_number']} ({len(mapping_rows)} candidates) for client {client_id}")
            sent_total += len(mapping_rows)
        except Exception as e:
            await conn.execute(
                "UPDATE jd_send_queue SET status='failed', error_msg=$1 WHERE id=$2",
                str(e), queue_id,
            )
            print(f"[flush-jd-queue] Batch {row['batch_number']} failed for client {client_id}: {e}")
            failed_total += len(mapping_rows)

    return {"data": {"processed": len(due), "sent": sent_total, "failed": failed_total}, "ok": True}


# ── CV phone-number redaction ─────────────────────────────────────────────────

_NON_RETRYABLE_ERRORS = (
    "download failed",
    "is no PDF",
    "tesseract is not installed",
    "cannot find object in xref",  # corrupted PDF
    "Failed to open stream",       # corrupted / unreadable PDF
    "upload failed",               # GCS auth issues treated as permanent for now
)


async def _redact_cvs(conn: asyncpg.Connection, batch: int = 5) -> dict:
    """Process up to `batch` unredacted CVs. Called by candidate-followups and the standalone endpoint."""
    from services.cv_redact_service import redact_candidate_cv

    rows = await conn.fetch(
        """
        SELECT id, cv_url FROM candidates
        WHERE cv_url IS NOT NULL
          AND COALESCE(cv_phone_redacted, false) = false
        ORDER BY created_at DESC
        LIMIT $1
        """,
        batch,
    )

    if not rows:
        return {"processed": 0, "redacted": 0, "skipped": 0}

    redacted = 0
    skipped = 0
    for row in rows:
        result = await redact_candidate_cv(row["cv_url"])
        if result["ok"]:
            await conn.execute(
                "UPDATE candidates SET cv_phone_redacted = true WHERE id = $1",
                row["id"],
            )
            print(f"[redact-cvs] {row['id']}: {result['redactions']} phone(s) redacted")
            redacted += 1
        else:
            err = result["error"] or ""
            # Mark as done for errors that will never resolve (broken URL, scanned PDF
            # without tesseract, corrupted file) so we don't retry them on every run
            if any(marker in err for marker in _NON_RETRYABLE_ERRORS):
                await conn.execute(
                    "UPDATE candidates SET cv_phone_redacted = true WHERE id = $1",
                    row["id"],
                )
            print(f"[redact-cvs] {row['id']}: SKIP — {err}")
            skipped += 1

    return {"processed": len(rows), "redacted": redacted, "skipped": skipped}


@router.post("/redact-cvs")
async def cron_redact_cvs(
    batch: int = 10,
    conn: asyncpg.Connection = Depends(get_conn),
):
    """
    Process up to `batch` candidates whose CV has not yet been phone-redacted.
    Downloads PDF from GCS, removes phone numbers, re-uploads, marks done.
    Also called automatically from /candidate-followups every 30 minutes (batch=5).
    """
    result = await _redact_cvs(conn, batch=batch)
    return {"data": result, "ok": True}


async def _classify_religions(conn: asyncpg.Connection, batch: int = 50) -> dict:
    """Classify religion for candidates with religion IS NULL using OpenAI."""
    from services.religion_classifier_service import classify_names

    rows = await conn.fetch(
        """
        SELECT id, name FROM candidates
        WHERE (religion IS NULL OR religion = '')
        ORDER BY created_at DESC
        LIMIT $1
        """,
        batch,
    )

    if not rows:
        return {"processed": 0, "classified": 0}

    names = [r["name"] for r in rows]
    id_by_name = {r["name"]: r["id"] for r in rows}

    try:
        results = await classify_names(names)
    except Exception as e:
        print(f"[classify-religions] OpenAI error: {e}")
        return {"processed": len(rows), "classified": 0, "error": str(e)}

    classified = 0
    for name, label in results.items():
        cid = id_by_name.get(name)
        if cid and label in ("M", "N"):
            # U = ambiguous → leave NULL so next cron run retries
            await conn.execute(
                "UPDATE candidates SET religion = $1 WHERE id = $2",
                label, cid,
            )
            classified += 1

    print(f"[classify-religions] processed={len(rows)} classified={classified}")
    return {"processed": len(rows), "classified": classified}


@router.post("/classify-religions")
async def cron_classify_religions(
    batch: int = 50,
    x_cron_secret: str = Header(None, alias="X-Cron-Secret"),
    conn: asyncpg.Connection = Depends(get_conn),
):
    """Classify religion for up to `batch` untagged candidates. Also called from /candidate-followups."""
    expected = os.environ.get("CRON_SECRET", "")
    if expected and x_cron_secret != expected:
        raise HTTPException(401, "Invalid cron secret")
    result = await _classify_religions(conn, batch=batch)
    return {"data": result, "ok": True}
