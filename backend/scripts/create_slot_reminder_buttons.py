"""
Converts the slot-selection reminders (r1/r2/r3) from an embedded-link body
to a URL button, matching the round-1/round-2 invite templates (v4) — every
interview-slot-booking message now uses the same button pattern.

Run: python scripts/create_slot_reminder_buttons.py

After Meta approves, add to backend/.env and routers/cron.py:
  (env unaffected — reminders are driven by cron.py's _CAND_SLOT_TEMPLATES list)
  _CAND_SLOT_TEMPLATES = ["hl_cand_slot_r1_v4", "hl_cand_slot_r2_v4", "hl_cand_slot_r3_v4"]
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
SAMPLE_CANDIDATE_ID  = "8a1f3c2e-4b5d-4e6f-9a0b-1c2d3e4f5a6b"

TEMPLATES = [
    (
        "hl_cand_slot_r1_v4",
        "Hi {{1}}, reminder — your interview slot selection for the {{2}} role at {{3}} is still pending.",
        ["Rahul Sharma", "Senior Accountant", "ABC Corp"],
        "Confirm Slot",
    ),
    (
        "hl_cand_slot_r2_v4",
        "Hi {{1}}, we still need your interview slot confirmation for the {{2}} role at {{3}}. Please confirm soon.",
        ["Rahul Sharma", "Senior Accountant", "ABC Corp"],
        "Confirm Slot",
    ),
    (
        "hl_cand_slot_r3_v4",
        "Hi {{1}}, final reminder — your interview opportunity with {{3}} for {{2}} will expire soon. Please confirm immediately.",
        ["Rahul Sharma", "Senior Accountant", "ABC Corp"],
        "Confirm Slot",
    ),
]


async def create_one(http, name, body, examples, btn_text):
    components = [
        {"type": "BODY", "text": body, "example": {"body_text": [examples]}},
        {"type": "BUTTONS", "buttons": [{
            "type": "URL", "text": btn_text,
            "url": f"{CANDIDATE_PORTAL_URL}/{{{{1}}}}",
            "example": [f"{CANDIDATE_PORTAL_URL}/c/{SAMPLE_CANDIDATE_ID}"],
        }]},
    ]
    resp = await http.post(
        f"{GRAPH_URL}/{CANDIDATE_WABA_ID}/message_templates",
        json={"name": name, "language": "en", "category": "UTILITY", "components": components},
        headers={"Authorization": f"Bearer {CANDIDATE_TOKEN}", "Content-Type": "application/json"},
    )
    return resp.status_code, resp.json()


async def main():
    print(f"\n{'─'*62}\n  Creating {len(TEMPLATES)} button-based slot reminders\n{'─'*62}\n")
    async with httpx.AsyncClient(timeout=30.0) as http:
        for (name, body, examples, btn_text) in TEMPLATES:
            status, resp = await create_one(http, name, body, examples, btn_text)
            if status in (200, 201) or resp.get("id"):
                print(f"  ✓ {name}  (id={resp.get('id', '?')}, status={resp.get('status')})")
            elif resp.get("error", {}).get("error_subcode") == 2388085:
                print(f"  ~ {name}  (already exists)")
            else:
                print(f"  ✗ {name}  → {resp.get('error', resp)}")

        print(f"\n{'─'*62}\n  Verifying category assigned\n{'─'*62}\n")
        await asyncio.sleep(2)
        list_resp = await http.get(
            f"{GRAPH_URL}/{CANDIDATE_WABA_ID}/message_templates",
            params={"limit": 250, "fields": "name,category,status"},
            headers={"Authorization": f"Bearer {CANDIDATE_TOKEN}"},
        )
        by_name = {t["name"]: t for t in list_resp.json().get("data", [])}
        for (name, *_rest) in TEMPLATES:
            t = by_name.get(name)
            if t:
                flag = "✓" if t["category"] == "UTILITY" else "✗ STILL " + t["category"]
                print(f"  {flag}  {name}  status={t['status']}")
            else:
                print(f"  ?  {name}  (not found yet)")


if __name__ == "__main__":
    asyncio.run(main())
