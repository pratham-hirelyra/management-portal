import os
import uuid
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
import asyncpg
from database import get_conn
from pydantic import BaseModel
from typing import List
import constants.whatsapp_templates as WT

_IST = ZoneInfo("Asia/Kolkata")

def _parse_slot_label_to_dt(label: str) -> datetime | None:
    """Parse slot label like 'Friday 29 May, 2026 · 01:00 pm – 02:00 pm' to IST datetime."""
    try:
        m = re.search(r'(\d{1,2})\s+([A-Za-z]+),?\s+(\d{4})\s*[·•]\s*(\d{1,2}:\d{2}\s*[aApP][mM])', label)
        if m:
            day, month, year, time_str = m.groups()
            dt = datetime.strptime(f"{day} {month} {year} {time_str.strip()}", "%d %B %Y %I:%M %p")
            return dt.replace(tzinfo=_IST)
    except Exception:
        pass
    return None

router = APIRouter(prefix="/schedule", tags=["schedule"])


class SlotBookingRequest(BaseModel):
    client_name: str
    slots: List[str]  # free-form labels entered by client


@router.get("/{client_id}")
async def get_schedule(client_id: str, conn: asyncpg.Connection = Depends(get_conn)):
    try:
        cid = uuid.UUID(client_id)
    except ValueError:
        raise HTTPException(400, "Invalid client ID")

    client = await conn.fetchrow(
        "SELECT id, company_name, poc_name, available_slots FROM clients WHERE id = $1",
        cid,
    )
    if not client:
        raise HTTPException(404, "Schedule not found")

    slots_booked = bool(client["available_slots"])

    return {
        "data": {
            "client_id": str(client["id"]),
            "company_name": client["company_name"],
            "poc_name": client["poc_name"],
            "slots_booked": slots_booked,
        },
        "ok": True,
    }


@router.post("/{client_id}/book")
async def book_slots(
    client_id: str, body: SlotBookingRequest, conn: asyncpg.Connection = Depends(get_conn)
):
    try:
        cid = uuid.UUID(client_id)
    except ValueError:
        raise HTTPException(400, "Invalid client ID")

    if not body.slots:
        raise HTTPException(400, "No slots provided")
    if len(body.slots) < 4:
        raise HTTPException(400, "Please provide at least 4 time slots")

    client = await conn.fetchrow(
        "SELECT id, poc_phone, available_slots FROM clients WHERE id = $1", cid
    )
    if not client:
        raise HTTPException(404, "Client not found")

    if client["available_slots"]:
        raise HTTPException(409, "Slots have already been submitted for this link.")

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
        bookings,
        cid,
    )

    # Send confirmation to client on WhatsApp
    if client["poc_phone"]:
        try:
            import services.msg91_service as msg91
            slot_lines = "\n".join(f"  • {slot}" for slot in body.slots)
            template = WT.CLIENT_SLOTS_RECEIVED
            if template:
                components = [{"type": "body", "parameters": [
                    {"type": "text", "text": slot_lines},
                ]}]
                await msg91.send_template(client["poc_phone"], template, "en", components, sender="client")
            else:
                msg = (
                    f"Thank you for sharing your availability! ✅\n\n"
                    f"Your preferred times:\n{slot_lines}\n\n"
                    f"We will review the available candidates and reach out to you shortly with their profile details."
                )
                await msg91.send_text(client["poc_phone"], msg, sender="client")
        except Exception as e:
            print(f"[schedule/book] client confirmation WA failed for {cid}: {e}")

    # Fan out: move interested candidates to approval queue + send JD to new candidates
    try:
        from routers.matching import on_slots_submitted
        await on_slots_submitted(cid, conn)
    except Exception as e:
        print(f"[schedule/book] on_slots_submitted failed for {cid}: {e}")

    return {"data": {"booked": len(bookings)}, "ok": True}


# ── Candidate schedule ─────────────────────────────────────────────────────────

class CandidateSlotConfirmRequest(BaseModel):
    slot_label: str  # one of the client's available slot labels


