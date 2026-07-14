"""
Client-facing "round 2 interview confirmed" template — distinct from the
existing round-1 client_interview_confirmed template so the client isn't left
wondering whether this is a fresh interview or the second round they
specifically requested via invite_round2.

Run: python scripts/create_round2_confirmed_template.py

After Meta approves, add to backend/.env:
  CLIENT_SLOT_CONFIRMED_ROUND2_TEMPLATE=hl_client_interview_round2_confirmed
"""
import asyncio
import httpx
import os
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))

GRAPH_URL = "https://graph.facebook.com/v19.0"

CLIENT_WABA_ID = os.environ["MSG91_CLIENT_WABA_ID"]
CLIENT_TOKEN   = os.environ.get("MSG91_CLIENT_API_TOKEN") or os.environ["WHATSAPP_API_TOKEN"]

NAME = "hl_client_interview_round2_confirmed"
BODY = (
    "Hi {{1}}! 🎯\n\n"
    "{{2}} has confirmed their *second round* interview slot on {{3}}.\n\n"
    "View details in your portal:\n{{4}}\n\n"
    "Team JustAccountants"
)
EXAMPLES = ["Rohan Mehta", "Rahul Sharma", "Mon 30 Jun 3pm", "https://employer.justaccountants.in"]


async def main():
    components = [{"type": "BODY", "text": BODY, "example": {"body_text": [EXAMPLES]}}]
    async with httpx.AsyncClient(timeout=30.0) as http:
        resp = await http.post(
            f"{GRAPH_URL}/{CLIENT_WABA_ID}/message_templates",
            json={"name": NAME, "language": "en", "category": "UTILITY", "components": components},
            headers={"Authorization": f"Bearer {CLIENT_TOKEN}", "Content-Type": "application/json"},
        )
        status, data = resp.status_code, resp.json()
        if status in (200, 201) or data.get("id"):
            print(f"✓ {NAME}  (id={data.get('id', '?')}, status={data.get('status')})")
        elif data.get("error", {}).get("error_subcode") == 2388085:
            print(f"~ {NAME}  (already exists)")
        else:
            print(f"✗ {NAME}  → {data.get('error', data)}")
    print("\nOnce APPROVED in Meta Business Manager, add to backend/.env:")
    print("  CLIENT_SLOT_CONFIRMED_ROUND2_TEMPLATE=hl_client_interview_round2_confirmed")


if __name__ == "__main__":
    asyncio.run(main())
