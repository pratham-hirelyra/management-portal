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


# hl_cand_jd_img_v3 swaps the Interested/Not-Interested quick-replies for URL
# buttons routed through MSG91's own Smart URL Shortener (m.9m.io) — the {{1}}
# parameter must be the real destination URL, which MSG91 rewrites into a
# tracked m.9m.io/... link at send time; a bare path segment gets rejected
# with "Invalid URL in Button Component". Not-Interested points at /p-apply,
# a page that doesn't exist on the frontend yet.
JD_V3_TEMPLATE = "hl_cand_jd_img_v3"
_CANDIDATE_PORTAL_URL = os.environ.get("CANDIDATE_PORTAL_URL", os.environ.get("FRONTEND_URL", "http://localhost:5174")).rstrip("/")

# Re-reachout templates that share JD_V3_TEMPLATE's exact body/button shape
# (submitted 2026-07-31 to replace the old quick-reply-only hl_cand_re_r*_img
# templates, which have no way to link out at all) — must get the same URL
# buttons, not the plain quick-reply fallback below.
_URL_BUTTON_TEMPLATES = {JD_V3_TEMPLATE, "hl_cand_re_r1_v3b", "hl_cand_re_r2_v3", "hl_cand_re_r3_v3b"}


def _jd_v3_apply_url(mapping_id: str) -> str:
    """Job-specific /apply link — ?job_id=<mapping_id> lets the form know which
    client's hard filters to check on submission (see routers/candidates.py:
    apply_candidate). Query param only, read client-side on page load; the
    actual submission stays a POST, same as before — see the ApplyForm/ApplyPage
    handling in candidate-portal."""
    return f"{_CANDIDATE_PORTAL_URL}/apply?job_id={mapping_id}"


def _jd_v3_p_apply_url(mapping_id: str) -> str:
    return f"{_CANDIDATE_PORTAL_URL}/p-apply?job_id={mapping_id}"