@router.get("/candidate/{mapping_id}")
async def get_candidate_schedule(mapping_id: str, conn: asyncpg.Connection = Depends(get_conn)):
    try:
        mid = uuid.UUID(mapping_id)
    except ValueError:
        raise HTTPException(400, "Invalid mapping ID")

    row = await conn.fetchrow(
        """
        SELECT m.id, m.stage, m.client_id, m.candidate_id,
               c.company_name, c.job_title, c.job_location, c.location_lat, c.location_lng,
               c.industry, c.job_type, c.num_employees, c.female_employee_pct,
               c.min_salary, c.max_salary, c.job_timings,
               c.age_requirements, c.tools_requirements,
               c.other_details AS client_other_details,
               c.available_slots,
               ca.name AS candidate_name,
               r.name AS rm_name, r.phone AS rm_phone
        FROM client_candidate_mappings m
        JOIN clients c ON c.id = m.client_id
        JOIN candidates ca ON ca.id = m.candidate_id
        LEFT JOIN rms r ON r.id = c.rm_id
        WHERE m.id = $1
        """,
        mid,
    )
    if not row:
        raise HTTPException(404, "Not found")

    # Check if this candidate is still locked by another client (projected-free scenario)
    other_lock = await conn.fetchrow(
        """
        SELECT expires_at FROM candidate_locks
        WHERE candidate_id = $1
          AND client_id   != $2
          AND unlocked_at  IS NULL
          AND expires_at   IS NOT NULL
          AND expires_at   > now()
        ORDER BY expires_at DESC
        LIMIT 1
        """,
        row["candidate_id"], row["client_id"],
    )
    earliest_allowed: datetime | None = other_lock["expires_at"] if other_lock else None

    slots = list(row["available_slots"] or [])
    slot_labels = [s["label"] for s in slots if s.get("label")]

    # Count bookings per slot for this client (excluding this mapping itself)
    booking_rows = await conn.fetch(
        """
        SELECT (available_slots->0->>'label') AS slot_label, COUNT(*) AS cnt
        FROM client_candidate_mappings
        WHERE client_id = $1
          AND stage IN ('slot_booked', 'interview_scheduled', 'interview_done', 'placed')
          AND (available_slots->0->>'confirmed_by') = 'candidate'
          AND id != $2
        GROUP BY slot_label
        """,
        row["client_id"], mid,
    )
    booked_counts = {r["slot_label"]: int(r["cnt"]) for r in booking_rows}

    # Build a label→iso lookup so we can filter by lock expiry
    slot_iso_map = {s["label"]: s.get("iso") for s in slots if s.get("label")}

    def _slot_allowed(label: str) -> bool:
        if not earliest_allowed:
            return True
        iso = slot_iso_map.get(label)
        if not iso:
            return True  # no ISO on slot — don't block
        try:
            slot_dt = datetime.fromisoformat(iso)
            # make timezone-aware if naive
            if slot_dt.tzinfo is None:
                from datetime import timezone as _tz
                slot_dt = slot_dt.replace(tzinfo=_tz.utc)
            return slot_dt >= earliest_allowed
        except Exception:
            return True

    # Only show slots with capacity AND after the candidate's lock expiry
    open_slots = [
        label for label in slot_labels
        if booked_counts.get(label, 0) < 2 and _slot_allowed(label)
    ]

    return {
        "data": {
            "mapping_id": str(row["id"]),
            "stage": row["stage"],
            "company_name": row["company_name"],
            "job_title": row["job_title"],
            "job_location": row["job_location"],
            "location_lat": float(row["location_lat"]) if row["location_lat"] else None,
            "location_lng": float(row["location_lng"]) if row["location_lng"] else None,
            "industry": row["industry"],
            "job_type": row["job_type"],
            "num_employees": row["num_employees"],
            "female_employee_pct": float(row["female_employee_pct"]) if row["female_employee_pct"] is not None else None,
            "min_salary": row["min_salary"],
            "max_salary": row["max_salary"],
            "job_timings": row["job_timings"],
            "age_requirements": row["age_requirements"],
            "tools_requirements": row["tools_requirements"],
            "other_details": row["client_other_details"],
            "candidate_name": row["candidate_name"],
            "rm_name": row["rm_name"],
            "rm_phone": row["rm_phone"],
            "available_slots": open_slots,
        },
        "ok": True,
    }


_GCS_URL_RE = re.compile(
    r'https://storage(?:\.cloud)?\.google(?:apis)?\.com/([^/?#]+)/(.+)'
)


