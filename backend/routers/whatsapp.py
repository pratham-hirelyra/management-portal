import uuid
import os
import re
import json as _json
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Request, Query
import asyncpg
from database import get_conn, get_pool
from models.whatsapp import BulkMessageRequest, AgreementMessageRequest
from pydantic import BaseModel
import services.msg91_service as msg91
import services.flow_engine as flow_engine
import asyncio
import constants.whatsapp_templates as WT

router = APIRouter(prefix="/whatsapp", tags=["whatsapp"])

_CANDIDATE_INTENT_TEMPLATE = WT.CANDIDATE_INTENT


# ── Request bodies ────────────────────────────────────────────────────────────

class SendTemplateBody(BaseModel):
    template_name: str
    lang: str = "en"
    variables: list[str] = []
    header_url: Optional[str] = None
    header_type: Optional[str] = None  # image | video | document

class BulkClientReachoutRequest(BaseModel):
    client_ids: list[str]
    template_name: str
    lang: str = "en"
    variables: list[str] = []
    header_url: Optional[str] = None
    header_type: Optional[str] = None

class BulkCandidateIntentRequest(BaseModel):
    mapping_ids: list[str]

class IntentCallbackBody(BaseModel):
    mapping_id: str
    intent: str  # "interested" | "not_interested"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_msg_id(result: dict) -> str | None:
    """MSG91 returns data as a dict with message_uuid on single sends."""
    data = result.get("data")
    if isinstance(data, dict):
        return data.get("message_uuid")
    if isinstance(data, list) and data:
        return data[0].get("id") or data[0].get("message_uuid")
    return None


def _build_components(
    variables: list[str],
    header_url: str | None = None,
    header_type: str | None = None,
) -> list[dict]:
    components = []
    if header_url:
        ht = (header_type or "image").lower()
        components.append({
            "type": "header",
            "parameters": [{"type": ht, ht: {"link": header_url}}],
        })
    if variables:
        components.append({
            "type": "body",
            "parameters": [{"type": "text", "text": v} for v in variables],
        })
    return components


# ── Webhook ───────────────────────────────────────────────────────────────────

@router.get("/webhook")
async def verify_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
):
    if hub_mode == "subscribe" and hub_verify_token == os.environ.get("WHATSAPP_VERIFY_TOKEN"):
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse(hub_challenge)
    raise HTTPException(403, "Webhook verification failed")


@router.post("/webhook")
async def receive_webhook(request: Request, background_tasks: BackgroundTasks):
    payload = await request.json()
    background_tasks.add_task(_process_webhook, payload)
    return {"status": "ok"}


@router.post("/msg91-event")
async def msg91_event(request: Request, background_tasks: BackgroundTasks):
    """
    Dedicated endpoint for MSG91 delivery event webhooks (failed, delivered, read).
    Set this URL in MSG91 → WhatsApp → Webhook Events for each event type.

    Payload fields used:
      uuid           — our meta_msg_id
      customerNumber — recipient phone
      templateName   — filter: only act on client_reachout_1
      webhookType    — FAILED | DELIVERED | READ
      reason         — error reason / Meta error code string
    """
    payload = await request.json()
    background_tasks.add_task(_process_msg91_event, payload)
    return {"status": "ok"}


@router.post("/msg91-click-event")
async def msg91_click_event(request: Request, background_tasks: BackgroundTasks):
    """
    Dedicated endpoint for MSG91's URL-click webhook (Dashboard → WhatsApp →
    Webhook (New) → CLICKED event) — a different payload shape from
    /msg91-event, fired when a recipient taps a URL-type template button.
    Currently only relevant to hl_cand_jd_img_v3 (Interested Role / Not
    Interested Role are URL buttons, not quick-replies — see
    services/msg91_service.py). Set this URL as the CLICKED event's webhook.

    Payload fields used:
      requestId      — MSG91 request id (not reliably our stored meta_msg_id
                       for bulk JD sends — phone is the fallback correlator,
                       same as delivery-status matching in _process_msg91_event)
      customerNumber — recipient phone
      link           — the real destination URL that was clicked (distinguishes
                       Interested Role /apply from Not Interested Role /p-apply)
    """
    payload = await request.json()
    background_tasks.add_task(_process_msg91_click_event, payload)
    return {"status": "ok"}


def _phones_for_client(client) -> list[str]:
    """Return all unique phone numbers for a client (from phone_numbers array, falling back to poc_phone)."""
    import json as _j
    raw = client["phone_numbers"] or []
    if isinstance(raw, str):
        try:
            raw = _j.loads(raw)
        except Exception:
            raw = []
    phones = [p["number"] for p in raw if isinstance(p, dict) and p.get("number")]
    if not phones and client["poc_phone"]:
        phones = [client["poc_phone"]]
    seen = set()
    return [p for p in phones if p not in seen and not seen.add(p)]


def _phone_variants(phone: str) -> list[str]:
    phone = phone.lstrip("+").strip()
    variants = [phone]
    if phone.startswith("91") and len(phone) == 12:
        variants.append(phone[2:])
    elif len(phone) == 10 and phone.isdigit():
        variants.append("91" + phone)
    return variants


async def _is_new_contact(conn: asyncpg.Connection, from_phone: str) -> bool:
    """True if this phone has never received an outbound message from us."""
    variants = _phone_variants(from_phone)
    sent = await conn.fetchval(
        """
        SELECT 1 FROM whatsapp_messages
        WHERE phone = ANY($1::text[]) AND direction = 'outbound'
        LIMIT 1
        """,
        variants,
    )
    return sent is None


async def _handle_candidate_reply(
    conn: asyncpg.Connection, from_phone: str, text: str, button_payload: str | None = None
):
    if not button_payload:
        known = await conn.fetchval(
            "SELECT 1 FROM candidates WHERE phone = ANY($1::text[]) LIMIT 1",
            _phone_variants(from_phone),
        )

        if known:
            # ── Payload-less button tap (MSG91 sometimes omits payload on 3-button templates) ──
            # Derive trigger from button text and apply to ALL active intent_ask mappings.
            text_upper = (text or "").upper()
            if "NOT LOOKING" in text_upper:
                fallback_trigger = "NOT_LOOKING_FOR_JOB"
            elif "NOT INTERESTED" in text_upper:
                fallback_trigger = "NOT_INTERESTED_ROLE"
            elif "INTERESTED" in text_upper:
                fallback_trigger = "INTERESTED"
            else:
                fallback_trigger = None

            if fallback_trigger:
                active_mappings = await conn.fetch(
                    """
                    SELECT ccm.id AS mapping_id, ccm.stage
                    FROM client_candidate_mappings ccm
                    JOIN candidates c ON c.id = ccm.candidate_id
                    WHERE c.phone = ANY($1::text[])
                      AND ccm.stage IN ('intent_ask', 'interested')
                    ORDER BY ccm.updated_at DESC
                    """,
                    _phone_variants(from_phone),
                )
                print(f"[webhook] payload-less button: trigger={fallback_trigger} phone={from_phone} mappings={len(active_mappings)}")
                for m in active_mappings:
                    try:
                        executed = await flow_engine.execute_candidate_flow(
                            conn, m["mapping_id"], m["stage"], fallback_trigger, from_phone
                        )
                        print(f"[webhook] payload-less flow executed={executed} mapping={m['mapping_id']}")
                    except Exception as e:
                        print(f"[webhook] payload-less flow error mapping={m['mapping_id']}: {e}")
                return

        # Free text that didn't match a deterministic keyword — a known candidate
        # asking something, or a first-time prospective contact. Replaces the old
        # static "Hi! Thanks for reaching out" greeting, which resent identical
        # text on every message from an unrecognised number with no memory of
        # what was already said. Gated by CANDIDATE_AI_RM_ENABLED and, for known
        # candidates, candidates.ai_rm_enabled — see services/ai_rm_service.py.
        from services import ai_rm_service as _ai_rm
        try:
            await _ai_rm.handle_free_text(conn, from_phone, text)
        except Exception as e:
            print(f"[webhook] ai_rm handle_free_text failed for {from_phone}: {e}")
        return

    # ── Path 1: button payload with embedded mapping_id ────────────────────────
    # Candidate button payloads: INTERESTED|<mapping_id>, NOT_INTERESTED_ROLE|<mapping_id>, NOT_LOOKING_FOR_JOB|<mapping_id>
    if button_payload and "|" in button_payload:
        trigger, mapping_id_str = button_payload.split("|", 1)
        # MSG91 payloads are unreliable with 3-button templates — use button TEXT as source of truth.
        text_upper = (text or "").upper()
        if "NOT LOOKING" in text_upper:
            trigger = "NOT_LOOKING_FOR_JOB"
        elif "NOT INTERESTED" in text_upper:
            trigger = "NOT_INTERESTED_ROLE"
        elif "INTERESTED" in text_upper:
            trigger = "INTERESTED"
        try:
            mapping_id = uuid.UUID(mapping_id_str.strip())
            mapping = await conn.fetchrow(
                "SELECT stage FROM client_candidate_mappings WHERE id = $1", mapping_id
            )
            if mapping:
                print(f"[webhook] candidate button: trigger={trigger.strip()} stage={mapping['stage']} mapping={mapping_id}")
                executed = await flow_engine.execute_candidate_flow(
                    conn, mapping_id, mapping["stage"], trigger.strip(), from_phone
                )
                print(f"[webhook] flow executed={executed}")
            else:
                print(f"[webhook] mapping not found: {mapping_id_str.strip()}")
        except ValueError as e:
            print(f"[webhook] invalid mapping_id '{mapping_id_str}': {e}")
        except Exception as e:
            print(f"[webhook] candidate reply error: {e}")
        return

    # ── Path 2: button payload without mapping_id (template sent payload as display text) ──
    # e.g. payload="Not Looking for Job" instead of "NOT_LOOKING_FOR_JOB|<uuid>"
    if button_payload and "|" not in button_payload:
        combined = ((button_payload or "") + " " + (text or "")).upper()
        if "NOT LOOKING" in combined:
            fallback_trigger = "NOT_LOOKING_FOR_JOB"
        elif "NOT INTERESTED" in combined:
            fallback_trigger = "NOT_INTERESTED_ROLE"
        elif "INTERESTED" in combined:
            fallback_trigger = "INTERESTED"
        else:
            fallback_trigger = None

        if fallback_trigger:
            active_mappings = await conn.fetch(
                """
                SELECT ccm.id AS mapping_id, ccm.stage
                FROM client_candidate_mappings ccm
                JOIN candidates c ON c.id = ccm.candidate_id
                WHERE c.phone = ANY($1::text[])
                  AND ccm.stage IN ('intent_ask', 'interested')
                ORDER BY ccm.updated_at DESC
                """,
                _phone_variants(from_phone),
            )
            print(f"[webhook] no-pipe button: trigger={fallback_trigger} phone={from_phone} payload={button_payload!r} mappings={len(active_mappings)}")
            for m in active_mappings:
                try:
                    executed = await flow_engine.execute_candidate_flow(
                        conn, m["mapping_id"], m["stage"], fallback_trigger, from_phone
                    )
                    print(f"[webhook] no-pipe flow executed={executed} mapping={m['mapping_id']}")
                except Exception as e:
                    print(f"[webhook] no-pipe flow error mapping={m['mapping_id']}: {e}")
        return


