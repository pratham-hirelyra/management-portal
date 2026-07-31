"""
Create the two templates needed for the post-AI-call hard-filter reorder
(hard filters now run AFTER the AI call, not before — see
routers/ringg.py:_apply_evaluation_result and services/flow_engine.py:
decline_for_filter_mismatch):

1. hl_cand_not_fit_reason_v1 — Interested Role path, AI eval passed but the
   candidate fails the client's hard filter. {{3}} is always a generic,
   candidate-safe category phrase produced by
   services/flow_engine.py:_safe_decline_reason_phrase — NEVER the raw
   filter_service.py reason string, which can include religion/gender
   rejection language and exact internal salary numbers.

2. hl_cand_eval_pass_generic_v1 — Not Interested Role / organic path, AI eval
   passed with no specific client's role in play, so no hard filter check
   applies — just a generic "completed technical round, sharing matching
   opportunities" ack.

Meta templates are immutable once approved — this only creates them if they
don't already exist (safe to re-run).

Run: python scripts/create_eval_completion_templates.py

Once APPROVED, set in backend/.env AND Cloud Run:
  CANDIDATE_NOT_FIT_CLIENT_REASON_TEMPLATE=hl_cand_not_fit_reason_v1
  CANDIDATE_EVAL_PASS_GENERIC_TEMPLATE=hl_cand_eval_pass_generic_v1
"""
import asyncio
import httpx
import os
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))

GRAPH_URL = "https://graph.facebook.com/v19.0"

CANDIDATE_WABA_ID = os.environ["WHATSAPP_WABA_ID"]
CANDIDATE_TOKEN = os.environ["WHATSAPP_API_TOKEN"]

TEMPLATES = [
    {
        "name": "hl_cand_not_fit_reason_v1",
        "body": (
            "Hi {{1}}, thank you for completing our AI Technical Round! 🙏\n\n"
            "After reviewing your profile against {{2}}'s requirements, we found a mismatch "
            "around {{3}}.\n\n"
            "We'll keep your profile on file and reach out as soon as a more suitable "
            "opportunity comes up.\n\n"
            "Best of luck! 😊"
        ),
        "examples": ["Rahul Sharma", "ABC Corp", "the salary expectations for this role"],
    },
    {
        "name": "hl_cand_eval_pass_generic_v1",
        "body": (
            "Hi {{1}},\n\n"
            "Congratulations! 🎉 You've successfully completed our AI Technical Round.\n\n"
            "We'll now share relevant job opportunities that match your requirements and "
            "experience with you.\n\n"
            "Abhishek Shah\nJustAccountants"
        ),
        "examples": ["Rahul Sharma"],
    },
]


async def main():
    async with httpx.AsyncClient(timeout=30.0) as http:
        for t in TEMPLATES:
            components = [
                {"type": "BODY", "text": t["body"], "example": {"body_text": [t["examples"]]}},
            ]
            resp = await http.post(
                f"{GRAPH_URL}/{CANDIDATE_WABA_ID}/message_templates",
                json={"name": t["name"], "language": "en", "category": "UTILITY", "components": components},
                headers={"Authorization": f"Bearer {CANDIDATE_TOKEN}", "Content-Type": "application/json"},
            )
            data = resp.json()
            if resp.status_code in (200, 201) or data.get("id"):
                print(f"  ✓ {t['name']}  (id={data.get('id', '?')}, status={data.get('status')})")
            elif data.get("error", {}).get("error_subcode") == 2388085:
                print(f"  ~ {t['name']}  (already exists)")
            else:
                print(f"  ✗ {t['name']}  → {data.get('error', data)}")

    print("\nOnce APPROVED, set in backend/.env AND Cloud Run:")
    print("  CANDIDATE_NOT_FIT_CLIENT_REASON_TEMPLATE=hl_cand_not_fit_reason_v1")
    print("  CANDIDATE_EVAL_PASS_GENERIC_TEMPLATE=hl_cand_eval_pass_generic_v1")


if __name__ == "__main__":
    asyncio.run(main())
