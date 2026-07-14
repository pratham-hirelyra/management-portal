"""
Create all new WhatsApp templates for the post-interview lifecycle feature
(postpone/cancel day-before check, two-round interviews, document collection,
offer accept/decline + 6h auto-reject).

Every template here is either purely informational or carries exactly ONE
dynamic URL button into the client-portal or candidate-portal — per the
"WhatsApp is reminder-only" rule, no quick-reply-button decision trees.
The button's {{1}} suffix is filled in per-send by
services.flow_engine.client_portal_deep_link / candidate_portal_deep_link,
same pattern as hl_client_pending_review_v4.

Run: python scripts/create_offer_lifecycle_templates.py
"""
import asyncio
import httpx
import os
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))

GRAPH_URL = "https://graph.facebook.com/v19.0"

CLIENT_WABA_ID    = os.environ["MSG91_CLIENT_WABA_ID"]
CLIENT_TOKEN      = os.environ.get("MSG91_CLIENT_API_TOKEN") or os.environ["WHATSAPP_API_TOKEN"]
CANDIDATE_WABA_ID = os.environ["WHATSAPP_WABA_ID"]
CANDIDATE_TOKEN   = os.environ["WHATSAPP_API_TOKEN"]

CLIENT_PORTAL_URL    = os.environ.get("CLIENT_PORTAL_URL", "https://employer.justaccountants.in").rstrip("/")
CANDIDATE_PORTAL_URL = os.environ.get("CANDIDATE_PORTAL_URL", "https://careers.justaccountants.in").rstrip("/")

SAMPLE_CLIENT_ID    = "9e3dfe9d-cc60-495c-9e33-14d9950d21cb"
SAMPLE_MAPPING_ID   = "8a1f3c2e-4b5d-4e6f-9a0b-1c2d3e4f5a6b"

# (waba_id, token, name, body_text, example_values, button_text | None, button_base_url | None, button_url_example | None)
TEMPLATES = [
    (
        CLIENT_WABA_ID, CLIENT_TOKEN, "hl_client_iv_confirm_v1",
        "Hi {{1}}, you have {{2}} interview(s) scheduled for tomorrow. Please review the details and confirm or make any changes in your hiring portal.",
        ["Rohan Mehta", "2"],
        "Review Tomorrow's Interviews", CLIENT_PORTAL_URL, f"{CLIENT_PORTAL_URL}/requirement/{SAMPLE_CLIENT_ID}/interviews",
    ),
    (
        CANDIDATE_WABA_ID, CANDIDATE_TOKEN, "hl_cand_iv_cancelled_v1",
        "Hi {{1}}, your interview with {{2}} for the {{3}} role has been cancelled. We'll reach out with an update if this opportunity reopens — no action is needed from you right now.",
        ["Rahul Sharma", "ABC Corp", "Senior Accountant"],
        None, None, None,
    ),
    (
        CANDIDATE_WABA_ID, CANDIDATE_TOKEN, "hl_cand_docs_req_v2",
        "Hi {{1}}, great news — {{2}} would like to move forward with you for the {{3}} role. To proceed, please upload a few documents: your last 3 months' salary slips, Aadhaar, and bank statement.",
        ["Rahul Sharma", "ABC Corp", "Senior Accountant"],
        "Upload Your Documents", CANDIDATE_PORTAL_URL, f"{CANDIDATE_PORTAL_URL}/?view=documents&mapping={SAMPLE_MAPPING_ID}",
    ),
    (
        CLIENT_WABA_ID, CLIENT_TOKEN, "hl_client_docs_received_v1",
        "Hi {{1}}, {{2}}'s documents for the {{3}} role have been received. You can view them in your portal — our customer executive will call you shortly to help close this hire.",
        ["Rohan Mehta", "Rahul Sharma", "Senior Accountant"],
        "View Documents & Next Steps", CLIENT_PORTAL_URL, f"{CLIENT_PORTAL_URL}/requirement/{SAMPLE_CLIENT_ID}/offer",
    ),
    (
        CLIENT_WABA_ID, CLIENT_TOKEN, "hl_rm_client_alert_v1",
        "Hi {{1}}, an update on {{2}} needs your attention: {{3}}. Please follow up with the client as soon as possible.",
        ["Priya Shah", "ABC Corp (Senior Accountant)", "Documents for Rahul Sharma have been received — please call to close the position"],
        None, None, None,
    ),
    (
        CANDIDATE_WABA_ID, CANDIDATE_TOKEN, "hl_cand_offer_v1",
        "Hi {{1}}, you've been offered the {{2}} position at {{3}} for ₹{{4}} per month. Please review your offer letter and respond within 6 hours in your portal.",
        ["Rahul Sharma", "Senior Accountant", "ABC Corp", "30,000"],
        "View Offer & Respond", CANDIDATE_PORTAL_URL, f"{CANDIDATE_PORTAL_URL}/?view=offer&mapping={SAMPLE_MAPPING_ID}",
    ),
    (
        CANDIDATE_WABA_ID, CANDIDATE_TOKEN, "hl_cand_offer_expired_v1",
        "Hi {{1}}, your offer window for the {{2}} position at {{3}} has closed since we didn't hear back in time. Wishing you the best in your job search.",
        ["Rahul Sharma", "Senior Accountant", "ABC Corp"],
        None, None, None,
    ),
    (
        CLIENT_WABA_ID, CLIENT_TOKEN, "hl_client_offer_update_v1",
        "Hi {{1}}, there's an update on {{2}} for the {{3}} role. Please check your portal for the details.",
        ["Rohan Mehta", "Rahul Sharma", "Senior Accountant"],
        "View Update", CLIENT_PORTAL_URL, f"{CLIENT_PORTAL_URL}/requirement/{SAMPLE_CLIENT_ID}/offer",
    ),
]