async def _send_onboarding_link(phone: str, client_id: str = "") -> None:
    """Send the client onboarding form link after they show interest."""
    frontend_url = os.environ.get("FRONTEND_URL", "http://localhost:5174")
    onboarding_url = f"{frontend_url}/client-onboard"
    template = WT.CLIENT_ONBOARDING
    if template:
        components = [{"type": "body", "parameters": [{"type": "text", "text": onboarding_url}]}]
        await msg91.send_template(phone, template, "en", components, sender="client")
    else:
        msg = (
            f"Thank you for your interest!\n\n"
            f"Please fill out your hiring requirements using the link below "
            f"and our team will find the right candidate for you:\n\n"
            f"{onboarding_url}"
        )
        await msg91.send_text(phone, msg, sender="client")


async def _post_agree_actions(conn, client_id) -> None:
    """Shared: called after a client confirms the agreement (web page or WhatsApp button)."""
    import uuid as _uuid

    client = await conn.fetchrow(
        "SELECT poc_phone, poc_name, company_name, rm_id, agreement_url, job_title FROM clients WHERE id = $1", client_id
    )
    if not client or not client["poc_phone"]:
        return

    print(f"[agree] post-agree for {client_id}: rm_id={client['rm_id']}, phone={client['poc_phone']}")

    # Send confirmed agreement PDF
    if client["agreement_url"]:
        try:
            poc_name = client["poc_name"] or client["company_name"] or "there"
            pdf_components = [
                {
                    "type": "header",
                    "parameters": [
                        {
                            "type": "document",
                            "document": {
                                "link": client["agreement_url"],
                                "filename": f"JustAccountants_Agreement_{(client['job_title'] or 'Agreement').replace(' ', '_')}.pdf",
                            },
                        }
                    ],
                }
            ]
            await msg91.send_template(
                client["poc_phone"],
                WT.CLIENT_AGREEMENT_CONFIRMED,
                "en",
                pdf_components,
                sender="client",
            )
        # Wait for 5 seconds
            await asyncio.sleep(5)
            
        except Exception as e:
            print(f"[agree] PDF send failed for {client_id}: {e}")

    # Tell the client their hiring process has started and their portal is live —
    # points at the OTP-based client-portal app (CLIENT_PORTAL_URL), not the
    # legacy token-based /client/{feedback_token} page in this frontend.
    try:
        portal_template = WT.CLIENT_PORTAL_LINK
        already_sent_portal_link = await conn.fetchval(
            """
            SELECT 1 FROM whatsapp_messages
            WHERE phone = $1 AND used_template_name = $2 AND direction = 'outbound'
            LIMIT 1
            """,
            client["poc_phone"],
            portal_template,
        )
        if not already_sent_portal_link:
            poc_name = client["poc_name"] or client["company_name"] or "there"
            portal_url = os.environ.get("CLIENT_PORTAL_URL", "https://employer.justaccountants.in").rstrip("/")
            if portal_template:
                components = [{"type": "body", "parameters": [
                    {"type": "text", "text": poc_name},
                    {"type": "text", "text": portal_url},
                ]}]
                await msg91.send_template(client["poc_phone"], portal_template, "en", components, sender="client")
            else:
                await msg91.send_text(
                    client["poc_phone"],
                    f"Hi {poc_name}! Your hiring process has started — you can now view your job profile, "
                    f"live on our portal:\n\n{portal_url}",
                    sender="client",
                )
    except Exception as e:
        print(f"[agree] portal-link send failed for {client_id}: {e}")

    # Ensure RM is assigned — assign on the spot if still null at agree-time
    # (RM ownership is still needed internally for routing/alerts — just no
    # longer WhatsApp-introduced to the client at this step)
    rm_id = client["rm_id"]
    if not rm_id:
        from services.rm_service import assign_rm as _assign_rm
        city = await conn.fetchval("SELECT city FROM clients WHERE id = $1", client_id)
        rm_row = await _assign_rm(str(client_id), city, conn)
        if rm_row:
            rm_id = rm_row["id"]
            print(f"[agree] Late RM assignment: {rm_row['name']} → {client_id}")
        else:
            print(f"[agree] no eligible RM found for client {client_id}")

    # Auto-trigger matchmaking + search-started message — this is the only
    # code path shared by every way a client can reach 'onboarded' (WhatsApp
    # AGREED button, web agreement page, and the admin PATCH /stage endpoint
    # which schedules these itself). Without this, clients onboarded via the
    # WhatsApp/web agreement flows never got decision-point run for them.
    from routers.clients import _run_auto_match, _send_search_started
    asyncio.create_task(_run_auto_match(str(client_id)))
    asyncio.create_task(_send_search_started(str(client_id)))


async def _dnd_single_phone(conn: asyncpg.Connection, client_id, phone: str) -> None:
    """
    Opt out a single phone number from a client.
    Removes it from the active phone_numbers[] list (so automated resends skip
    it) and adds it to dnd_phones[]. poc_phone is only ever reassigned to a
    surviving number — never cleared — so the contact stays visible on the
    client record even once every number has opted out. is_dnd (and stage,
    once no numbers remain) is what actually blocks future messaging: every
    automated reachout/follow-up query filters on is_dnd = false, and the two
    manual single-send endpoints check is_dnd explicitly.

    phone_numbers entries may be either plain phone strings (written by the
    enrichment cron) or {"number": ..., ...} dicts (written by the onboarding/
    agreement flows) — both shapes are handled here.
    """
    await conn.execute(
        "ALTER TABLE clients ADD COLUMN IF NOT EXISTS dnd_phones jsonb DEFAULT '[]'::jsonb"
    )

    client = await conn.fetchrow(
        "SELECT id, poc_phone, phone_numbers, dnd_phones FROM clients WHERE id = $1",
        client_id,
    )
    if not client:
        return

    # Normalise to 10 digits
    phone_10 = re.sub(r"\D", "", phone)
    if phone_10.startswith("91") and len(phone_10) == 12:
        phone_10 = phone_10[2:]

    def _entry_number(p) -> str | None:
        if isinstance(p, dict):
            return p.get("number")
        if isinstance(p, str):
            return p
        return None

    def _matches(num: str | None) -> bool:
        return bool(num) and re.sub(r"\D", "", num)[-10:] == phone_10

    raw = client["phone_numbers"]
    current_entries = (raw if isinstance(raw, list) else _json.loads(raw)) if raw else []
    if not isinstance(current_entries, list):
        current_entries = []

    raw_dnd = client["dnd_phones"]
    dnd_phones: list[str] = (raw_dnd if isinstance(raw_dnd, list) else _json.loads(raw_dnd)) if raw_dnd else []

    # Remove opting-out number from the active list (preserving each entry's original shape)
    remaining_entries = [p for p in current_entries if not _matches(_entry_number(p))]
    remaining_numbers = [n for n in (_entry_number(p) for p in remaining_entries) if n]

    # Track in dnd_phones
    if phone_10 not in dnd_phones:
        dnd_phones.append(phone_10)

    old_poc = client["poc_phone"]
    was_poc = _matches(old_poc)

    if was_poc and remaining_numbers:
        promoted_poc = remaining_numbers[0]
    elif was_poc:
        promoted_poc = None  # no active number left to promote
    else:
        promoted_poc = old_poc

    no_phones_left = not remaining_numbers and not promoted_poc

    # Never actually blank poc_phone — fall back to the last known number so
    # it stays visible on the client record even when fully opted out.
    new_poc = promoted_poc if promoted_poc is not None else old_poc

    await conn.execute(
        """
        UPDATE clients
        SET phone_numbers = $2::jsonb,
            poc_phone     = $3,
            dnd_phones    = $4::jsonb,
            is_dnd        = $5,
            labels        = CASE WHEN $5 THEN array_append(array_remove(COALESCE(labels, '{}'), 'Opted Out'), 'Opted Out') ELSE labels END,
            stage         = CASE WHEN $5 THEN 'disqualified'::client_stage ELSE stage END,
            updated_at    = now()
        WHERE id = $1
        """,
        client_id,
        _json.dumps(remaining_entries),
        new_poc,
        _json.dumps(dnd_phones),
        no_phones_left,
    )

    if no_phones_left:
        print(f"[dnd] client {client_id} fully DND'd — no phones remaining after {phone_10} opted out")
    else:
        print(f"[dnd] phone {phone_10} opted out from client {client_id} — {len(remaining_numbers)} phone(s) remain")