def _gcs_bucket_blob(url: str):
    """Return (bucket, blob_path) for any GCS URL variant, or None."""
    m = _GCS_URL_RE.match(url)
    return (m.group(1), m.group(2)) if m else None


def _gcs_aware_fetcher(url: str) -> dict:
    """
    WeasyPrint URL fetcher: uses GCS client (service-account auth) for any
    storage.googleapis.com or storage.cloud.google.com URL, falls back to
    the default fetcher for everything else (fonts, CDN, etc.).
    """
    import urllib.parse
    from weasyprint import default_url_fetcher
    from google.cloud import storage as _gcs

    gcs = _gcs_bucket_blob(url)
    if gcs:
        try:
            # URL-decode the blob path so GCS client doesn't double-encode it
            blob_path = urllib.parse.unquote(gcs[1])
            blob = _gcs.Client().bucket(gcs[0]).blob(blob_path)
            data = blob.download_as_bytes()
            mime = blob.content_type or "application/octet-stream"
            return {"content": data, "mime_type": mime, "encoding": None}
        except Exception as e:
            print(f"[gcs_fetcher] failed for {url}: {e}")

    return default_url_fetcher(url)


def _html_url_to_pdf_bytes(html_url: str) -> bytes | None:
    """Fetch an HTML file from GCS (either URL format) and convert to PDF."""
    from weasyprint import HTML as _WH
    from google.cloud import storage as _gcs

    gcs = _gcs_bucket_blob(html_url)
    if gcs:
        try:
            html_bytes = _gcs.Client().bucket(gcs[0]).blob(gcs[1]).download_as_bytes()
            html_str = html_bytes.decode("utf-8", errors="replace")
        except Exception as e:
            print(f"[html_to_pdf] GCS read failed for {html_url}: {e}")
            return None
    else:
        import httpx
        try:
            resp = httpx.get(html_url, timeout=30.0, follow_redirects=True)
            if not resp.is_success:
                print(f"[html_to_pdf] HTTP {resp.status_code} for {html_url}")
                return None
            html_str = resp.text
        except Exception as e:
            print(f"[html_to_pdf] fetch failed for {html_url}: {e}")
            return None

    try:
        return _WH(
            string=html_str,
            base_url=html_url,
            url_fetcher=_gcs_aware_fetcher,
        ).write_pdf()
    except Exception as e:
        print(f"[html_to_pdf] WeasyPrint failed for {html_url}: {e}")
        return None


async def _html_url_to_gcs_pdf(html_url: str, blob_path: str) -> str | None:
    """Convert an HTML URL to PDF and upload to GCS. Returns public URL or None."""
    import asyncio
    from services.agreement_service import upload_to_gcp
    loop = asyncio.get_running_loop()
    try:
        print(f"[html_to_gcs_pdf] starting PDF generation for {html_url}")
        pdf_bytes = await loop.run_in_executor(None, _html_url_to_pdf_bytes, html_url)
        if not pdf_bytes:
            print(f"[html_to_gcs_pdf] no PDF bytes returned for {html_url}")
            return None
        print(f"[html_to_gcs_pdf] PDF generated ({len(pdf_bytes)} bytes), uploading...")
        url = await loop.run_in_executor(None, upload_to_gcp, pdf_bytes, "", blob_path)
        print(f"[html_to_gcs_pdf] uploaded: {url}")
        return url
    except Exception as e:
        print(f"[html_to_gcs_pdf] failed for {html_url}: {e}")
        return None


