"""
Create the "candidate pending review" client-notification template.

This is a business-initiated message (fired when a candidate taps Interested,
not as a reply to anything the client sent us) — per WhatsApp's conversation-
window rules this must be a pre-approved UTILITY template, not free-form text.

The URL button is DYNAMIC — its {{1}} suffix is filled in per-send (see
services.flow_engine.client_portal_deep_link) so the button always opens the
exact portal page the notification is about (Candidate Review, in this case),
not just the portal root. This is the pattern for every future client
notification template that needs to deep-link somewhere specific.

Run: python scripts/create_client_pending_review_template.py
"""
import asyncio
import httpx
import os
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))

GRAPH_URL = "https://graph.facebook.com/v19.0"

CLIENT_WABA_ID = os.environ["MSG91_CLIENT_WABA_ID"]
CLIENT_TOKEN = os.environ.get("MSG91_CLIENT_API_TOKEN") or os.environ["WHATSAPP_API_TOKEN"]
PORTAL_URL = os.environ.get("CLIENT_PORTAL_URL", "https://employer.justaccountants.in").rstrip("/")

TEMPLATE_NAME = "hl_client_pending_review_v4"
BODY_TEXT = "Hi {{1}}, you have {{2}} candidate(s) pending review for the {{3}} role. Please review them soon to keep hiring on track."
EXAMPLE_VALUES = ["Rohan Mehta", "3", "Senior Accountant"]
BUTTON_URL_EXAMPLE = "requirement/9e3dfe9d-cc60-495c-9e33-14d9950d21cb/review"


async def main():
    payload = {
        "name": TEMPLATE_NAME,
        "language": "en",
        "category": "UTILITY",
        "components": [
            {
                "type": "BODY",
                "text": BODY_TEXT,
                "example": {"body_text": [EXAMPLE_VALUES]},
            },
            {
                "type": "BUTTONS",
                "buttons": [
                    {
                        "type": "URL",
                        "text": "Review Now",
                        "url": f"{PORTAL_URL}/{{{{1}}}}",
                        "example": [f"{PORTAL_URL}/{BUTTON_URL_EXAMPLE}"],
                    },
                ],
            },
        ],
    }
    async with httpx.AsyncClient(timeout=20.0) as http:
        resp = await http.post(
            f"{GRAPH_URL}/{CLIENT_WABA_ID}/message_templates",
            json=payload,
            headers={"Authorization": f"Bearer {CLIENT_TOKEN}", "Content-Type": "application/json"},
        )
    body = resp.json()
    if resp.status_code in (200, 201) or body.get("id"):
        print(f"✓ [CLIENT] {TEMPLATE_NAME}  (id={body.get('id', '?')}, status={body.get('status')})")
    elif body.get("error", {}).get("error_subcode") == 2388085:
        print(f"~ [CLIENT] {TEMPLATE_NAME}  (already exists)")
    else:
        print(f"✗ [CLIENT] {TEMPLATE_NAME}  → {body.get('error', body)}")

    print()
    print("Once APPROVED in Meta Business Manager, add to backend/.env:")
    print(f"  CLIENT_PENDING_REVIEW_TEMPLATE={TEMPLATE_NAME}")


if __name__ == "__main__":
    asyncio.run(main())