_BOT_KEYWORDS = frozenset([
    "automated", "auto reply", "auto-reply", "automatic reply", "automatic response",
    "automated response", "automated message", "out of office", "i am out of office",
    "this is an automated", "do not reply", "do not respond", "noreply", "no-reply",
    "this message was sent automatically", "this is a system generated",
])

async def _check_and_flag_bot(conn: asyncpg.Connection, client_id, from_phone: str, text: str) -> bool:
    """
    Auto-flags as bot ONLY on keyword match (safe, zero false positives).
    For suspicious behaviour (fast reply, high frequency), sends a Google Chat
    alert so admin can manually set is_bot=true — does NOT auto-flag.
    Returns True if the message should be suppressed (auto-flagged as bot).
    """
    await conn.execute("ALTER TABLE clients ADD COLUMN IF NOT EXISTS is_bot boolean DEFAULT false")

    lower = (text or "").lower()
    variants = _phone_variants(from_phone)

    # ── Auto-flag: keyword match only ────────────────────────────────────────
    if any(kw in lower for kw in _BOT_KEYWORDS):
        await conn.execute(
            "UPDATE clients SET is_bot = true, updated_at = now() WHERE id = $1",
            client_id,
        )
        reason = f"auto-reply keyword in message: {text[:80]}"
        print(f"[bot-detect] client {client_id} auto-flagged as bot — {reason}")
        try:
            from services.google_chat_service import send_alert
            asyncio.create_task(send_alert(
                title="Bot Auto-Flagged — Keyword Match",
                detail=reason,
                severity="WARNING",
                context={"client_id": str(client_id), "phone": from_phone},
            ))
        except Exception:
            pass
        return True

    # ── Alert only: fast reply (< 30s) ───────────────────────────────────────
    last_outbound = await conn.fetchval(
        "SELECT sent_at FROM whatsapp_messages WHERE phone = ANY($1::text[]) AND direction = 'outbound' ORDER BY sent_at DESC LIMIT 1",
        variants,
    )
    if last_outbound:
        import datetime as _dt
        from datetime import timezone as _tz
        now = _dt.datetime.now(_tz.utc)
        last_aware = last_outbound if last_outbound.tzinfo else last_outbound.replace(tzinfo=_tz.utc)
        elapsed = (now - last_aware).total_seconds()
        if elapsed < 30:
            try:
                from services.google_chat_service import send_alert
                asyncio.create_task(send_alert(
                    title="Possible Bot — Very Fast Reply",
                    detail=f"Reply received {elapsed:.1f}s after outbound. Message: {text[:120]}\nReview and set is_bot=true manually if needed.",
                    severity="WARNING",
                    context={"client_id": str(client_id), "phone": from_phone},
                ))
            except Exception:
                pass

    # ── Alert only: high frequency (5+ inbound in 5 min) ─────────────────────
    recent_inbound = await conn.fetchval(
        "SELECT COUNT(*) FROM whatsapp_messages WHERE phone = ANY($1::text[]) AND direction = 'inbound' AND created_at > now() - interval '5 minutes'",
        variants,
    )
    if recent_inbound >= 5:
        try:
            from services.google_chat_service import send_alert
            asyncio.create_task(send_alert(
                title="Possible Bot — High Message Frequency",
                detail=f"{recent_inbound} inbound messages in 5 minutes. Message: {text[:120]}\nReview and set is_bot=true manually if needed.",
                severity="WARNING",
                context={"client_id": str(client_id), "phone": from_phone},
            ))
        except Exception:
            pass

    return False


