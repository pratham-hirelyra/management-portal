"""
Create the client-portal OTP AUTHENTICATION template on the CLIENT WABA.
Mirrors the hl_cand_otp_v2 template creation in create_templates.py, but targets
MSG91_CLIENT_WABA_ID instead of the candidate WABA.

Note: MSG91_CLIENT_API_TOKEN is unset in .env — msg91_service._api_token("client")
already falls back to WHATSAPP_API_TOKEN in that case, and that token has been
confirmed (empirically, via a real send) to have send access on the client WABA
too, so we use the same fallback here for template creation.

Run: python scripts/create_client_otp_template.py
"""
import asyncio
import httpx
import os
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))

GRAPH_URL = "https://graph.facebook.com/v19.0"

CLIENT_WABA_ID = os.environ["MSG91_CLIENT_WABA_ID"]
CLIENT_TOKEN = os.environ.get("MSG91_CLIENT_API_TOKEN") or os.environ["WHATSAPP_API_TOKEN"]

TEMPLATE_NAME = "hl_client_otp_v1"


async def main():
    payload = {
        "name": TEMPLATE_NAME,
        "language": "en",
        "category": "AUTHENTICATION",
        "components": [
            {"type": "BODY", "add_security_recommendation": True},
            {"type": "FOOTER", "code_expiration_minutes": 10},
            {"type": "BUTTONS", "buttons": [{"type": "OTP", "otp_type": "COPY_CODE", "text": "Copy Code"}]},
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
    print(f"  CLIENT_OTP_TEMPLATE={TEMPLATE_NAME}")


if __name__ == "__main__":
    asyncio.run(main())
