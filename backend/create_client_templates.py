"""
Create all client-side WhatsApp templates in the new WABA.
Reads WABA ID and API token from .env.
"""
import asyncio, json, os, httpx
from dotenv import load_dotenv

load_dotenv()

WABA_ID   = os.environ["MSG91_CLIENT_WABA_ID"]
API_TOKEN = os.environ["MSG91_CLIENT_API_TOKEN"]
BASE_URL  = "https://graph.facebook.com/v19.0"

# ── Client templates to create ────────────────────────────────────────────────
TEMPLATES = [
    {
        "name": "client_reachout_new",
        "category": "UTILITY",
        "language": "en",
        "components": [
            {
                "type": "BODY",
                "text": "We noticed that you may be looking to hire an Accountant. This is a follow-up regarding that requirement.\n\nCan you please confirm the current status."
            },
            {
                "type": "BUTTONS",
                "buttons": [
                    {"type": "QUICK_REPLY", "text": "Position Open"},
                    {"type": "QUICK_REPLY", "text": "Not Required"}
                ]
            }
        ]
    },
    {
        "name": "client_reachout_1",
        "category": "MARKETING",
        "language": "en",
        "components": [
            {
                "type": "BODY",
                "text": "Click on the below button to get started"
            },
            {
                "type": "BUTTONS",
                "buttons": [
                    {"type": "QUICK_REPLY", "text": "Interested"},
                    {"type": "QUICK_REPLY", "text": "Not Interested"}
                ]
            }
        ]
    },
    {
        "name": "client_send_agreement",
        "category": "UTILITY",
        "language": "en",
        "components": [
            {
                "type": "BODY",
                "text": "Hi {{1}},\n\nYour recruitment agreement with JustAccountants is ready for review.\n  \nTap below to view the full terms and confirm your details.\n{{2}}\n  \nOnce confirmed, we'll assign your dedicated RM and start sharing candidate profiles.\n\nTeam JustAccountants",
                "example": {"body_text": [["Pratham", "https://storage.googleapis.com/hirelyra-clients/R_Kumar_1/R_Kumar_1.pdf"]]}
            }
        ]
    },
    {
        "name": "client_onboarding_1",
        "category": "UTILITY",
        "language": "en",
        "components": [
            {
                "type": "BODY",
                "text": "Thank you for your interest in *Just Accountants* 🙏\n\nWe're excited to help you find the right accountant for your business.\n\nLink: {{1}}\nPlease fill out your hiring requirements using the link above",
                "example": {"body_text": [["https://www.justaccountants.in/client-onboard"]]}
            }
        ]
    },
    {
        "name": "client_onboarding_r1",
        "category": "UTILITY",
        "language": "en",
        "components": [
            {
                "type": "BODY",
                "text": "Hi,\n\nWe noticed your hiring requirement form is still pending. To begin shortlisting candidates, please take 2 minutes to complete it:\n{{1}}\n\nWe look forward to hearing from you.",
                "example": {"body_text": [["https://portal.justaccountants.in/client-onboard"]]}
            }
        ]
    },
    {
        "name": "client_onboarding_r2",
        "category": "UTILITY",
        "language": "en",
        "components": [
            {
                "type": "BODY",
                "text": "Hi,\n\nFollowing up on the hiring form which is still incomplete. Without this, we are unable to begin the search for a qualified candidate on your behalf:\n{{1}}\n\nPlease complete it at your earliest.",
                "example": {"body_text": [["https://portal.justaccountants.in/client-onboard"]]}
            }
        ]
    },
    {
        "name": "hl_ob_r3",
        "category": "UTILITY",
        "language": "en",
        "components": [
            {
                "type": "BODY",
                "text": "Hi,\n\nThis is our final reminder to complete the hiring requirement form:\n{{1}}\n\nIf you no longer need our assistance, no action is needed.",
                "example": {"body_text": [["https://portal.justaccountants.in/client-onboard"]]}
            }
        ]
    },
    {
        "name": "client_share_rm_details",
        "category": "UTILITY",
        "language": "en",
        "components": [
            {
                "type": "BODY",
                "text": "Hi {{1}},\n\nThank you for confirming your agreement with JustAccountants! 🎉\n\nSit back and relax while we find matching profiles for you.\nHere are the details for your dedicated Relationship Manager:\n\n- *Name*: {{2}}\n- *Phone*: {{3}}\n\nFeel free to reach out to them directly with any questions moving forward.\n\nWelcome aboard!\nTeam JustAccountants",
                "example": {"body_text": [["Pratham", "Abhishek Shah", "9725549164"]]}
            }
        ]
    },
    {
        "name": "client_select_interview_time_slot",
        "category": "UTILITY",
        "language": "en",
        "components": [
            {
                "type": "BODY",
                "text": "Please share 2–3 preferred dates and time slots for the interview.\n\nYou can use the link below to submit your availability:\n{{1}}\n\nWe will coordinate accordingly and share the confirmation update.",
                "example": {"body_text": [["https://calendly.com"]]}
            }
        ]
    },
    {
        "name": "client_agreement_confirmed",
        "category": "UTILITY",
        "language": "en",
        "components": [
            {
                "type": "BODY",
                "text": "Your recruitment agreement with JustAccountants has been confirmed. 📄\n\nPlease find your agreement attached for your records.\n\nIf you need any assistance during your hiring journey, feel free to reach out to your RM directly.\n\nTeam JustAccountants"
            }
        ]
    },
    {
        "name": "client_interview_confirmed",
        "category": "UTILITY",
        "language": "en",
        "components": [
            {
                "type": "BODY",
                "text": "Hi {{1}}, an interview has been scheduled for {{2}} on {{3}}.\n\nView {{2}}'s profile and all your pipeline candidates here:\n{{4}}\n\nLet us know if you need to reschedule.\n\nTeam JustAccountants",
                "example": {"body_text": [["Rajesh", "Amit Sharma", "Wednesday, 28 May 2026 · 10:00 AM – 11:00 AM", "https://application.justaccountants.in/handover/abc123"]]}
            }
        ]
    },
    {
        "name": "hl_client_slots_received",
        "category": "UTILITY",
        "language": "en",
        "components": [
            {
                "type": "BODY",
                "text": "Hi {{1}}, thank you for sharing your availability! 🙏\n\nHere are the slots you've shared:\n{{2}}\n\nWe'll confirm the interview schedule with the candidates and get back to you shortly.",
                "example": {"body_text": [["Rohan Mehta", "Mon 9 Jun 10am, Mon 9 Jun 2pm"]]}
            }
        ]
    },
    {
        "name": "hl_client_search_started",
        "category": "UTILITY",
        "language": "en",
        "components": [
            {
                "type": "BODY",
                "text": "Hi {{1}}! 👋\n\nThank you for onboarding with JustAccountants.\n\nWe have started shortlisting accountants that match your exact requirements and will share the candidate profiles with you within 48 hours.\n\nStay tuned!\nJustAccountants Team",
                "example": {"body_text": [["Rohan Mehta"]]}
            }
        ]
    },
    {
        "name": "hl_agr_r1",
        "category": "UTILITY",
        "language": "en",
        "components": [
            {
                "type": "BODY",
                "text": "Hi {{1}},\n\nThe service agreement for the {{2}} placement has been shared with you. Kindly review and confirm your acceptance so we can begin shortlisting candidates for you.",
                "example": {"body_text": [["Rahul", "Accountant"]]}
            }
        ]
    },
    {
        "name": "hl_agr_r2",
        "category": "UTILITY",
        "language": "en",
        "components": [
            {
                "type": "BODY",
                "text": "Hi {{1}},\n\nFollowing up on the service agreement for {{2}}. Your confirmation is needed before we can proceed with the placement. Please review and accept at your earliest convenience.",
                "example": {"body_text": [["Rahul", "Accountant"]]}
            }
        ]
    },
    {
        "name": "hl_agr_r3",
        "category": "UTILITY",
        "language": "en",
        "components": [
            {
                "type": "BODY",
                "text": "Hi {{1}},\n\nThis is a final reminder regarding the pending service agreement for {{2}}. Please confirm your acceptance to move forward. For any concerns about the terms, feel free to reach out directly.",
                "example": {"body_text": [["Rahul", "Accountant"]]}
            }
        ]
    },
    {
        "name": "hl_slot_r1",
        "category": "UTILITY",
        "language": "en",
        "components": [
            {
                "type": "BODY",
                "text": "Hi {{1}},\n\nWe are waiting on your available interview slots for the {{2}} position. A shortlisted candidate is ready to schedule. Please share your preferred dates and times at your earliest convenience.",
                "example": {"body_text": [["Rahul", "Accountant"]]}
            }
        ]
    },
    {
        "name": "hl_slot_r2",
        "category": "UTILITY",
        "language": "en",
        "components": [
            {
                "type": "BODY",
                "text": "Hi {{1}},\n\nQuick follow-up - we have not received your interview availability for {{2}} yet. Please share 2-3 preferred time slots so we can confirm the interview with the candidate.",
                "example": {"body_text": [["Rahul", "Accountant"]]}
            }
        ]
    },
    {
        "name": "hl_slot_r3",
        "category": "UTILITY",
        "language": "en",
        "components": [
            {
                "type": "BODY",
                "text": "Hi {{1}},\n\nThis is our last reminder for interview slots for the {{2}} position. To avoid losing the shortlisted candidate, please share your availability as soon as possible.",
                "example": {"body_text": [["Rahul", "Accountant"]]}
            }
        ]
    },
]