async def _handle_client_reply(
    conn: asyncpg.Connection, from_phone: str, text: str, button_payload: str | None = None
):
    # ── STOP button — phone-level opt-out (only DND whole client if no phones remain) ──
    if button_payload == "STOP" or (text or "").strip().upper() == "STOP":
        variants = _phone_variants(from_phone)
        client = await conn.fetchrow(
            """
            SELECT id FROM clients
            WHERE poc_phone = ANY($1::text[])
               OR EXISTS (
                   SELECT 1 FROM jsonb_array_elements(
                       CASE WHEN jsonb_typeof(COALESCE(phone_numbers,'[]'::jsonb)) = 'array'
                            THEN COALESCE(phone_numbers,'[]'::jsonb)
                            ELSE '[]'::jsonb END
                   ) p
                   WHERE COALESCE(p ->> 'number', p #>> '{}') = ANY($1::text[])
               )
            LIMIT 1
            """,
            variants,
        )
        if client:
            await _dnd_single_phone(conn, client["id"], from_phone)
        return

    # ── Bot detection — skip all further processing if flagged ───────────────
    variants = _phone_variants(from_phone)
    client_for_bot = await conn.fetchrow(
        """
        SELECT id, is_bot FROM clients
        WHERE poc_phone = ANY($1::text[])
           OR EXISTS (
               SELECT 1 FROM jsonb_array_elements(
                   CASE WHEN jsonb_typeof(COALESCE(phone_numbers,'[]'::jsonb)) = 'array'
                        THEN COALESCE(phone_numbers,'[]'::jsonb)
                        ELSE '[]'::jsonb END
               ) p WHERE COALESCE(p ->> 'number', p #>> '{}') = ANY($1::text[])
           )
        LIMIT 1
        """,
        variants,
    )
    if client_for_bot:
        if client_for_bot["is_bot"]:
            print(f"[bot-detect] ignoring reply from known bot {from_phone} for client {client_for_bot['id']}")
            return
        flagged = await _check_and_flag_bot(conn, client_for_bot["id"], from_phone, text or "")
        if flagged:
            return

    # ── Simple client support bot — issue/stuck-reason button flow ───────────
    # Keyed purely off button id, so it's checked before anything else that
    # branches on button_payload. See services/client_support_bot.py.
    if button_payload:
        from services import client_support_bot
        if await client_support_bot.handle_button(conn, from_phone, button_payload, text):
            return

    # ── Greeting / YES flow for first-time unknown contacts ───────────────────
    if not button_payload:
        known = await conn.fetchval(
            """
            SELECT 1 FROM clients
            WHERE poc_phone = ANY($1::text[])
               OR EXISTS (
                   SELECT 1 FROM jsonb_array_elements(
                       CASE WHEN jsonb_typeof(COALESCE(phone_numbers,'[]'::jsonb)) = 'array'
                            THEN COALESCE(phone_numbers,'[]'::jsonb)
                            ELSE '[]'::jsonb END
                   ) p WHERE p->>'number' = ANY($1::text[])
               )
            LIMIT 1
            """,
            _phone_variants(from_phone),
        )
        if not known:
            variants = _phone_variants(from_phone)
            outbound_count = await conn.fetchval(
                "SELECT COUNT(*) FROM whatsapp_messages WHERE phone = ANY($1::text[]) AND direction = 'outbound'",
                variants,
            )
            if outbound_count == 0:
                # First contact — send greeting and record it
                greeting = (
                    "Hi! 👋 Thanks for reaching out to JustAccountants.\n\n"
                    "We're a specialised recruitment firm helping businesses hire qualified accounting professionals.\n\n"
                    "Are you looking to hire an accountant? Reply *YES* and we'll get you started, "
                    "or let us know how we can help."
                )
                try:
                    await msg91.send_text(from_phone, greeting, sender="client")
                    await conn.execute(
                        "INSERT INTO whatsapp_messages (message_type, direction, phone, message_text, status) VALUES ('greeting', 'outbound', $1, $2, 'sent')",
                        from_phone, greeting,
                    )
                except Exception as e:
                    print(f"[webhook] client greeting failed for {from_phone}: {e}")
                return
            elif text.strip().lower() in ("yes", "yes!", "haan", "ha", "y"):
                # Unknown contact said YES — send onboarding link
                try:
                    await _send_onboarding_link(from_phone)
                except Exception as e:
                    print(f"[webhook] client onboarding link failed for {from_phone}: {e}")
                return
            else:
                # Unknown contact typed something else (not "yes", not their
                # first message) — not a client yet, so the support-bot's
                # "are you facing any issues with the process?" doesn't fit;
                # just nudge them toward the YES flow.
                try:
                    await msg91.send_text(
                        from_phone,
                        "Reply *YES* if you're looking to hire an accountant and we'll get you started! 😊",
                        sender="client",
                    )
                except Exception as e:
                    print(f"[webhook] client nudge failed for {from_phone}: {e}")
                return

    # ── Path 1: button payload with client_id ──────────────────────────────────
    # INTERESTED|<uuid>      — client wants to hire
    # NOT_INTERESTED|<uuid>  — client opts out → DND (client reachout templates only)
    # AGREED|<uuid>          — agreement quick-reply shortcut
    # NOT_AGREED|<uuid>      — agreement declined via WhatsApp
    if button_payload and "|" in button_payload:
        trigger, client_id_str = button_payload.split("|", 1)
        try:
            client_id = uuid.UUID(client_id_str.strip())
            trigger = trigger.strip().upper()

            if trigger == "INTERESTED":
                client_row = await conn.fetchrow("SELECT stage, poc_phone FROM clients WHERE id = $1", client_id)
                if client_row and client_row["stage"] in ("scraped", "reachout_sent", "lead"):
                    phone_10 = next(
                        (v for v in _phone_variants(from_phone) if len(v) == 10),
                        from_phone,
                    )
                    await conn.execute(
                        """
                        UPDATE clients
                        SET stage = 'interested'::client_stage,
                            poc_phone = COALESCE(poc_phone, $1),
                            onboarding_form_sent_at = COALESCE(onboarding_form_sent_at, now()),
                            updated_at = now()
                        WHERE id = $2
                        """,
                        phone_10, client_id,
                    )
                    await _send_onboarding_link(phone_10, str(client_id))

            elif trigger == "NOT_INTERESTED":
                await conn.execute(
                    """
                    UPDATE clients
                    SET is_dnd = true, stage = 'disqualified'::client_stage, updated_at = now()
                    WHERE id = $1
                    """,
                    client_id,
                )
                try:
                    await msg91.send_text(
                        from_phone,
                        "Thank you for letting us know! 🙏\n\nNo worries at all — if you ever need to hire accounting professionals in the future, we're just a message away. Have a great day!",
                        sender="client",
                    )
                except Exception as e:
                    print(f"[webhook] NOT_INTERESTED farewell send failed for {from_phone}: {e}")

            elif trigger == "AGREED":
                updated = await conn.execute(
                    "UPDATE clients SET stage = 'onboarded'::client_stage, updated_at = now() WHERE id = $1 AND stage = 'agreement_sent'::client_stage",
                    client_id,
                )
                if updated != "UPDATE 0":
                    await _post_agree_actions(conn, client_id)

            elif trigger == "NOT_AGREED":
                await conn.execute(
                    "UPDATE clients SET stage = 'disqualified'::client_stage, updated_at = now() WHERE id = $1 AND stage = 'agreement_sent'::client_stage",
                    client_id,
                )

        except (ValueError, Exception) as e:
            print(f"[webhook] client button payload handling failed: {e}")
        return

    # ── Path 1b: "View Profiles" button tap on a reachout template ────────────
    # Detect by button text or payload (no UUID embedded, look up by phone).
    # client_reachout_2 buttons are "View Profiles" / "Not Required" / "Stop" —
    # MSG91 can echo back either the button text or a payload, so match both,
    # plus the older "Position Open" wording from client_reachout_new for any
    # in-flight replies to previously-sent messages.
    payload_lower = (button_payload or "").lower()
    text_lower = text.lower()
    is_view_profiles = (
        "view_profiles" in payload_lower or
        "view profiles" in payload_lower or
        "view profiles" in text_lower or
        "position_open" in payload_lower or
        "position open" in payload_lower or
        "position open" in text_lower
    )
    if is_view_profiles:
        variants = _phone_variants(from_phone)
        row = await conn.fetchrow(
            """
            SELECT wm.client_id, c.poc_phone, c.phone_numbers
            FROM whatsapp_messages wm
            JOIN clients c ON c.id = wm.client_id
            WHERE wm.phone = ANY($1::text[])
              AND wm.message_type = 'client_reachout'
              AND wm.direction = 'outbound'
              AND c.stage = 'reachout_sent'::client_stage
            ORDER BY wm.sent_at DESC LIMIT 1
            """,
            variants,
        )
        if row:
            import json as _json
            client_id = row["client_id"]
            phone_10 = next((v for v in variants if len(v) == 10), variants[0])
            _raw = row["phone_numbers"] or []
            if isinstance(_raw, str):
                try: _raw = _json.loads(_raw)
                except Exception: _raw = []
            updated_phones = [{**p, "agreed": p.get("number") == phone_10} for p in _raw if isinstance(p, dict)]
            await conn.execute(
                """
                UPDATE clients
                SET stage        = 'interested'::client_stage,
                    agreed_phone = $1,
                    poc_phone    = $1,
                    phone_numbers = $2::jsonb,
                    updated_at   = now()
                WHERE id = $3
                """,
                phone_10, _json.dumps(updated_phones), client_id,
            )
            try:
                await _send_onboarding_link(phone_10, str(client_id))
            except Exception:
                pass
        return

    # ── Path 2: reachout reply — look up client via whatsapp_messages (multi-number case) ─────
    variants = _phone_variants(from_phone)

    reachout_row = await conn.fetchrow(
        """
        SELECT wm.client_id, c.stage, c.phone_numbers
        FROM whatsapp_messages wm
        JOIN clients c ON c.id = wm.client_id
        WHERE wm.phone = ANY($1::text[])
          AND wm.message_type = 'client_reachout'
          AND wm.direction = 'outbound'
          AND c.stage = 'reachout_sent'::client_stage
        ORDER BY wm.sent_at DESC LIMIT 1
        """,
        variants,
    )
    if reachout_row:
        import json as _json
        client_id = reachout_row["client_id"]
        phone_10 = next((v for v in variants if len(v) == 10), variants[0])

        _POSITIVE = {"yes", "y", "haan", "ha", "sure", "ok", "okay",
                     "proceed", "please", "yes please", "yes!", "haan ji", "ji", "ji haan"}
        text_norm = text.strip().lower().rstrip("!.,")
        is_positive = text_norm in _POSITIVE or any(k in text_norm for k in ("view profiles", "hire", "yes", "position open", "open"))

        if not is_positive:
            # Unknown intent — nudge, don't advance stage
            try:
                await msg91.send_text(
                    from_phone,
                    "Hi! 👋 To explore hiring accountants with us, please tap the *'View Profiles'* button in our earlier message, or reply *YES* to get started.",
                    sender="client",
                )
            except Exception as e:
                print(f"[webhook] client reachout nudge failed for {from_phone}: {e}")
            return

        _raw = reachout_row["phone_numbers"] or []
        if isinstance(_raw, str):
            try: _raw = _json.loads(_raw)
            except Exception: _raw = []
        updated_phones = [{**p, "agreed": p.get("number") == phone_10} for p in _raw if isinstance(p, dict)]
        await conn.execute(
            """
            UPDATE clients
            SET stage        = 'interested'::client_stage,
                agreed_phone = $1,
                poc_phone    = $1,
                phone_numbers = $2::jsonb,
                updated_at   = now()
            WHERE id = $3
            """,
            phone_10, _json.dumps(updated_phones), client_id,
        )
        try:
            await _send_onboarding_link(phone_10, str(client_id))
        except Exception as e:
            print(f"[webhook] onboarding link after text-yes failed for {from_phone}: {e}")
        return

    # ── Path 3: poc_phone fallback for agreement_sent / onboarded ─────────────
    client = await conn.fetchrow(
        "SELECT id, stage FROM clients WHERE poc_phone = ANY($1::text[])", variants
    )
    if not client:
        return

    stage = client["stage"]

    # Deliberately no text-"yes"/"ok" onboarding shortcut here — a client can
    # only move to 'onboarded' by pressing Agree on the actual agreement page
    # or the WhatsApp template's Agree button (routers/clients.py agree_to_
    # agreement, and the AGREED button handler above). Free text at
    # agreement_sent stage — including "yes"/"ok" — falls through to the
    # advisory AI agent below, which can nudge them back to the real link
    # without ever mutating stage itself.

    # Known client, free text that didn't match any deterministic flow above
    # (e.g. a question, or something they're stuck on, sent at any stage other
    # than the specific agreement-confirmation moment handled just above).
    #
    # Onboarded clients get the simple deterministic support bot (issue/stuck-
    # reason button flow — see services/client_support_bot.py). Clients still
    # earlier in the pipeline (lead/scraping/.../agreement_sent, and terminal
    # churned/disqualified) get the advisory-only AI agent instead — see
    # services/ai_client_rm_service.py.
    if stage == "onboarded":
        from services import client_support_bot
        try:
            await client_support_bot.handle_free_text(conn, from_phone, text)
        except Exception as e:
            print(f"[webhook] client_support_bot handle_free_text failed for {from_phone}: {e}")
    else:
        from services import ai_client_rm_service as _ai_client_rm
        try:
            await _ai_client_rm.handle_free_text(conn, from_phone, text)
        except Exception as e:
            print(f"[webhook] ai_client_rm handle_free_text failed for {from_phone}: {e}")


