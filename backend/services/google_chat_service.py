import os
import traceback
import httpx
import google.auth
import google.auth.transport.requests

_SPACE_ID = "spaces/AAQAdKCNf-s"
_SCOPES   = ["https://www.googleapis.com/auth/chat.bot"]
_CHAT_URL = f"https://chat.googleapis.com/v1/{_SPACE_ID}/messages"
_ENV      = os.environ.get("ENV", "production")

# Key file path — only used locally if GOOGLE_APPLICATION_CREDENTIALS is not set
_SA_KEY = os.path.join(os.path.dirname(__file__), "..", "service-account-key.json")


def _get_token() -> str:
    """
    On Cloud Run: uses the attached service account via ADC automatically.
    Locally: uses service-account-key.json (sets GOOGLE_APPLICATION_CREDENTIALS if needed).
    """
    if not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") and os.path.exists(_SA_KEY):
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = os.path.abspath(_SA_KEY)

    creds, _ = google.auth.default(scopes=_SCOPES)
    creds.refresh(google.auth.transport.requests.Request())
    return creds.token


async def send_alert(
    title: str,
    detail: str,
    severity: str = "ERROR",   # ERROR | WARNING | INFO
    context: dict | None = None,
) -> None:
    """Send a formatted alert card to the Google Chat space. Never raises."""
    try:
        icon = {"ERROR": "🔴", "WARNING": "🟡", "INFO": "🔵"}.get(severity, "🔴")
        lines = [
            f"{icon} *[{severity}] {title}*",
            f"```{detail[:2000]}```",
        ]
        if context:
            ctx_lines = "\n".join(f"• *{k}*: `{v}`" for k, v in context.items())
            lines.append(ctx_lines)
        lines.append(f"_Env: {_ENV}_")

        token = _get_token()
        async with httpx.AsyncClient(timeout=8) as http:
            await http.post(
                _CHAT_URL,
                headers={"Authorization": f"Bearer {token}"},
                json={"text": "\n".join(lines)},
            )
    except Exception as e:
        print(f"[google_chat] alert delivery failed: {e}")


async def alert_exception(exc: Exception, endpoint: str = "", extra: dict | None = None) -> None:
    """Convenience wrapper for unhandled exceptions."""
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    ctx = {"endpoint": endpoint} if endpoint else {}
    if extra:
        ctx.update(extra)
    await send_alert(
        title=f"{type(exc).__name__}: {str(exc)[:120]}",
        detail=tb,
        severity="ERROR",
        context=ctx or None,
    )


async def alert_msg91_failure(phone: str, error: str, message_type: str = "") -> None:
    await send_alert(
        title="MSG91 Send Failure",
        detail=error[:500],
        severity="ERROR",
        context={"phone": phone, "type": message_type},
    )


async def alert_cron_failure(cron_name: str, error: str) -> None:
    await send_alert(
        title=f"Cron Failed: {cron_name}",
        detail=error[:500],
        severity="ERROR",
        context={"cron": cron_name},
    )


async def alert_scraper_failure(client_id: str, scraper_type: str, error: str) -> None:
    await send_alert(
        title=f"Scraper Failed: {scraper_type}",
        detail=error[:500],
        severity="ERROR",
        context={"client_id": client_id, "scraper": scraper_type},
    )
