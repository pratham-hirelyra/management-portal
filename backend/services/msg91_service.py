import os
import httpx

BASE_URL = "https://api.msg91.com/api/v5/whatsapp"


async def _alert_msg91(phone: str, error: str, msg_type: str = "") -> None:
    try:
        from services.google_chat_service import alert_msg91_failure
        await alert_msg91_failure(phone, error, msg_type)
    except Exception:
        pass

# NOTE: verify endpoint paths and payload format against current MSG91 docs if anything changes.
# MSG91 is a Meta BSP — payload structure mirrors Meta's Cloud API but wrapped.


def _headers() -> dict:
    return {
        "authkey": os.environ["MSG91_AUTH_KEY"],
        "Content-Type": "application/json",
    }


def _format_phone(phone: str) -> str:
    """Ensure 91-prefixed format for India numbers."""
    phone = phone.strip().lstrip("+")
    if len(phone) == 10 and phone.isdigit():
        return "91" + phone
    return phone


def _candidate_phone(phone: str) -> str:
    """Return TEST_PHONE_OVERRIDE if set (routes all candidate messages to one number for testing)."""
    override = os.environ.get("TEST_PHONE_OVERRIDE", "").strip()
    return _format_phone(override) if override else _format_phone(phone)


def _number(sender: str) -> str:
    if sender == "candidate":
        return os.environ.get("MSG91_CANDIDATE_WHATSAPP_NUMBER", "")
    return os.environ.get("MSG91_CLIENT_WHATSAPP_NUMBER", "")


def _waba_id(sender: str) -> str:
    if sender == "client":
        return os.environ.get("MSG91_CLIENT_WABA_ID") or os.environ.get("WHATSAPP_WABA_ID", "")
    return os.environ.get("WHATSAPP_WABA_ID", "")


def _api_token(sender: str) -> str:
    if sender == "client":
        return os.environ.get("MSG91_CLIENT_API_TOKEN") or os.environ.get("WHATSAPP_API_TOKEN", "")
    return os.environ.get("WHATSAPP_API_TOKEN", "")


async def get_templates(sender: str = "client") -> list[dict]:
    waba_id = _waba_id(sender)
    token = _api_token(sender)
    if not waba_id or not token:
        return []
    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            resp = await http.get(
                f"https://graph.facebook.com/v19.0/{waba_id}/message_templates",
                params={"limit": 100},
                headers={"Authorization": f"Bearer {token}"},
            )
        data = resp.json()
        return data.get("data", [])
    except Exception:
        return []


async def send_template(
    phone: str,
    template_name: str,
    lang: str = "en",
    components: list | None = None,
    sender: str = "client",
) -> dict:
    tmpl: dict = {
        "name": template_name,
        "language": {"code": lang},
    }
    if components:
        tmpl["components"] = components

    payload = {
        "integrated_number": _number(sender),
        "content_type": "template",
        "payload": {
            "messaging_product": "whatsapp",
            "to": _candidate_phone(phone) if sender == "candidate" else _format_phone(phone),
            "type": "template",
            "template": tmpl,
        },
    }
    import json as _j
    print(f"[send_template] payload: {_j.dumps(payload)[:800]}")
    async with httpx.AsyncClient(timeout=15.0) as http:
        resp = await http.post(
            f"{BASE_URL}/whatsapp-outbound-message/",
            json=payload,
            headers=_headers(),
        )
    print(f"[send_template] {resp.status_code}: {resp.text[:400]}")
    resp.raise_for_status()
    return resp.json()


