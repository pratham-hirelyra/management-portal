"""
Adds explicit "Action Required" framing to the round-1 and round-2 interview
invite messages, matching the pattern already used on the slot-selection
reminders (hl_cand_slot_r1/2/3_v3). v3/round2_v2 (submitted earlier this
session) deliberately dropped that phrase while chasing a UTILITY
classification fix — now that the reminders prove "Action Required" itself
isn't the trigger (they kept it and still passed), adding it back here.

Run: python scripts/create_slot_select_action_required.py

After Meta approves, add to backend/.env:
  CANDIDATE_SLOT_SELECT_TEMPLATE=hl_cand_slot_select_v4
  CANDIDATE_SLOT_SELECT_ROUND2_TEMPLATE=hl_cand_slot_select_round2_v3
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
        "hl_cand_slot_select_v4",
        "Hi {{1}}, \U0001F3AF Action Required: {{2}} has shortlisted you for the {{3}} role and "
        "would like to schedule an interview. Please confirm your preferred time slot using the "
        "link below.",
        ["Rahul Sharma", "ABC Corp", "Senior Accountant"],
        "Select Interview Slot", CANDIDATE_PORTAL_URL, f"{CANDIDATE_PORTAL_URL}/c/{SAMPLE_CANDIDATE_ID}",
    ),
    (
        "hl_cand_slot_select_round2_v3",
        "Hi {{1}}, \U0001F3AF Action Required: {{2}} would like to schedule a second round interview "
        "for the {{3}} role. Please confirm your preferred time slot here:\n{{4}}\n\nThank you!",
        ["Rahul Sharma", "ABC Corp", "Senior Accountant", f"{CANDIDATE_PORTAL_URL}/c/{SAMPLE_CANDIDATE_ID}"],
        None, None, None,
    ),
]


async def create_one(http, name, body, examples, btn_text, btn_base_url, btn_url_example):
    components = [{"type": "BODY", "text": body, "example": {"body_text": [examples]}}]
    if btn_text:
        components.append({
            "type": "BUTTONS",
            "buttons": [{
                "type": "URL", "text": btn_text,
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
    print(f"\n{'─'*62}\n  Creating {len(TEMPLATES)} 'Action Required' slot-invite templates\n{'─'*62}\n")
    async with httpx.AsyncClient(timeout=30.0) as http:
        for (name, body, examples, btn_text, btn_base_url, btn_url_example) in TEMPLATES:
            status, resp = await create_one(http, name, body, examples, btn_text, btn_base_url, btn_url_example)
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

    print(f"\n{'─'*62}\n  Once APPROVED, add to backend/.env:\n{'─'*62}\n")
    print("  CANDIDATE_SLOT_SELECT_TEMPLATE=hl_cand_slot_select_v4")
    print("  CANDIDATE_SLOT_SELECT_ROUND2_TEMPLATE=hl_cand_slot_select_round2_v3")


if __name__ == "__main__":
    asyncio.run(main())
