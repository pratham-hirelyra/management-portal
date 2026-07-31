"""
Simple deterministic client-side support bot — replaces the AI customer-
executive agent (services/ai_client_rm_service.py, no longer called from
routers/whatsapp.py) for known clients whose free-text message didn't match
any of the existing deterministic flows.

Entirely inbound-triggered: the client always messages first, so every send
here happens within the 24-hour customer-service window and uses a plain
interactive reply-button message (msg91_service.send_interactive_buttons) —
no WhatsApp template approval needed, unlike outbound-initiated messages.

Flow (all keyed off button id, so no pending-state table is needed):
  1. Free text that doesn't match anything else -> "Are you facing any
     issues with the process?" [Yes]
  2. Tap Yes (CLIENT_ISSUE_YES) -> "Where are you currently stuck?"
     [Interview] [Feedback] [Finalizing Candidate] [Others]
  3. Tap any of those (CLIENT_STUCK_*) -> "A customer support member from
     our team will reach out to you shortly." + notify the RM on WhatsApp
     and Google Chat.
"""

import asyncpg

ISSUE_YES_ID = "CLIENT_ISSUE_YES"

_STUCK_LABELS = {
    "CLIENT_STUCK_INTERVIEW": "Interview Scheduling",
    "CLIENT_STUCK_FEEDBACK": "Feedback",
    "CLIENT_STUCK_FINALIZING": "Finalizing Candidate",
    "CLIENT_STUCK_OTHERS": "Others",
}


async def _find_client(conn: asyncpg.Connection, from_phone: str):
    from routers.whatsapp import _phone_variants
    variants = _phone_variants(from_phone)
    return await conn.fetchrow(
        """
        SELECT id, company_name, poc_name FROM clients
        WHERE poc_phone = ANY($1::text[])
           OR EXISTS (
               SELECT 1 FROM jsonb_array_elements(
                   CASE WHEN jsonb_typeof(COALESCE(phone_numbers,'[]'::jsonb)) = 'array'
                        THEN COALESCE(phone_numbers,'[]'::jsonb) ELSE '[]'::jsonb END
               ) p WHERE COALESCE(p ->> 'number', p #>> '{}') = ANY($1::text[])
           )
        LIMIT 1
        """,
        variants,
    )


async def _log(conn: asyncpg.Connection, client_id, phone: str, text: str) -> None:
    try:
        await conn.execute(
            """
            INSERT INTO whatsapp_messages (client_id, message_type, direction, phone, message_text, status, sent_at)
            VALUES ($1, 'client_support_bot', 'outbound', $2, $3, 'sent', now())
            """,
            client_id, phone, text,
        )
    except Exception as e:
        print(f"[client_support_bot] log failed for {phone}: {e}")


async def handle_free_text(conn: asyncpg.Connection, from_phone: str, text: str) -> None:
    """Entry point for known-client free text that matched no other flow."""
    import services.msg91_service as msg91

    client = await _find_client(conn, from_phone)
    body = "Are you facing any issues with the process?"
    try:
        await msg91.send_interactive_buttons(
            from_phone, body, [{"id": ISSUE_YES_ID, "title": "Yes"}], sender="client",
        )
        await _log(conn, client["id"] if client else None, from_phone, body)
    except Exception as e:
        print(f"[client_support_bot] issue-check send failed for {from_phone}: {e}")


async def handle_button(conn: asyncpg.Connection, from_phone: str, button_payload: str | None, text: str) -> bool:
    """Returns True if this button belongs to the support-bot flow (caller
    should stop processing), False otherwise."""
    import services.msg91_service as msg91

    if button_payload == ISSUE_YES_ID:
        body = "Can you please select one of the issues from the below list?"
        # WhatsApp reply-button messages cap at 3 buttons — 4 options need a
        # list message instead (up to 10 rows).
        rows = [{"id": bid, "title": label} for bid, label in _STUCK_LABELS.items()]
        client = await _find_client(conn, from_phone)
        try:
            await msg91.send_interactive_list(from_phone, body, rows, button_label="Select", sender="client")
            await _log(conn, client["id"] if client else None, from_phone, body)
        except Exception as e:
            print(f"[client_support_bot] stuck-reason send failed for {from_phone}: {e}")
        return True

    if button_payload in _STUCK_LABELS:
        stuck_reason = _STUCK_LABELS[button_payload]
        client = await _find_client(conn, from_phone)
        ack = "A customer support member from our team will reach out to you shortly. 🙏"
        try:
            await msg91.send_text(from_phone, ack, sender="client")
            if client:
                await _log(conn, client["id"], from_phone, ack)
        except Exception as e:
            print(f"[client_support_bot] ack send failed for {from_phone}: {e}")

        if client:
            message = f"Client {client['company_name'] or from_phone} is stuck on: {stuck_reason}. They need a callback."
            try:
                from services.flow_engine import notify_rm_alert
                await notify_rm_alert(client["id"], message, conn)
            except Exception as e:
                print(f"[client_support_bot] notify_rm_alert failed for {from_phone}: {e}")
            try:
                from services.google_chat_service import send_alert
                await send_alert(
                    "Client needs support", message, severity="WARNING",
                    context={"phone": from_phone, "client_id": str(client["id"]), "stuck_on": stuck_reason},
                )
            except Exception as e:
                print(f"[client_support_bot] google chat alert failed for {from_phone}: {e}")
        return True

    return False