async def _process_client_reachout_click(link: str, recipient: str) -> None:
    """
    client_reachout_3's "View Profile" URL button was clicked — same outcome
    as the old client_reachout_2 quick-reply "View Profiles" tap (see Path 1b
    in _handle_client_reply): move the client to 'interested', record which
    phone number they clicked from as the agreed one. No onboarding-link text
    follow-up needed — the click itself already lands them on the form.

    The button is approved as a DYNAMIC url (see _send_client_reachout_step),
    so the link carries client_id in ?ref= — that's the primary lookup path.
    The phone + used_template_name fallback below only matters if ref is ever
    missing or malformed.
    """
    import json as _json
    import re as _re
    from urllib.parse import urlparse, parse_qs

    pool = get_pool()
    async with pool.acquire() as conn:
        try:
            client_id = None
            ref = parse_qs(urlparse(link).query).get("ref", [None])[0]
            if ref:
                try:
                    client_id = uuid.UUID(ref)
                except ValueError:
                    client_id = None

            variants = _phone_variants(recipient)
            phone_10 = next((v for v in variants if len(v) == 10), recipient)

            if client_id:
                client = await conn.fetchrow(
                    "SELECT id, phone_numbers FROM clients WHERE id = $1", client_id
                )
            else:
                # Fallback: no/invalid ref — match by phone against the most
                # recent client_reachout_3 send, same reasoning as the JD v3
                # phone-based fallback above.
                client = await conn.fetchrow(
                    """
                    SELECT c.id, c.phone_numbers
                    FROM whatsapp_messages wm
                    JOIN clients c ON c.id = wm.client_id
                    WHERE RIGHT(wm.phone, 10) = RIGHT($1, 10)
                      AND wm.used_template_name = 'client_reachout_3'
                      AND wm.direction = 'outbound'
                    ORDER BY wm.sent_at DESC LIMIT 1
                    """,
                    recipient,
                )

            if not client:
                print(f"[msg91-click] no matching client_reachout_3 client for ref={ref!r} recipient={recipient!r}")
                return

            _raw = client["phone_numbers"] or []
            if isinstance(_raw, str):
                try: _raw = _json.loads(_raw)
                except Exception: _raw = []
            updated_phones = [{**p, "agreed": p.get("number") == phone_10} for p in _raw if isinstance(p, dict)]

            await conn.execute(
                """
                UPDATE clients
                SET stage        = 'interested'::client_stage,
                    agreed_phone = $1,
                    poc_phone    = $1,
                    phone_numbers = $2::jsonb,
                    updated_at   = now()
                WHERE id = $3 AND stage = 'reachout_sent'::client_stage
                """,
                phone_10, _json.dumps(updated_phones), client["id"],
            )
            print(f"[msg91-click] client_reachout_3 View Profile clicked — client {client['id']} → interested")
        except Exception as e:
            print(f"[msg91-click] client_reachout_3 click error: {e}")


async def _process_msg91_click_event(payload: dict):
    """
    Process a MSG91 CLICKED event for URL-type template buttons — no
    quick-reply payload exists for these, so this webhook is the only signal
    WhatsApp gives us for a tap. Covers two unrelated flows, dispatched by the
    clicked link's path:
      • hl_cand_jd_img_v3 (candidate side): Interested Role → /apply, Not
        Interested Role → /p-apply — dispatches through flow_engine, same as
        the old quick-reply buttons (see Path 1 in _handle_candidate_reply).
      • client_reachout_3 (client side): View Profile → /client-onboard —
        advances the client straight to 'interested', same as the old
        quick-reply "View Profiles" handling (see Path 1b in
        _handle_client_reply), but the URL carries client_id in ?ref= so no
        phone-based lookup is needed for the primary path.
    """
    import re as _re

    print(f"[msg91-click] RAW: {_json.dumps(payload, default=str)}")

    link = (payload.get("link") or "").strip()
    recipient = _re.sub(r'\D', '', payload.get("customerNumber") or "")
    if len(recipient) == 12 and recipient.startswith("91"):
        recipient = recipient[2:]

    if not link or not recipient:
        print(f"[msg91-click] missing link or recipient — link={link!r} recipient={recipient!r}")
        return

    if "/client-onboard" in link:
        await _process_client_reachout_click(link, recipient)
        return

    if "/p-apply" in link:
        trigger = "NOT_INTERESTED_ROLE"
    elif "/apply" in link:
        trigger = "INTERESTED"
    else:
        print(f"[msg91-click] unrecognised link, ignoring: {link}")
        return

    pool = get_pool()
    async with pool.acquire() as conn:
        try:
            # Phone-based fallback match, same reasoning as delivery-status
            # matching below — bulk JD sends never get a stored meta_msg_id.
            row = await conn.fetchrow(
                """
                SELECT mapping_id FROM whatsapp_messages
                WHERE RIGHT(phone, 10) = RIGHT($1, 10)
                  AND used_template_name = $2
                  AND direction = 'outbound'
                  AND mapping_id IS NOT NULL
                ORDER BY sent_at DESC LIMIT 1
                """,
                recipient, msg91.JD_V3_TEMPLATE,
            )
            if not row or not row["mapping_id"]:
                print(f"[msg91-click] no matching {msg91.JD_V3_TEMPLATE} send found for {recipient}")
                return

            mapping_id = row["mapping_id"]
            mapping = await conn.fetchrow(
                "SELECT stage FROM client_candidate_mappings WHERE id = $1", mapping_id
            )
            if not mapping:
                print(f"[msg91-click] mapping not found: {mapping_id}")
                return

            phone_10 = recipient if len(recipient) == 10 else recipient[-10:]
            print(f"[msg91-click] trigger={trigger} stage={mapping['stage']} mapping={mapping_id} link={link}")
            executed = await flow_engine.execute_candidate_flow(
                conn, mapping_id, mapping["stage"], trigger, phone_10
            )
            print(f"[msg91-click] flow executed={executed}")
        except Exception as e:
            print(f"[msg91-click] error: {e}")


async def _process_msg91_event(payload: dict):
    """
    Process a MSG91 delivery event (FAILED / DELIVERED / READ) for client_reachout_2/_3/_new
    and candidate JD-share / re-reachout templates (candidate_share_client_jd, hl_cand_re_r1-3).

    MSG91 payload fields:
      uuid           — message UUID (our meta_msg_id)
      customerNumber — recipient phone (with or without country code)
      templateName   — template name used
      webhookType    — FAILED | DELIVERED | READ | SENT
      reason         — error description or Meta error code (e.g. "131049")
    """
    import json as _json
    import re as _re

    print(f"[msg91-event] RAW: {_json.dumps(payload, default=str)}")

    # MSG91 uses requestId as our meta_msg_id, and eventName for the event type
    msg_uuid     = (payload.get("requestId") or payload.get("uuid") or "").strip() or None
    webhook_type = (payload.get("eventName") or payload.get("webhookType") or "").strip().upper()
    reason       = (payload.get("reason") or "").strip()
    recipient    = _re.sub(r'\D', '', payload.get("customerNumber") or "")
    if len(recipient) == 12 and recipient.startswith("91"):
        recipient = recipient[2:]

    print(f"[msg91-event] type={webhook_type} uuid={msg_uuid} recipient={recipient} reason={reason}")

    # Extract numeric error code from reason string if present
    code_match = _re.search(r'\b(13\d{4}|1[0-9]{5})\b', reason)
    error_code = int(code_match.group(1)) if code_match else 0

    TRANSIENT_CODES = {130429, 133004, 131000, 131026, 131031, 133010}

    pool = get_pool()
    async with pool.acquire() as conn:
        try:
            # Look up the message row — covers client reachout and candidate JD-share /
            # re-reachout templates (the latter never get a meta_msg_id from bulk sends,
            # so phone-based fallback matching is what makes their delivery tracking work)
            row = await conn.fetchrow(
                """
                SELECT id, client_id, candidate_id, phone FROM whatsapp_messages
                WHERE (meta_msg_id = $1 OR ($2 != '' AND RIGHT(phone, 10) = RIGHT($2, 10)))
                  AND used_template_name IN (
                      'client_reachout_2', 'client_reachout_3', 'client_reachout_new',
                      $3, $4, $5, 'hl_cand_re_r1', 'hl_cand_re_r2', 'hl_cand_re_r3'
                  )
                  AND direction = 'outbound'
                ORDER BY sent_at DESC LIMIT 1
                """,
                msg_uuid, recipient, _CANDIDATE_INTENT_TEMPLATE, msg91.JD_V3_TEMPLATE, msg91.FF_TEMPLATE,
            )

            if webhook_type in ("DELIVERED", "READ"):
                status_val = webhook_type.lower()
                if row:
                    await conn.execute(
                        """
                        UPDATE whatsapp_messages SET status = $1 WHERE id = $2
                          AND CASE status WHEN 'sent' THEN 1 WHEN 'delivered' THEN 2 WHEN 'read' THEN 3 ELSE 0 END
                            < CASE $1::text WHEN 'sent' THEN 1 WHEN 'delivered' THEN 2 WHEN 'read' THEN 3 ELSE 0 END
                        """,
                        status_val, row["id"],
                    )
                print(f"[msg91-event] {status_val} → uuid={msg_uuid} row={row['id'] if row else 'not found'}")
                return

            if webhook_type != "FAILED":
                return

            # FAILED — transient or unknown errors: log only, cron retries naturally
            if error_code in TRANSIENT_CODES or error_code == 0:
                print(f"[msg91-event] transient/unknown failure {error_code} for {recipient} — no action")
                return

            # Permanent failure: update status on ANY matching outbound message
            fail_status = f"failed_{error_code}" if error_code else "failed"
            if row:
                await conn.execute(
                    "UPDATE whatsapp_messages SET status = $1 WHERE id = $2",
                    fail_status, row["id"],
                )

            # Message status is already updated above; do NOT DND for any FAILED
            # delivery, including 131049 — that code means Meta itself throttled
            # delivery to protect the recipient from marketing-message overload
            # (a cross-business per-user rate cap), not that the recipient opted
            # out or rejected anything. It is not a client response.
            if row and row["client_id"]:
                print(f"[msg91-event] error {error_code or reason[:40]} → delivery failed for client {row['client_id']} phone {recipient} (not DND'd)")
            else:
                print(f"[msg91-event] FAILED but no matching message row for uuid={msg_uuid} phone={recipient}")

        except Exception as e:
            print(f"[msg91-event] error: {e}")


