"""
RM auto-assignment and WhatsApp intro notification.
"""
import asyncio
import asyncpg
import constants.whatsapp_templates as WT


async def assign_rm(client_id: str, city: str | None, conn: asyncpg.Connection) -> dict | None:
    """
    Pick the best RM for this client and write rm_id to the clients row.
    Returns the assigned RM record (dict) or None if no eligible RM exists.

    Selection order:
      1. Active RMs in the same city (case-insensitive), under max_clients capacity
         → fewest active clients → lowest all_time_clients (tie-break)
      2. Fall back to any active RM under capacity with fewest active clients
    """
    query = """
        SELECT
            r.id, r.name, r.phone, r.email, r.city, r.max_clients, r.all_time_clients,
            COUNT(c.id) FILTER (
                WHERE c.stage::text NOT IN ('disqualified', 'onboarded')
            ) AS active_count,
            CASE WHEN $1::text IS NOT NULL AND LOWER(r.city) = LOWER($1::text) THEN 0 ELSE 1 END AS city_priority
        FROM rms r
        LEFT JOIN clients c ON c.rm_id = r.id
        WHERE r.is_active = true
        GROUP BY r.id
        HAVING COUNT(c.id) FILTER (
            WHERE c.stage::text NOT IN ('disqualified', 'onboarded')
        ) < r.max_clients
        ORDER BY city_priority ASC, active_count ASC, r.all_time_clients ASC
        LIMIT 1
    """
    rm = await conn.fetchrow(query, city)
    if not rm:
        return None

    await conn.execute(
        """
        UPDATE clients SET rm_id = $1, updated_at = now() WHERE id = $2::uuid
        """,
        rm["id"], client_id,
    )
    await conn.execute(
        "UPDATE rms SET all_time_clients = all_time_clients + 1, updated_at = now() WHERE id = $1",
        rm["id"],
    )
    return dict(rm)


async def send_rm_intro(
    poc_phone: str,
    poc_name: str,
    rm_name: str,
    rm_phone: str,
    template_name: str | None = None,
) -> None:
    """
    Send the RM introduction UTILITY WhatsApp template to the client POC.
    Retries up to 3 times at 5-minute intervals on failure.
    Call this from a BackgroundTask so it never blocks the request.
    """
    import services.msg91_service as msg91

    tpl = template_name or WT.RM_INTRO
    print(f"[rm_intro] Sending template={tpl} to poc={poc_phone} rm={rm_name}/{rm_phone}")
    components = [
        {
            "type": "body",
            "parameters": [
                {"type": "text", "text": poc_name or "there"},
                {"type": "text", "text": rm_name},
                {"type": "text", "text": rm_phone or "—"},
            ],
        }
    ]

    for attempt in range(3):
        if attempt > 0:
            await asyncio.sleep(60)  # 1-minute retry gap (reduced from 5 min)
        try:
            resp = await msg91.send_template(poc_phone, tpl, "en", components, sender="client")
            print(f"[rm_intro] Sent to {poc_phone} on attempt {attempt + 1}: {resp}")
            return
        except Exception as exc:
            print(f"[rm_intro] Attempt {attempt + 1} failed for {poc_phone}: {exc}")

    print(f"[rm_intro] All 3 attempts failed for {poc_phone}")


async def maybe_assign_and_notify(
    client_id: str,
    city: str | None,
    poc_phone: str | None,
    poc_name: str | None,
    background_tasks,
) -> None:
    """
    Convenience wrapper used by client create/onboard endpoints.
    Runs assignment synchronously (needs a conn), schedules WA notification as background task.
    """
    from database import get_pool

    pool = get_pool()
    async with pool.acquire() as conn:
        existing_rm = await conn.fetchval(
            "SELECT rm_id FROM clients WHERE id = $1::uuid", client_id
        )
        if existing_rm:
            return  # already assigned

        rm = await assign_rm(client_id, city, conn)
        if rm and poc_phone:
            background_tasks.add_task(
                send_rm_intro,
                poc_phone,
                poc_name or "there",
                rm["name"],
                rm.get("phone") or "—",
            )
