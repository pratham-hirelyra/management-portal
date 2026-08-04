"""
Ops/engineering alerting — every "something broke" signal (msg91 send
failures, cron failures, scraper failures, unhandled exceptions, agent-loop
crashes) becomes a claimable ticket in engineering_queue instead of a
Google Chat card. See services/ticket_service.py::create_engineering_ticket.

Deduped on a fingerprint (category + a stable title) so a flapping
integration doesn't spawn hundreds of tickets — repeat firings just bump
occurrence_count on the same open ticket.
"""
import traceback

from database import get_pool
import services.ticket_service as ticket_service


async def send_alert(
    title: str,
    detail: str,
    severity: str = "ERROR",   # ERROR | WARNING | INFO
    context: dict | None = None,
    category: str = "other",   # 'integration_failure' | 'cron_failure' | 'unhandled_exception' | 'other'
    fingerprint: str | None = None,
) -> None:
    """Open (or bump the occurrence count on) an engineering ticket. Never
    raises — this is called from inside exception handlers, so a failure
    here must not cascade. Acquires its own connection since callers span
    contexts that may or may not already hold one."""
    try:
        pool = get_pool()
        async with pool.acquire() as conn:
            await ticket_service.create_engineering_ticket(
                conn, title=title, detail=(detail or "")[:2000], severity=severity,
                category=category, context=context, fingerprint=fingerprint,
            )
    except Exception as e:
        print(f"[ops_alert] ticket creation failed: {e}")


async def alert_exception(exc: Exception, endpoint: str = "", extra: dict | None = None) -> None:
    """Convenience wrapper for unhandled exceptions. Fingerprinted on
    exception type + endpoint rather than the default title+category key —
    the title embeds the exception message, which would otherwise never
    dedupe two firings of the same recurring bug into one ticket."""
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    ctx = {"endpoint": endpoint} if endpoint else {}
    if extra:
        ctx.update(extra)
    await send_alert(
        title=f"{type(exc).__name__}: {str(exc)[:120]}",
        detail=tb,
        severity="ERROR",
        context=ctx or None,
        category="unhandled_exception",
        fingerprint=f"unhandled_exception:{type(exc).__name__}:{endpoint}",
    )


async def alert_msg91_failure(phone: str, error: str, message_type: str = "") -> None:
    await send_alert(
        title="MSG91 Send Failure",
        detail=error[:500],
        severity="ERROR",
        context={"phone": phone, "type": message_type},
        category="integration_failure",
    )


async def alert_cron_failure(cron_name: str, error: str) -> None:
    await send_alert(
        title=f"Cron Failed: {cron_name}",
        detail=error[:500],
        severity="ERROR",
        context={"cron": cron_name},
        category="cron_failure",
    )


async def alert_scraper_failure(client_id: str, scraper_type: str, error: str) -> None:
    await send_alert(
        title=f"Scraper Failed: {scraper_type}",
        detail=error[:500],
        severity="ERROR",
        context={"client_id": client_id, "scraper": scraper_type},
        category="integration_failure",
    )