async def create_template(http: httpx.AsyncClient, tpl: dict) -> dict:
    resp = await http.post(
        f"{BASE_URL}/{WABA_ID}/message_templates",
        params={"access_token": API_TOKEN},
        json=tpl,
    )
    return {"name": tpl["name"], "status": resp.status_code, "body": resp.json()}


async def main():
    print(f"Creating {len(TEMPLATES)} client templates in WABA {WABA_ID}\n")
    created = skipped = failed = 0

    async with httpx.AsyncClient(timeout=30) as http:
        for tpl in TEMPLATES:
            result = await create_template(http, tpl)
            body = result["body"]
            if result["status"] == 200:
                tid = body.get("id", "")
                print(f"  ✓ {tpl['name']} → id={tid}")
                created += 1
            else:
                err = body.get("error", {})
                code = err.get("code")
                msg  = err.get("message", str(body))
                # 2388085 = template name already exists
                if code == 2388085 or "already exists" in msg.lower():
                    print(f"  ~ {tpl['name']} → already exists (skipped)")
                    skipped += 1
                else:
                    print(f"  ✗ {tpl['name']} → [{result['status']}] {msg}")
                    failed += 1

    print(f"\nDone — created: {created}, skipped (exists): {skipped}, failed: {failed}")


if __name__ == "__main__":
    asyncio.run(main())