async def send_bulk_intent(
    candidates: list[dict],
    body_components: dict,
    template_name: str | None = None,
    image_url: str | None = None,
) -> dict:
    """
    Send a JD template to multiple candidates.
    Each candidate gets unique button payloads encoding their mapping_id so
    we can identify which client-candidate pair they're responding to.

    candidates: [{"phone": "91xxx", "mapping_id": "uuid-str", "name": "Rahul", "intro": "..."}, ...]
    body_components: shared body_1..body_12 dict
    template_name: override template (defaults to CANDIDATE_INTENT_TEMPLATE env var)
    image_url: optional GCS URL to set as the template image header
    """
    import asyncio as _asyncio
    import json as _json
    _default_intro = "I am sharing the job details for your review:"
    _tmpl_name = template_name or os.environ.get("CANDIDATE_INTENT_TEMPLATE", "candidate_share_client_jd")
    _img_mode = image_url is not None or _tmpl_name.endswith("_img")

    if _img_mode and not image_url:
        raise ValueError("image_url is required for img-variant templates but was not provided")

    # MSG91 bulk to_and_components does not support image headers.
    # Use the same individual send_template format that works for client reachout with images.
    if _img_mode:
        sent = 0
        for c in candidates:
            mid = c["mapping_id"]
            components = [
                {
                    "type": "header",
                    "parameters": [{"type": "image", "image": {"link": image_url}}],
                },
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": c.get("name") or ""},
                        {"type": "text", "text": (c.get("intro") or _default_intro) + (f" 📍 Location: {c['maps_url']}" if c.get("maps_url") else "")},
                    ],
                },
                {"type": "button", "sub_type": "quick_reply", "index": "0",
                 "parameters": [{"type": "payload", "payload": f"INTERESTED|{mid}"}]},
                {"type": "button", "sub_type": "quick_reply", "index": "1",
                 "parameters": [{"type": "payload", "payload": f"NOT_INTERESTED_ROLE|{mid}"}]},
                {"type": "button", "sub_type": "quick_reply", "index": "2",
                 "parameters": [{"type": "payload", "payload": f"NOT_LOOKING_FOR_JOB|{mid}"}]},
            ]
            try:
                result = await send_template(c["phone"], _tmpl_name, "en", components, sender="candidate")
                print(f"[send_bulk_intent/img] sent to {c['phone']}: {result}")
                sent += 1
            except Exception as e:
                print(f"[send_bulk_intent/img] failed for {c['phone']}: {e}")
            await _asyncio.sleep(0.3)
        return {"sent": sent}

    to_and_components = []
    for c in candidates:
        per_recipient = dict(body_components)
        per_recipient["body_13"] = {"type": "text", "value": c.get("name") or ""}
        per_recipient["body_14"] = {"type": "text", "value": c.get("intro") or _default_intro}
        per_recipient["button_0"] = {"sub_type": "quick_reply", "type": "payload", "value": f"INTERESTED|{c['mapping_id']}"}
        per_recipient["button_1"] = {"sub_type": "quick_reply", "type": "payload", "value": f"NOT_INTERESTED_ROLE|{c['mapping_id']}"}
        per_recipient["button_2"] = {"sub_type": "quick_reply", "type": "payload", "value": f"NOT_LOOKING_FOR_JOB|{c['mapping_id']}"}
        to_and_components.append({"to": [_candidate_phone(c["phone"])], "components": per_recipient})

    _ns = os.environ.get("CANDIDATE_INTENT_TEMPLATE_NAMESPACE", "")
    template_payload: dict = {
        "name": _tmpl_name,
        "language": {"code": "en", "policy": "deterministic"},
        "to_and_components": to_and_components,
    }
    if _ns:
        template_payload["namespace"] = _ns

    payload = {
        "integrated_number": _number("candidate"),
        "content_type": "template",
        "payload": {
            "messaging_product": "whatsapp",
            "type": "template",
            "template": template_payload,
        },
    }
    print(f"[send_bulk_intent] payload: {_json.dumps(payload, indent=2)[:2000]}")
    async with httpx.AsyncClient(timeout=30.0) as http:
        resp = await http.post(
            f"{BASE_URL}/whatsapp-outbound-message/bulk/",
            json=payload,
            headers=_headers(),
        )
    print(f"[send_bulk_intent] response {resp.status_code}: {resp.text[:500]}")
    if not resp.is_success:
        err = f"MSG91 bulk intent {resp.status_code}: {resp.text}"
        await _alert_msg91("bulk", err, "bulk_intent")
        raise ValueError(err)
    return resp.json()


