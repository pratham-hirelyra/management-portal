"""
Converts the client-facing interview-confirmation messages (round 1 + round 2)
from an embedded-link body to a URL button with a deep link straight into the
Interview Management tab for that requirement, matching the button pattern
used on every candidate-side slot message this session.

Run: python scripts/create_client_interview_confirmed_buttons.py

After Meta approves, add to backend/.env:
  CLIENT_SLOT_CONFIRMED_TEMPLATE=client_interview_confirmed_v2
  CLIENT_SLOT_CONFIRMED_ROUND2_TEMPLATE=hl_client_interview_round2_confirmed_v2
"""
import asyncio
import httpx
import os
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))

GRAPH_URL = "https://graph.facebook.com/v19.0"
CLIENT_WABA_ID = os.environ["MSG91_CLIENT_WABA_ID"]
CLIENT_TOKEN   = os.environ.get("MSG91_CLIENT_API_TOKEN") or os.environ["WHATSAPP_API_TOKEN"]
CLIENT_PORTAL_URL = os.environ.get("CLIENT_PORTAL_URL", "https://employer.justaccountants.in").rstrip("/")
SAMPLE_CLIENT_ID = "9e3dfe9d-cc60-495c-9e33-14d9950d21cb"

# (name, body, examples, button_text, url_example_suffix)
TEMPLATES = [
    (
        "client_interview_confirmed_v2",
        "Hi {{1}}, an interview has been scheduled for {{2}} on {{3}}.\n\nView their profile and "
        "manage your pipeline using the link below.",
        ["Rohan Mehta", "Rahul Sharma", "Mon 30 Jun 3pm"],
        "View Interview",
        f"requirement/{SAMPLE_CLIENT_ID}/interviews",
    ),
    (
        "hl_client_interview_round2_confirmed_v2",
        "Hi {{1}}! \U0001F3AF\n\n{{2}} has confirmed their *second round* interview slot on {{3}}.\n\n"
        "View details using the link below.",
        ["Rohan Mehta", "Rahul Sharma", "Mon 30 Jun 3pm"],
        "View Interview",
        f"requirement/{SAMPLE_CLIENT_ID}/interviews",
    ),
]


async def create_one(http, name, body, examples, btn_text, url_suffix):
    components = [
        {"type": "BODY", "text": body, "example": {"body_text": [examples]}},
        {"type": "BUTTONS", "buttons": [{
            "type": "URL", "text": btn_text,
            "url": f"{CLIENT_PORTAL_URL}/{{{{1}}}}",
            "example": [f"{CLIENT_PORTAL_URL}/{url_suffix}"],
        }]},
    ]
    resp = await http.post(
        f"{GRAPH_URL}/{CLIENT_WABA_ID}/message_templates",
        json={"name": name, "language": "en", "category": "UTILITY", "components": components},
        headers={"Authorization": f"Bearer {CLIENT_TOKEN}", "Content-Type": "application/json"},
    )
    return resp.status_code, resp.json()


async def main():
    print(f"\n{'─'*62}\n  Creating {len(TEMPLATES)} client interview-confirmed button templates\n{'─'*62}\n")
    async with httpx.AsyncClient(timeout=30.0) as http:
        for (name, body, examples, btn_text, url_suffix) in TEMPLATES:
            status, resp = await create_one(http, name, body, examples, btn_text, url_suffix)
            if status in (200, 201) or resp.get("id"):
                print(f"  ✓ {name}  (id={resp.get('id', '?')}, status={resp.get('status')})")
            elif resp.get("error", {}).get("error_subcode") == 2388085:
                print(f"  ~ {name}  (already exists)")
            else:
                print(f"  ✗ {name}  → {resp.get('error', resp)}")

        print(f"\n{'─'*62}\n  Verifying category assigned\n{'─'*62}\n")
        await asyncio.sleep(2)
        list_resp = await http.get(
            f"{GRAPH_URL}/{CLIENT_WABA_ID}/message_templates",
            params={"limit": 250, "fields": "name,category,status"},
            headers={"Authorization": f"Bearer {CLIENT_TOKEN}"},
        )
        by_name = {t["name"]: t for t in list_resp.json().get("data", [])}
        for (name, *_rest) in TEMPLATES:
            t = by_name.get(name)
            if t:
                flag = "✓" if t["category"] == "UTILITY" else "✗ STILL " + t["category"]
                print(f"  {flag}  {name}  status={t['status']}")
            else:
                print(f"  ?  {name}  (not found yet)")

    print(f"\n{'─'*62}\n  Once APPROVED, add to backend/.env:\n{'─'*62}\n")
    print("  CLIENT_SLOT_CONFIRMED_TEMPLATE=client_interview_confirmed_v2")
    print("  CLIENT_SLOT_CONFIRMED_ROUND2_TEMPLATE=hl_client_interview_round2_confirmed_v2")


if __name__ == "__main__":
    asyncio.run(main())