async def _handle_delivery_status(payload: dict, conn) -> bool:
    """
    Handle MSG91 delivery status webhooks for client_reachout_2 only.
    Returns True if this was a delivery status event (so caller can skip inbound handling).

    FAILED deliveries only update the message status — they never DND the client.
    131049 in particular is Meta's own marketing-frequency throttle, not a client
    opt-out, so it must not be treated as one. Transient failures (rate limit,
    server error, phone off) are ignored entirely — cron retries naturally.
    """
    import json as _json

    # MSG91 delivery events arrive in several shapes — handle all known variants
    # Shape A: flat  { message_uuid, status, error_code, recipient }
    # Shape B: nested { statuses: [{ id, status, errors: [{code, title}] }] }
    # Shape C: MSG91 wraps Meta format { type:"status", ... }

    TRANSIENT_CODES = {130429, 133004, 131000, 131026, 131031, 133010}  # rate-limit, server down, generic, unreachable, WABA-level failure

    import re as _re

    msg_uuid   = None
    status_val = None
    error_code = None
    recipient  = None

    if "message_uuid" in payload:
        # Shape A (direct Meta)
        msg_uuid   = payload.get("message_uuid")
        status_val = payload.get("status", "")
        error_code = payload.get("error_code")
        recipient  = payload.get("recipient", "").lstrip("+")
    elif "statuses" in payload:
        # Shape B (Meta-style nested)
        entry = payload["statuses"][0] if payload["statuses"] else {}
        msg_uuid   = entry.get("id")
        status_val = entry.get("status", "")
        errors     = entry.get("errors") or []
        error_code = errors[0].get("code") if errors else None
        recipient  = entry.get("recipient_id", "").lstrip("+")
    elif "eventName" in payload or "customerNumber" in payload:
        # Shape C (MSG91 native delivery callback)
        # MSG91 uses requestId as our meta_msg_id, and eventName for the event type
        msg_uuid  = (payload.get("requestId") or payload.get("uuid") or "").strip() or None
        wtype     = (payload.get("eventName") or payload.get("webhookType") or "").strip().upper()
        status_val = {"DELIVERED": "delivered", "READ": "read",
                      "FAILED": "failed", "SENT": "sent"}.get(wtype, "")
        reason_str = (payload.get("reason") or "").strip()
        code_match = _re.search(r'\b(13\d{4}|1[0-9]{5})\b', reason_str)
        error_code = int(code_match.group(1)) if code_match else None
        recipient  = _re.sub(r'\D', '', payload.get("customerNumber") or "")
        if len(recipient) == 12 and recipient.startswith("91"):
            recipient = recipient[2:]
    else:
        return False  # not a delivery event

    if status_val not in ("failed", "delivered", "read", "sent"):
        return False

    print(f"[webhook/delivery] uuid={msg_uuid} status={status_val} code={error_code} recipient={recipient}")

    # For delivered/read — only advance status, never downgrade (read > delivered > sent)
    if status_val in ("delivered", "read"):
        if msg_uuid:
            await conn.execute(
                """
                UPDATE whatsapp_messages SET status = $1
                WHERE meta_msg_id = $2 AND direction = 'outbound'
                  AND CASE status WHEN 'sent' THEN 1 WHEN 'delivered' THEN 2 WHEN 'read' THEN 3 ELSE 0 END
                    < CASE $1::text WHEN 'sent' THEN 1 WHEN 'delivered' THEN 2 WHEN 'read' THEN 3 ELSE 0 END
                """,
                status_val, msg_uuid,
            )
        return True

    if status_val != "failed":
        return True

    try:
        error_int = int(error_code) if error_code is not None else 0
    except (ValueError, TypeError):
        error_int = 0

    # Transient failures — let cron retry naturally, no DB change needed
    if error_int in TRANSIENT_CODES or error_int == 0:
        return True

    # Update status on any matching outbound message
    fail_status = f"failed_{error_int}"
    if msg_uuid:
        await conn.execute(
            "UPDATE whatsapp_messages SET status = $1 WHERE meta_msg_id = $2 AND direction = 'outbound'",
            fail_status, msg_uuid,
        )

    # Look up client_reachout row for DND — only reachout failures should DND the client
    row = await conn.fetchrow(
        """
        SELECT id, client_id, phone FROM whatsapp_messages
        WHERE (meta_msg_id = $1 OR phone = $2)
          AND message_type = 'client_reachout'
          AND direction = 'outbound'
        ORDER BY sent_at DESC LIMIT 1
        """,
        msg_uuid,
        recipient or "",
    )
    if not row:
        return True  # not a client reachout — status already updated above, no DND needed

    # Message status is already updated above; do NOT DND for any FAILED delivery,
    # including 131049 — that code means Meta itself throttled delivery to protect
    # the recipient from marketing-message overload (a cross-business per-user
    # rate cap), not that the recipient opted out. It is not a client response.
    print(f"[webhook/delivery] failure {error_int} → client {row['client_id']} phone {row['phone']} (not DND'd)")

    return True


async def _process_webhook(payload: dict):
    """
    MSG91 webhook format (flat object):
      sender            — from phone
      integrated_number — which of our numbers received it
      content_type      — "button" | "text" | "image" | ...
      button.payload    — button payload (when content_type == "button")
      button.text       — button label text
      messages[0].text.body — message text (when content_type == "text")
    """
    import json as _json
    print(f"[webhook] RAW PAYLOAD: {_json.dumps(payload, default=str)}")
    pool = get_pool()
    async with pool.acquire() as conn:
        try:
            # Check for delivery status event first — these have no sender/content_type
            if await _handle_delivery_status(payload, conn):
                return

            from_phone = payload.get("sender", "").lstrip("+")
            our_number = payload.get("integrated_number", "").lstrip("+")
            content_type = payload.get("content_type", "")

            candidate_num = os.environ.get("MSG91_CANDIDATE_WHATSAPP_NUMBER", "").lstrip("+")
            client_num = os.environ.get("MSG91_CLIENT_WHATSAPP_NUMBER", "").lstrip("+")

            text: str = ""
            button_payload: str | None = None

            if content_type == "button":
                btn = payload.get("button", {})
                button_payload = btn.get("payload", "").strip() or None
                text = btn.get("text", "").strip()
            elif content_type == "interactive":
                # Interactive reply — either a reply-button tap (button_reply)
                # or a list-message row tap (list_reply); extract id as payload.
                interactive = payload.get("interactive") or {}
                if not interactive:
                    msgs = payload.get("messages", [])
                    if msgs:
                        interactive = msgs[0].get("interactive", {})
                reply = interactive.get("button_reply") or interactive.get("list_reply") or {}
                button_payload = reply.get("id", "").strip() or None
                text = reply.get("title", "").strip()
            elif content_type == "text":
                msgs = payload.get("messages", [])
                if msgs:
                    text = msgs[0].get("text", {}).get("body", "").strip()

            await conn.execute(
                """
                INSERT INTO whatsapp_messages (message_type, direction, phone, message_text, status)
                VALUES ('inbound_reply', 'inbound', $1, $2, 'received')
                """,
                from_phone, button_payload or text or content_type,
            )

            if not text and not button_payload:
                return

            if our_number == candidate_num:
                await _handle_candidate_reply(conn, from_phone, text, button_payload)
            elif our_number == client_num:
                await _handle_client_reply(conn, from_phone, text, button_payload)

        except Exception as e:
            print(f"[webhook] process_webhook error: {e}")