# hl_cand_jd_share_5 — sent to candidates who've already filled the form
# (supersedes hl_cand_jd_share_2 — same buttons, body gained a Role field).
# STATUS ON META: PENDING as of 2026-07-29 — sends will fail until approved.
# No header image, 5 body vars (name, company_name, job_title/role,
# salary-upto, area/location — positional {{1}}/{{2}}/{{3}}/{{4}}/{{5}}).
# 3 buttons: View Details (URL, dynamic, m.9m.io/{{1}} pattern) straight into
# the portal dashboard (not job-specific — candidate reviews all their
# opportunities there once logged in), Update Profile (URL, dynamic, same
# pattern, deep-links to the portal's Profile tab via ?view=profile), and Not
# Looking for Job (quick-reply, same as before).
FF_TEMPLATE = "hl_cand_jd_share_5"
_FF_VIEW_PATH = os.environ.get("CANDIDATE_INTENT_FF_VIEW_URL", f"{_CANDIDATE_PORTAL_URL}/")
_FF_PROFILE_PATH = os.environ.get("CANDIDATE_INTENT_FF_PROFILE_URL", f"{_CANDIDATE_PORTAL_URL}/?view=profile")


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

    candidates: [{"phone": "91xxx", "mapping_id": "uuid-str", "name": "Rahul", "intro": "...",
                  "template_name": "..."}, ...]  — per-candidate "template_name" overrides
                 the batch-level template_name (used to mix hl_cand_jd_img / hl_cand_jd_img_v3
                 in the same send based on candidate.form_submitted).
    body_components: shared body_1..body_12 dict
    template_name: override template (defaults to CANDIDATE_INTENT_TEMPLATE env var)
    image_url: optional GCS URL to set as the template image header
    """
    import asyncio as _asyncio
    import json as _json
    _default_intro = "I am sharing the job details for your review:"
    _batch_tmpl_name = template_name or os.environ.get("CANDIDATE_INTENT_TEMPLATE", "candidate_share_client_jd")

    def _resolve_tmpl(c: dict) -> str:
        return c.get("template_name") or _batch_tmpl_name

    # FF_TEMPLATE never needs a header image; JD_V3_TEMPLATE and the old
    # default (hl_cand_jd_img) both do — only require image_url if at least
    # one candidate in this batch actually resolves to one of those.
    _non_ff_templates = [_resolve_tmpl(c) for c in candidates if _resolve_tmpl(c) != FF_TEMPLATE]
    _any_needs_image = bool(_non_ff_templates) and (
        image_url is not None
        or _batch_tmpl_name.endswith(("_img", "_v3"))
        or any(t.endswith(("_img", "_v3")) for t in _non_ff_templates)
    )
    _img_mode = _any_needs_image or any(_resolve_tmpl(c) == FF_TEMPLATE for c in candidates)

    if _any_needs_image and not image_url:
        raise ValueError("image_url is required for img-variant templates but was not provided")

    # MSG91 bulk to_and_components does not support image headers (and
    # FF_TEMPLATE has no header at all) — send each candidate individually,
    # branching per-candidate on its own resolved template name so a single
    # batch can freely mix form-filled (FF_TEMPLATE) and not-yet-filled
    # (JD_V3_TEMPLATE) candidates for the same client.
    if _img_mode:
        sent = 0
        for c in candidates:
            mid = c["mapping_id"]
            _tmpl_name = _resolve_tmpl(c)

            if _tmpl_name == FF_TEMPLATE:
                components = [
                    {"type": "body", "parameters": [
                        {"type": "text", "text": c.get("name") or ""},
                        {"type": "text", "text": c.get("company_name") or ""},
                        {"type": "text", "text": c.get("role") or ""},
                        {"type": "text", "text": c.get("salary") or ""},
                        {"type": "text", "text": c.get("area") or ""},
                    ]},
                    {"type": "button", "sub_type": "url", "index": "0",
                     "parameters": [{"type": "text", "text": _FF_VIEW_PATH}]},
                    {"type": "button", "sub_type": "url", "index": "1",
                     "parameters": [{"type": "text", "text": _FF_PROFILE_PATH}]},
                    {"type": "button", "sub_type": "quick_reply", "index": "2",
                     "parameters": [{"type": "payload", "payload": f"NOT_LOOKING_FOR_JOB|{mid}"}]},
                ]
            else:
                body_params = [
                    {"type": "text", "text": c.get("name") or ""},
                    {"type": "text", "text": (c.get("intro") or _default_intro) + (f" 📍 Location: {c['maps_url']}" if c.get("maps_url") else "")},
                ]
                if _tmpl_name in _URL_BUTTON_TEMPLATES:
                    buttons = [
                        {"type": "button", "sub_type": "url", "index": "0",
                         "parameters": [{"type": "text", "text": _jd_v3_apply_url(mid)}]},
                        {"type": "button", "sub_type": "url", "index": "1",
                         "parameters": [{"type": "text", "text": _jd_v3_p_apply_url(mid)}]},
                        {"type": "button", "sub_type": "quick_reply", "index": "2",
                         "parameters": [{"type": "payload", "payload": f"NOT_LOOKING_FOR_JOB|{mid}"}]},
                    ]
                else:
                    buttons = [
                        {"type": "button", "sub_type": "quick_reply", "index": "0",
                         "parameters": [{"type": "payload", "payload": f"INTERESTED|{mid}"}]},
                        {"type": "button", "sub_type": "quick_reply", "index": "1",
                         "parameters": [{"type": "payload", "payload": f"NOT_INTERESTED_ROLE|{mid}"}]},
                        {"type": "button", "sub_type": "quick_reply", "index": "2",
                         "parameters": [{"type": "payload", "payload": f"NOT_LOOKING_FOR_JOB|{mid}"}]},
                    ]
                components = [
                    {
                        "type": "header",
                        "parameters": [{"type": "image", "image": {"link": image_url}}],
                    },
                    {"type": "body", "parameters": body_params},
                    *buttons,
                ]
            try:
                result = await send_template(c["phone"], _tmpl_name, "en", components, sender="candidate")
                print(f"[send_bulk_intent/img] sent to {c['phone']} using {_tmpl_name}: {result}")
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


async def send_interactive_list(
    phone: str,
    body_text: str,
    rows: list[dict],  # [{"id": "...", "title": "...", "description": "..."}]
    button_label: str = "Select",
    sender: str = "candidate",
) -> dict:
    """Send an interactive list message — same no-template rule as
    send_interactive_buttons, but for >3 options (WhatsApp reply-button
    messages cap at 3; list messages allow up to 10 rows)."""
    payload = {
        "recipient_number": _candidate_phone(phone) if sender == "candidate" else _format_phone(phone),
        "integrated_number": _number(sender),
        "content_type": "interactive",
        "interactive": {
            "type": "list",
            "body": {"text": body_text},
            "action": {
                "button": button_label,
                "sections": [{
                    "rows": [
                        {"id": r["id"], "title": r["title"], **({"description": r["description"]} if r.get("description") else {})}
                        for r in rows
                    ],
                }],
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