async def send_bulk_with_buttons(candidates: list[dict], template_name: str, body_params: list[str], namespace: str = "") -> dict:
    """
    Send a button template (with INTERESTED/NOT_INTERESTED_ROLE/NOT_LOOKING_FOR_JOB buttons)
    to multiple candidates. body_params are positional values for {{1}}, {{2}}, ...
    Each candidate dict: {"phone": "91xxx", "mapping_id": "uuid-str"}
    """
    to_and_components = []
    for c in candidates:
        components: dict = {}
        for i, val in enumerate(body_params, start=1):
            components[f"body_{i}"] = {"type": "text", "value": val}
        components["button_0"] = {"sub_type": "quick_reply", "type": "payload", "value": f"NOT_INTERESTED_ROLE|{c['mapping_id']}"}
        components["button_1"] = {"sub_type": "quick_reply", "type": "payload", "value": f"INTERESTED|{c['mapping_id']}"}
        components["button_2"] = {"sub_type": "quick_reply", "type": "payload", "value": f"NOT_LOOKING_FOR_JOB|{c['mapping_id']}"}
        to_and_components.append({"to": [_candidate_phone(c["phone"])], "components": components})

    payload = {
        "integrated_number": _number("candidate"),
        "content_type": "template",
        "payload": {
            "messaging_product": "whatsapp",
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": "en", "policy": "deterministic"},
                "namespace": namespace,
                "to_and_components": to_and_components,
            },
        },
    }
    async with httpx.AsyncClient(timeout=30.0) as http:
        resp = await http.post(
            f"{BASE_URL}/whatsapp-outbound-message/bulk/",
            json=payload,
            headers=_headers(),
        )
    resp.raise_for_status()
    return resp.json()


async def send_document(phone: str, document_url: str, filename: str = "Agreement.pdf", sender: str = "client") -> dict:
    payload = {
        "integrated_number": _number(sender),
        "content_type": "document",
        "payload": {
            "messaging_product": "whatsapp",
            "to": _format_phone(phone),
            "type": "document",
            "document": {
                "link": document_url,
                "filename": filename,
            },
        },
    }
    async with httpx.AsyncClient(timeout=15.0) as http:
        resp = await http.post(
            f"{BASE_URL}/whatsapp-outbound-message/",
            json=payload,
            headers=_headers(),
        )
    resp.raise_for_status()
    return resp.json()


async def send_video(phone: str, video_url: str, caption: str = "", sender: str = "client") -> dict:
    payload = {
        "integrated_number": _number(sender),
        "content_type": "video",
        "recipient_number": _format_phone(phone),
        "attachment_url": video_url,
    }
    if caption:
        payload["caption"] = caption
    async with httpx.AsyncClient(timeout=15.0) as http:
        resp = await http.post(
            f"{BASE_URL}/whatsapp-outbound-message/",
            json=payload,
            headers=_headers(),
        )
    resp.raise_for_status()
    return resp.json()


async def send_interactive_buttons(
    phone: str,
    body_text: str,
    buttons: list[dict],  # [{"id": "YES_INBOUND", "title": "Yes, I'm looking!"}]
    sender: str = "candidate",
) -> dict:
    """Send an interactive reply-button message (no template needed, within 24h window)."""
    payload = {
        "recipient_number": _candidate_phone(phone) if sender == "candidate" else _format_phone(phone),
        "integrated_number": _number(sender),
        "content_type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": body_text},
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": b["id"], "title": b["title"]}}
                    for b in buttons
                ]
            },
        },
    }
    async with httpx.AsyncClient(timeout=15.0) as http:
        resp = await http.post(
            "https://control.msg91.com/api/v5/whatsapp/whatsapp-outbound-message/",
            json=payload,
            headers=_headers(),
        )
    resp.raise_for_status()
    return resp.json()


async def send_text(phone: str, text: str, sender: str = "client") -> dict:
    payload = {
        "integrated_number": _number(sender),
        "content_type": "text",
        "recipient_number": _candidate_phone(phone) if sender == "candidate" else _format_phone(phone),
        "text": text,
    }
    async with httpx.AsyncClient(timeout=15.0) as http:
        resp = await http.post(
            f"{BASE_URL}/whatsapp-outbound-message/",
            json=payload,
            headers=_headers(),
        )
    resp.raise_for_status()
    return resp.json()