async def create_one(http: httpx.AsyncClient, waba_id, token, name, body, examples, btn_text, btn_base_url, btn_url_example):
    components = [{"type": "BODY", "text": body, "example": {"body_text": [examples]}}]
    if btn_text:
        components.append({
            "type": "BUTTONS",
            "buttons": [{
                "type": "URL",
                "text": btn_text,
                "url": f"{btn_base_url}/{{{{1}}}}",
                "example": [btn_url_example],
            }],
        })
    resp = await http.post(
        f"{GRAPH_URL}/{waba_id}/message_templates",
        json={"name": name, "language": "en", "category": "UTILITY", "components": components},
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    return resp.status_code, resp.json()


async def main():
    print(f"\n{'─'*62}\n  Creating {len(TEMPLATES)} offer-lifecycle WhatsApp templates\n{'─'*62}\n")
    async with httpx.AsyncClient(timeout=30.0) as http:
        for (waba_id, token, name, body, examples, btn_text, btn_base_url, btn_url_example) in TEMPLATES:
            label = "CLIENT   " if waba_id == CLIENT_WABA_ID else "CANDIDATE"
            status, resp = await create_one(http, waba_id, token, name, body, examples, btn_text, btn_base_url, btn_url_example)
            if status in (200, 201) or resp.get("id"):
                print(f"  ✓ [{label}] {name}  (id={resp.get('id', '?')}, status={resp.get('status')})")
            elif resp.get("error", {}).get("error_subcode") == 2388085:
                print(f"  ~ [{label}] {name}  (already exists)")
            else:
                print(f"  ✗ [{label}] {name}  → {resp.get('error', resp)}")
    print(f"\n{'─'*62}\n  Done. Once APPROVED in Meta Business Manager, add to backend/.env:\n{'─'*62}\n")
    print("  INTERVIEW_CONFIRM_TEMPLATE=hl_client_iv_confirm_v1")
    print("  IV_CANCELLED_TEMPLATE=hl_cand_iv_cancelled_v1")
    print("  DOCS_REQUEST_TEMPLATE=hl_cand_docs_req_v2")
    print("  DOCS_RECEIVED_TEMPLATE=hl_client_docs_received_v1")
    print("  RM_ALERT_TEMPLATE=hl_rm_client_alert_v1")
    print("  OFFER_TEMPLATE=hl_cand_offer_v1")
    print("  OFFER_EXPIRED_TEMPLATE=hl_cand_offer_expired_v1")
    print("  CLIENT_OFFER_UPDATE_TEMPLATE=hl_client_offer_update_v1")
    print()


if __name__ == "__main__":
    asyncio.run(main())
