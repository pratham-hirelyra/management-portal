import os
import httpx


async def send_whatsapp_template(phone: str, components: list = None) -> dict:
    return await send_whatsapp_named_template(
        phone,
        template_name=os.environ["WHATSAPP_REACHOUT_TEMPLATE_NAME"],
        template_lang=os.environ["WHATSAPP_REACHOUT_TEMPLATE_LANG"],
        components=components,
    )


async def send_whatsapp_named_template(
    phone: str,
    template_name: str,
    template_lang: str = "en",
    components: list = None,
) -> dict:
    url = f"https://graph.facebook.com/v19.0/{os.environ['WHATSAPP_PHONE_NUMBER_ID']}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": template_lang},
            "components": components or [],
        },
    }
    async with httpx.AsyncClient() as http:
        resp = await http.post(url, headers=_headers(), json=payload)
    return resp.json()


async def send_whatsapp_text(phone: str, text: str) -> dict:
    url = f"https://graph.facebook.com/v19.0/{os.environ['WHATSAPP_PHONE_NUMBER_ID']}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "text",
        "text": {"body": text},
    }
    async with httpx.AsyncClient() as http:
        resp = await http.post(url, headers=_headers(), json=payload)
    return resp.json()


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {os.environ['WHATSAPP_API_TOKEN']}",
        "Content-Type": "application/json",
    }
