"""
Create WhatsApp templates for the new "willing to join?" gate between Hire
and document collection (see company_portal.hire_candidate,
candidate_portal.respond_to_join_intent, cron._followup_candidate_documents).

Both templates are candidate-facing, UTILITY, with a single dynamic URL
button into the candidate portal — same pattern as hl_cand_docs_req_v2.

Run: python scripts/create_join_intent_templates.py
"""
import asyncio
import httpx
import os
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))

GRAPH_URL = "https://graph.facebook.com/v19.0"

CANDIDATE_WABA_ID = os.environ["WHATSAPP_WABA_ID"]
CANDIDATE_TOKEN   = os.environ["WHATSAPP_API_TOKEN"]

CANDIDATE_PORTAL_URL = os.environ.get("CANDIDATE_PORTAL_URL", "https://careers.justaccountants.in").rstrip("/")

SAMPLE_MAPPING_ID = "8a1f3c2e-4b5d-4e6f-9a0b-1c2d3e4f5a6b"

TEMPLATES = [
    (
        "hl_cand_join_intent_v1",
        "Hi {{1}}, great news — {{2}} would like to move forward with you for the {{3}} role. "
        "Please confirm you're still willing to join as soon as possible so we can take the next step.",
        ["Rahul Sharma", "ABC Corp", "Senior Accountant"],
        "Confirm Your Interest", CANDIDATE_PORTAL_URL, f"{CANDIDATE_PORTAL_URL}/?view=join-intent&mapping={SAMPLE_MAPPING_ID}",
    ),
    (
        "hl_cand_docs_reminder_v1",
        "Hi {{1}}, a quick reminder to upload your documents for the {{2}} role at {{3}} — "
        "we're still waiting to hear from you so we can move things forward.",
        ["Rahul Sharma", "Senior Accountant", "ABC Corp"],
        "Upload Your Documents", CANDIDATE_PORTAL_URL, f"{CANDIDATE_PORTAL_URL}/?view=documents&mapping={SAMPLE_MAPPING_ID}",
    ),
]


async def create_one(http: httpx.AsyncClient, name, body, examples, btn_text, btn_base_url, btn_url_example):
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
        f"{GRAPH_URL}/{CANDIDATE_WABA_ID}/message_templates",
        json={"name": name, "language": "en", "category": "UTILITY", "components": components},
        headers={"Authorization": f"Bearer {CANDIDATE_TOKEN}", "Content-Type": "application/json"},
    )
    return resp.status_code, resp.json()


async def main():
    print(f"\n{'─'*62}\n  Creating {len(TEMPLATES)} join-intent / docs-reminder templates\n{'─'*62}\n")
    async with httpx.AsyncClient(timeout=30.0) as http:
        for (name, body, examples, btn_text, btn_base_url, btn_url_example) in TEMPLATES:
            status, resp = await create_one(http, name, body, examples, btn_text, btn_base_url, btn_url_example)
            if status in (200, 201) or resp.get("id"):
                print(f"  ✓ {name}  (id={resp.get('id', '?')}, status={resp.get('status')})")
            elif resp.get("error", {}).get("error_subcode") == 2388085:
                print(f"  ~ {name}  (already exists)")
            else:
                print(f"  ✗ {name}  → {resp.get('error', resp)}")
    print(f"\n{'─'*62}\n  Done. Once APPROVED in Meta Business Manager, add to backend/.env:\n{'─'*62}\n")
    print("  JOIN_INTENT_TEMPLATE=hl_cand_join_intent_v1")
    print("  DOCS_REMINDER_TEMPLATE=hl_cand_docs_reminder_v1")
    print()


if __name__ == "__main__":
    asyncio.run(main())