# ── MSG91 flow intent callback ────────────────────────────────────────────────

@router.post("/intent-callback")
async def intent_callback(
    body: IntentCallbackBody,
    conn: asyncpg.Connection = Depends(get_conn),
):
    """Manual override / MSG91 HTTP callback: advance a mapping through the flow engine."""
    try:
        mapping_id = uuid.UUID(body.mapping_id)
    except ValueError:
        raise HTTPException(400, "Invalid mapping_id")

    mapping = await conn.fetchrow(
        """
        SELECT ccm.stage, c.phone AS candidate_phone
        FROM client_candidate_mappings ccm
        JOIN candidates c ON c.id = ccm.candidate_id
        WHERE ccm.id = $1
        """,
        mapping_id,
    )
    if not mapping:
        raise HTTPException(404, "Mapping not found")

    from services.msg91_service import _format_phone
    phone = _format_phone(mapping["candidate_phone"] or "")

    executed = await flow_engine.execute_candidate_flow(
        conn, mapping_id, mapping["stage"], body.intent, phone
    )
    if not executed:
        raise HTTPException(400, f"No flow step for stage='{mapping['stage']}', trigger='{body.intent}'")
    return {"data": {"mapping_id": str(mapping_id), "trigger": body.intent}, "ok": True}


# ── Templates ─────────────────────────────────────────────────────────────────

@router.get("/templates")
async def list_templates(type: str = Query("client", alias="type")):
    templates = await msg91.get_templates(sender=type)
    return {"data": templates, "count": len(templates), "ok": True}


# ── Pending messages ──────────────────────────────────────────────────────────

@router.get("/pending")
async def list_pending(conn: asyncpg.Connection = Depends(get_conn)):
    rows = await conn.fetch(
        """
        SELECT wm.*,
            cl.company_name AS client_name,
            c.name          AS candidate_name
        FROM whatsapp_messages wm
        LEFT JOIN clients    cl ON cl.id = wm.client_id
        LEFT JOIN candidates c  ON c.id  = wm.candidate_id
        WHERE wm.status = 'pending_review'
        ORDER BY wm.created_at ASC
        """
    )
    return {"data": [dict(r) for r in rows], "count": len(rows), "ok": True}


# ── Client sends ──────────────────────────────────────────────────────────────

@router.post("/send/client-reachout/{client_id}")
async def send_client_reachout(
    client_id: uuid.UUID,
    body: SendTemplateBody,
    conn: asyncpg.Connection = Depends(get_conn),
):
    client = await conn.fetchrow("SELECT * FROM clients WHERE id = $1", client_id)
    if not client:
        raise HTTPException(404, "Client not found")
    if client["is_dnd"]:
        raise HTTPException(400, "Client has opted out (DND) — cannot send")

    phones = _phones_for_client(client)
    if not phones:
        raise HTTPException(400, "Client has no phone numbers")

    components = _build_components(body.variables, body.header_url, body.header_type)
    components.extend([
        {"type": "button", "sub_type": "quick_reply", "index": "0",
         "parameters": [{"type": "payload", "payload": f"INTERESTED|{client_id}"}]},
        {"type": "button", "sub_type": "quick_reply", "index": "1",
         "parameters": [{"type": "payload", "payload": f"NOT_INTERESTED|{client_id}"}]},
    ])
    sent = []
    for phone in phones:
        try:
            result = await msg91.send_template(phone, body.template_name, body.lang, components, sender="client")
            meta_msg_id = _extract_msg_id(result)
            await conn.execute(
                """
                INSERT INTO whatsapp_messages
                    (client_id, message_type, direction, phone, status, meta_msg_id, sent_at)
                VALUES ($1, 'client_reachout', 'outbound', $2, 'sent', $3, now())
                """,
                client_id, phone, meta_msg_id,
            )
            sent.append(phone)
        except Exception:
            pass

    await conn.execute(
        "UPDATE clients SET stage = 'reachout_sent'::client_stage, updated_at = now() WHERE id = $1",
        client_id,
    )
    return {"data": {"sent_to": sent, "count": len(sent)}, "ok": True}


@router.post("/send/client-bulk-reachout")
async def send_client_bulk_reachout(
    body: BulkClientReachoutRequest,
    conn: asyncpg.Connection = Depends(get_conn),
):
    results = []
    for cid_str in body.client_ids:
        try:
            cid = uuid.UUID(cid_str)
            client = await conn.fetchrow("SELECT * FROM clients WHERE id = $1", cid)
            if not client:
                results.append({"client_id": cid_str, "ok": False, "error": "Not found"})
                continue
            if client["is_dnd"]:
                results.append({"client_id": cid_str, "ok": False, "error": "Client has opted out (DND)"})
                continue

            phones = _phones_for_client(client)
            if not phones:
                results.append({"client_id": cid_str, "ok": False, "error": "No phone numbers"})
                continue

            components = _build_components(body.variables, body.header_url, body.header_type)
            components.extend([
                {"type": "button", "sub_type": "quick_reply", "index": "0",
                 "parameters": [{"type": "payload", "payload": f"INTERESTED|{cid}"}]},
                {"type": "button", "sub_type": "quick_reply", "index": "1",
                 "parameters": [{"type": "payload", "payload": f"NOT_INTERESTED|{cid}"}]},
            ])
            sent = []
            for phone in phones:
                try:
                    result = await msg91.send_template(phone, body.template_name, body.lang, components, sender="client")
                    meta_msg_id = _extract_msg_id(result)
                    await conn.execute(
                        """
                        INSERT INTO whatsapp_messages
                            (client_id, message_type, direction, phone, status, meta_msg_id, sent_at)
                        VALUES ($1, 'client_reachout', 'outbound', $2, 'sent', $3, now())
                        """,
                        cid, phone, meta_msg_id,
                    )
                    sent.append(phone)
                except Exception:
                    pass

            await conn.execute(
                "UPDATE clients SET stage = 'reachout_sent'::client_stage, updated_at = now() WHERE id = $1",
                cid,
            )
            results.append({"client_id": cid_str, "ok": True, "sent_to": sent})
        except Exception as e:
            results.append({"client_id": cid_str, "ok": False, "error": str(e)})
    return {"data": results, "count": len(results), "ok": True}


@router.post("/send/client-agreement/{client_id}")
async def send_client_agreement(
    client_id: uuid.UUID,
    conn: asyncpg.Connection = Depends(get_conn),
):
    client = await conn.fetchrow("SELECT * FROM clients WHERE id = $1", client_id)
    if not client:
        raise HTTPException(404, "Client not found")
    if client["is_dnd"]:
        raise HTTPException(400, "Client has opted out (DND) — cannot send")
    if not client["poc_phone"]:
        raise HTTPException(400, "Client has no POC phone number")
    if not client["agreement_url"]:
        raise HTTPException(400, "Generate the agreement PDF first")

    template = WT.CLIENT_AGREEMENT

    # Body variables: {{1}} = poc_name, {{2}} = agreement_url
    # Button payloads encode client_id so webhook knows which client responded
    components = [
        {
            "type": "body",
            "parameters": [
                {"type": "text", "text": client["poc_name"] or client["company_name"]},
                {"type": "text", "text": client["agreement_url"]},
            ],
        },
        {
            "type": "button",
            "sub_type": "quick_reply",
            "index": "0",
            "parameters": [{"type": "payload", "payload": f"AGREED|{client_id}"}],
        },
        {
            "type": "button",
            "sub_type": "quick_reply",
            "index": "1",
            "parameters": [{"type": "payload", "payload": f"NOT_AGREED|{client_id}"}],
        },
    ]

    result = await msg91.send_template(client["poc_phone"], template, "en", components, sender="client")
    meta_msg_id = _extract_msg_id(result)

    await conn.execute(
        """
        INSERT INTO whatsapp_messages
            (client_id, message_type, direction, phone, status, meta_msg_id, sent_at)
        VALUES ($1, 'client_agreement', 'outbound', $2, 'sent', $3, now())
        """,
        client_id, client["poc_phone"], meta_msg_id,
    )
    await conn.execute(
        """UPDATE clients SET stage = 'agreement_sent'::client_stage,
           agreement_sent_at = now(), updated_at = now() WHERE id = $1""",
        client_id,
    )
    return {"data": {"meta_msg_id": meta_msg_id}, "ok": True}