async def _send_interview_confirmation_to_client(
    mapping_id: str,
    slot_label: str,
) -> None:
    """Send a WhatsApp template to the client when a candidate confirms their slot."""
    from database import get_pool
    import services.msg91_service as msg91

    template = WT.CLIENT_SLOT_CONFIRMED
    round2_template = WT.CLIENT_SLOT_CONFIRMED_ROUND2
    # Points at the OTP-based client-portal app, not the legacy /client/{feedback_token}
    # page in this frontend (same fix as the onboarding-success flow).
    portal_url = os.environ.get("CLIENT_PORTAL_URL", "https://employer.justaccountants.in").rstrip("/")

    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT c.id AS client_id, c.poc_name, c.poc_phone,
                   ca.name AS candidate_name, m.interview_round
            FROM client_candidate_mappings m
            JOIN clients    c  ON c.id  = m.client_id
            JOIN candidates ca ON ca.id = m.candidate_id
            WHERE m.id = $1
            """,
            uuid.UUID(mapping_id),
        )
        if not row or not row["poc_phone"]:
            return

        poc_name       = row["poc_name"] or "there"
        candidate      = row["candidate_name"] or "Candidate"
        phone          = row["poc_phone"]
        client_id      = row["client_id"]
        is_round2      = (row["interview_round"] or 1) >= 2

    # v2 templates moved the link to a URL button (deep-linked into the
    # Interview Management tab for this requirement) instead of embedding
    # it as a 4th body param — same button pattern as every candidate-side
    # slot message this session. Only used once these are approved and the
    # env vars are flipped; the original templates still expect the link
    # embedded in the body (see the plain body_components below).
    _BUTTON_TEMPLATES = {WT.CLIENT_SLOT_CONFIRMED_V2, WT.CLIENT_SLOT_CONFIRMED_ROUND2_V2}
    from services.flow_engine import client_portal_deep_link
    body_components = [{"type": "body", "parameters": [
        {"type": "text", "text": poc_name},
        {"type": "text", "text": candidate},
        {"type": "text", "text": slot_label},
        {"type": "text", "text": portal_url},
    ]}]
    button_components = [
        {"type": "body", "parameters": [
            {"type": "text", "text": poc_name},
            {"type": "text", "text": candidate},
            {"type": "text", "text": slot_label},
        ]},
        {"type": "button", "sub_type": "url", "index": "0", "parameters": [
            {"type": "text", "text": client_portal_deep_link(client_id, "interviews")},
        ]},
    ]

    try:
        if is_round2 and round2_template:
            components = button_components if round2_template in _BUTTON_TEMPLATES else body_components
            await msg91.send_template(phone, round2_template, "en", components, sender="client")
        elif not is_round2 and template:
            components = button_components if template in _BUTTON_TEMPLATES else body_components
            await msg91.send_template(phone, template, "en", components, sender="client")
        else:
            round_phrase = "second round " if is_round2 else ""
            await msg91.send_text(
                phone,
                f"Hi {poc_name}!\n\n{candidate} has confirmed their {round_phrase}interview slot on {slot_label}.\n\n"
                f"View your pipeline and all shortlisted candidates here:\n"
                f"👉 {portal_url}\n\n"
                f"Let us know if you need to reschedule.\n\nTeam JustAccountants",
                sender="client",
            )
        pool2 = get_pool()
        async with pool2.acquire() as log_conn:
            await log_conn.execute(
                """
                INSERT INTO whatsapp_messages
                    (client_id, mapping_id, message_type, direction, phone, status, sent_at)
                VALUES ($1, $2::uuid, 'client_interview_confirmed', 'outbound', $3, 'sent', now())
                """,
                client_id, mapping_id, phone,
            )
    except Exception as e:
        print(f"[interview_confirmation] send failed for {mapping_id}: {e}")


@router.post("/candidate/{mapping_id}/confirm")
async def confirm_candidate_slot(
    mapping_id: str,
    body: CandidateSlotConfirmRequest,
    background_tasks: BackgroundTasks,
    conn: asyncpg.Connection = Depends(get_conn),
):
    try:
        mid = uuid.UUID(mapping_id)
    except ValueError:
        raise HTTPException(400, "Invalid mapping ID")

    row = await conn.fetchrow(
        """
        SELECT m.id, m.stage, m.client_id, m.candidate_id,
               ca.phone AS candidate_phone, ca.name AS candidate_name,
               c.company_name, c.job_location, c.location_lat, c.location_lng,
               c.available_slots
        FROM client_candidate_mappings m
        JOIN clients    c  ON c.id  = m.client_id
        JOIN candidates ca ON ca.id = m.candidate_id
        WHERE m.id = $1
        """,
        mid,
    )
    if not row:
        raise HTTPException(404, "Not found")

    # Guard: candidate has already confirmed a slot for this mapping — don't allow changes
    if row["stage"] in ("slot_booked", "interview_scheduled", "interview_done", "placed"):
        raise HTTPException(409, "An interview slot has already been confirmed for this link.")

    # Guard: reject if slot is before candidate's lock on another client expires
    other_lock = await conn.fetchrow(
        """
        SELECT expires_at FROM candidate_locks
        WHERE candidate_id = $1
          AND client_id   != $2
          AND unlocked_at  IS NULL
          AND expires_at   IS NOT NULL
          AND expires_at   > now()
        ORDER BY expires_at DESC
        LIMIT 1
        """,
        row["candidate_id"], row["client_id"],
    )
    if other_lock and other_lock["expires_at"]:
        # Look up ISO for the chosen slot label
        all_slots = list(row["available_slots"] or [])
        slot_iso = next((s.get("iso") for s in all_slots if s.get("label") == body.slot_label), None)
        if slot_iso:
            try:
                slot_dt = datetime.fromisoformat(slot_iso)
                from datetime import timezone as _tz
                if slot_dt.tzinfo is None:
                    slot_dt = slot_dt.replace(tzinfo=_tz.utc)
                if slot_dt < other_lock["expires_at"]:
                    raise HTTPException(409, {
                        "reason": "candidate_locked",
                        "earliest_allowed": other_lock["expires_at"].isoformat(),
                        "message": f"This candidate is busy until {other_lock['expires_at'].strftime('%-d %b, %I:%M %p')}. Please pick a later slot.",
                    })
            except HTTPException:
                raise
            except Exception:
                pass

    # Race-condition guard: re-check slot still has capacity
    existing_bookings = await conn.fetchval(
        """
        SELECT COUNT(*) FROM client_candidate_mappings
        WHERE client_id = $1
          AND stage IN ('slot_booked', 'interview_scheduled', 'interview_done', 'placed')
          AND (available_slots->0->>'confirmed_by') = 'candidate'
          AND (available_slots->0->>'label') = $2
          AND id != $3
        """,
        row["client_id"], body.slot_label, mid,
    )
    if existing_bookings >= 2:
        raise HTTPException(409, "This slot is no longer available. Please go back and select another.")

    # Parse the slot label to get the actual interview start time
    parsed_slot_dt = _parse_slot_label_to_dt(body.slot_label)

    # Store candidate's slot selection + advance stage
    await conn.execute(
        """
        UPDATE client_candidate_mappings
        SET available_slots = $1,
            stage = 'slot_booked'::mapping_stage,
            interview_slot = $3,
            slot_sent_at = now(),
            updated_at = now()
        WHERE id = $2
        """,
        [{"label": body.slot_label, "confirmed_by": "candidate",
          "confirmed_at": datetime.now(timezone.utc).isoformat()}],
        mid,
        parsed_slot_dt,
    )

    # Stamp the lock expiry so other clients can see this candidate as projected-free
    # within 48h of the interview (lock expires 24h after interview start)
    if parsed_slot_dt:
        from datetime import timedelta as _td
        lock_expires = parsed_slot_dt + _td(hours=24)
        await conn.execute(
            """
            INSERT INTO candidate_locks (candidate_id, client_id, expires_at)
            VALUES ($1, $2, $3)
            ON CONFLICT (candidate_id, client_id)
            DO UPDATE SET expires_at = EXCLUDED.expires_at, unlocked_at = NULL
            """,
            row["candidate_id"], row["client_id"], lock_expires,
        )

    import services.msg91_service as msg91

    # Confirm to candidate immediately
    if row["candidate_phone"]:
        try:
            lat, lng = row.get("location_lat"), row.get("location_lng")
            if lat and lng:
                maps_url = f"https://maps.google.com/?q={lat},{lng}"
            elif row.get("job_location"):
                import urllib.parse
                maps_url = f"https://maps.google.com/?q={urllib.parse.quote(row['job_location'])}"
            else:
                maps_url = None
            location_line = f"📍 {maps_url}\n" if maps_url else ""
            msg = (
                f"Hi {row['candidate_name']}! ✅\n\n"
                f"Your interview with *{row['company_name']}* has been scheduled for:\n"
                f"📅 {body.slot_label}\n"
                f"{location_line}\n"
                f"Please be available 5 minutes before the scheduled time."
            )
            await msg91.send_text(row["candidate_phone"], msg, sender="candidate")
        except Exception:
            pass

    # Generate PDF + notify client in background (don't block the response)
    background_tasks.add_task(
        _send_interview_confirmation_to_client,
        str(mid),
        body.slot_label,
    )

    return {"data": {"confirmed": body.slot_label}, "ok": True}