@router.post("/send/client-availability/{client_id}")
async def send_client_availability(
    client_id: uuid.UUID,
    conn: asyncpg.Connection = Depends(get_conn),
):
    client = await conn.fetchrow("SELECT * FROM clients WHERE id = $1", client_id)
    if not client:
        raise HTTPException(404, "Client not found")
    if not client["poc_phone"]:
        raise HTTPException(400, "Client has no POC phone number")

    template = WT.CLIENT_AVAILABILITY
    if not template:
        raise HTTPException(400, "CLIENT_AVAILABILITY_TEMPLATE env var not set")

    result = await msg91.send_template(client["poc_phone"], template, "en", [], sender="client")
    meta_msg_id = _extract_msg_id(result)

    await conn.execute(
        """
        INSERT INTO whatsapp_messages
            (client_id, message_type, direction, phone, status, meta_msg_id, sent_at)
        VALUES ($1, 'client_availability_ask', 'outbound', $2, 'sent', $3, now())
        """,
        client_id, client["poc_phone"], meta_msg_id,
    )
    return {"data": {"meta_msg_id": meta_msg_id}, "ok": True}


# ── Candidate sends ───────────────────────────────────────────────────────────

@router.post("/send/candidate-intent-bulk")
async def send_candidate_intent_bulk(
    body: BulkCandidateIntentRequest,
    conn: asyncpg.Connection = Depends(get_conn),
):
    mapping_uuids = [uuid.UUID(mid) for mid in body.mapping_ids]
    rows = await conn.fetch(
        """
        SELECT ccm.id, ccm.client_id, ccm.candidate_id, c.phone AS candidate_phone,
               c.name AS candidate_name, c.source AS candidate_source,
               c.form_submitted AS candidate_form_submitted, ccm.wa_sent_at
        FROM client_candidate_mappings ccm
        JOIN candidates c ON c.id = ccm.candidate_id
        WHERE ccm.id = ANY($1::uuid[]) AND c.phone IS NOT NULL
        """,
        mapping_uuids,
    )
    if not rows:
        raise HTTPException(400, "No mappings with valid phone numbers found")

    client = await conn.fetchrow("SELECT * FROM clients WHERE id = $1", rows[0]["client_id"])
    if not client:
        raise HTTPException(404, "Client not found")

    # Reuse the canonical component builder so body_1..body_12 line up with the
    # actually-approved hl_cand_jd_share template slots (was previously hand-rolled
    # here with a completely different field order, producing a garbled message).
    from routers.cron import _build_jd_components, _jd_intro, _client_maps_url

    components = _build_jd_components(dict(client))

    # Ensure JD card image exists — generate + persist if not already stored
    jd_image_url: str | None = client["jd_card_url"]
    if not jd_image_url:
        try:
            from services.jd_card_service import generate_jd_card
            jd_image_url = await generate_jd_card(dict(client))
            await conn.execute(
                "UPDATE clients SET jd_card_url = $1 WHERE id = $2",
                jd_image_url, client["id"],
            )
        except Exception as _img_err:
            import traceback
            traceback.print_exc()
            raise HTTPException(500, f"JD card generation failed: {_img_err}")

    maps_url = _client_maps_url(dict(client))
    area = client["job_location"] or client["location"] or ""
    salary = str(int(client["max_salary"])) if client["max_salary"] else ""
    # Form already filled → hl_cand_jd_share_2 (View Details straight into
    # the portal dashboard). Form not yet filled → hl_cand_jd_img_v3 (URL
    # buttons straight to /apply and /p-apply instead of quick-reply payloads).
    # See services/msg91_service.py.
    templates_by_mapping = {
        str(r["id"]): (msg91.FF_TEMPLATE if r["candidate_form_submitted"] else msg91.JD_V3_TEMPLATE)
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
            "area": area,
            "salary": salary,
            "company_name": client["company_name"] or "",
            "role": client["job_title"] or "",
            "template_name": templates_by_mapping[str(r["id"])],
        }
        for r in rows
    ]
    await msg91.send_bulk_intent(candidates, components, image_url=jd_image_url)

    await conn.execute(
        """
        UPDATE client_candidate_mappings
        SET stage = 'intent_ask'::mapping_stage, wa_sent_at = now(), updated_at = now()
        WHERE id = ANY($1::uuid[])
        """,
        mapping_uuids,
    )
    for r in rows:
        await conn.execute(
            """
            INSERT INTO whatsapp_messages
                (candidate_id, mapping_id, message_type, direction, phone, status, sent_at, used_template_name)
            VALUES ($1, $2, 'candidate_intent_ask', 'outbound', $3, 'sent', now(), $4)
            """,
            r["candidate_id"], r["id"], r["candidate_phone"], templates_by_mapping[str(r["id"])],
        )
        await conn.execute("SELECT record_candidate_jd_sent($1, $2)", r["candidate_id"], r["id"])
        await conn.execute(
            """
            INSERT INTO candidate_locks (candidate_id, client_id)
            VALUES ($1, $2)
            ON CONFLICT (candidate_id, client_id)
            DO UPDATE SET locked_at = now(), unlocked_at = NULL, unlock_reason = NULL
            """,
            r["candidate_id"], r["client_id"],
        )

    return {"data": {"sent": len(candidates)}, "ok": True}


@router.post("/send/candidate-intent/{mapping_id}")
async def send_candidate_intent(
    mapping_id: uuid.UUID,
    body: SendTemplateBody,
    conn: asyncpg.Connection = Depends(get_conn),
):
    row = await conn.fetchrow(
        """
        SELECT ccm.id, ccm.candidate_id, ccm.client_id,
               ccm.wa_sent_at, c.phone AS candidate_phone
        FROM client_candidate_mappings ccm
        JOIN candidates c ON c.id = ccm.candidate_id
        WHERE ccm.id = $1
        """,
        mapping_id,
    )
    if not row:
        raise HTTPException(404, "Mapping not found")
    if not row["candidate_phone"]:
        raise HTTPException(400, "Candidate has no phone number")
    if row["wa_sent_at"] is not None:
        raise HTTPException(400, "Candidate has already been contacted for this client")

    components = _build_components(body.variables)
    result = await msg91.send_template(row["candidate_phone"], body.template_name, body.lang, components, sender="candidate")
    meta_msg_id = _extract_msg_id(result)

    await conn.execute(
        """
        INSERT INTO whatsapp_messages
            (candidate_id, mapping_id, message_type, direction, phone, status, meta_msg_id, sent_at, used_template_name)
        VALUES ($1, $2, 'candidate_intent_ask', 'outbound', $3, 'sent', $4, now(), $5)
        """,
        row["candidate_id"], mapping_id, row["candidate_phone"], meta_msg_id, body.template_name,
    )
    await conn.execute("SELECT record_candidate_jd_sent($1, $2)", row["candidate_id"], mapping_id)
    await conn.execute(
        """
        UPDATE client_candidate_mappings
        SET stage = 'intent_ask'::mapping_stage, wa_sent_at = now(), updated_at = now()
        WHERE id = $1
        """,
        mapping_id,
    )
    await conn.execute(
        """
        INSERT INTO candidate_locks (candidate_id, client_id)
        VALUES ($1, $2)
        ON CONFLICT (candidate_id, client_id)
        DO UPDATE SET locked_at = now(), unlocked_at = NULL, unlock_reason = NULL
        """,
        row["candidate_id"], row["client_id"],
    )
    return {"data": {"meta_msg_id": meta_msg_id}, "ok": True}


@router.post("/send/candidate-bulk")
async def send_candidate_bulk(
    body: BulkMessageRequest,
    conn: asyncpg.Connection = Depends(get_conn),
):
    created = []
    for mapping_id in body.mapping_ids:
        row = await conn.fetchrow(
            """
            SELECT ccm.id, ccm.candidate_id, c.phone
            FROM client_candidate_mappings ccm
            JOIN candidates c ON c.id = ccm.candidate_id
            WHERE ccm.id = $1
            """,
            mapping_id,
        )
        if not row:
            continue
        msg = await conn.fetchrow(
            """
            INSERT INTO whatsapp_messages
                (candidate_id, mapping_id, message_type, direction, phone, message_text, status)
            VALUES ($1, $2, 'candidate_bulk', 'outbound', $3, $4, 'pending_review')
            RETURNING *
            """,
            row["candidate_id"], mapping_id, row["phone"], body.message_text,
        )
        created.append(dict(msg))
    return {"data": created, "count": len(created), "ok": True}


@router.post("/approve/{message_id}")
async def approve_message(
    message_id: uuid.UUID,
    conn: asyncpg.Connection = Depends(get_conn),
):
    msg = await conn.fetchrow("SELECT * FROM whatsapp_messages WHERE id = $1", message_id)
    if not msg:
        raise HTTPException(404, "Message not found")
    if msg["status"] != "pending_review":
        raise HTTPException(400, f"Message status is '{msg['status']}', not 'pending_review'")

    result = await msg91.send_text(msg["phone"], msg["message_text"], sender="candidate")
    meta_msg_id = _extract_msg_id(result)

    await conn.execute(
        """
        UPDATE whatsapp_messages
        SET status = 'sent', meta_msg_id = $1, sent_at = now(), approved_at = now()
        WHERE id = $2
        """,
        meta_msg_id, message_id,
    )
    return {"data": {"meta_msg_id": meta_msg_id}, "ok": True}
