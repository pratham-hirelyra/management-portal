"""
One-time backfill: populate clients.city/state and candidates.city/state for
rows that predate the multi-city (pan-India) migration (040_active_cities.sql).

Never overwrites an existing non-null candidates.state (owned by the external
voice-screening automation).

Usage:
    python scripts/backfill_city_state.py [--limit N] [--dry-run] [--sleep 0.2]

--limit caps how many rows per table get processed (useful for a test run
before backfilling everything — re-geocoding thousands of rows makes real,
billed Google Maps API calls).
"""
import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncpg
from dotenv import load_dotenv

load_dotenv()


def _fallback_city(text: str | None) -> str | None:
    """Last-resort free-text guess when geocoding isn't feasible (e.g. rate
    limits during a bulk run) — last plausible comma-separated segment."""
    if not text:
        return None
    parts = [p.strip() for p in text.split(",") if p.strip()]
    if parts and parts[-1].lower() == "india":
        parts = parts[:-1]
    for part in reversed(parts):
        if 3 < len(part) < 30 and part.replace(" ", "").isalpha():
            return part.title()
    return None


async def backfill_clients(conn: asyncpg.Connection, limit: int | None, dry_run: bool, sleep: float) -> dict:
    from services.city_service import reverse_geocode
    from routers.clients import geocode

    rows = await conn.fetch(
        f"""
        SELECT id, location, job_location, location_lat, location_lng
        FROM clients
        WHERE city IS NULL
        ORDER BY created_at DESC
        {"LIMIT " + str(limit) if limit else ""}
        """
    )

    geocoded = fallback = skipped = 0
    for r in rows:
        city = state = None
        if r["location_lat"] is not None and r["location_lng"] is not None:
            city, state = await reverse_geocode(float(r["location_lat"]), float(r["location_lng"]))
        elif r["job_location"] or r["location"]:
            _, _, city, state = await geocode(r["job_location"] or r["location"])

        if city:
            geocoded += 1
        else:
            city = _fallback_city(r["job_location"] or r["location"])
            if city:
                fallback += 1
            else:
                skipped += 1
                continue

        if not dry_run:
            await conn.execute(
                "UPDATE clients SET city = $1, state = COALESCE(state, $2), updated_at = now() WHERE id = $3",
                city, state, r["id"],
            )
        await asyncio.sleep(sleep)

    return {"total": len(rows), "geocoded": geocoded, "fallback": fallback, "skipped": skipped}


async def backfill_candidates(conn: asyncpg.Connection, limit: int | None, dry_run: bool, sleep: float) -> dict:
    from services.city_service import reverse_geocode
    from routers.candidates import _geocode

    rows = await conn.fetch(
        f"""
        SELECT id, current_location, location_lat, location_lng
        FROM candidates
        WHERE city IS NULL
        ORDER BY created_at DESC
        {"LIMIT " + str(limit) if limit else ""}
        """
    )

    geocoded = fallback = skipped = 0
    for r in rows:
        city = state = None
        if r["location_lat"] is not None and r["location_lng"] is not None:
            city, state = await reverse_geocode(float(r["location_lat"]), float(r["location_lng"]))
        elif r["current_location"]:
            _, _, city, state = await _geocode(r["current_location"])

        if city:
            geocoded += 1
        else:
            city = _fallback_city(r["current_location"])
            if city:
                fallback += 1
            else:
                skipped += 1
                continue

        if not dry_run:
            # Never overwrite an existing state — external automation owns it.
            await conn.execute(
                "UPDATE candidates SET city = $1, state = COALESCE(state, $2), updated_at = now() WHERE id = $3",
                city, state, r["id"],
            )
        await asyncio.sleep(sleep)

    return {"total": len(rows), "geocoded": geocoded, "fallback": fallback, "skipped": skipped}


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="Cap rows processed per table")
    parser.add_argument("--dry-run", action="store_true", help="Don't write, just report what would happen")
    parser.add_argument("--sleep", type=float, default=0.2, help="Seconds between geocode calls (rate limiting)")
    args = parser.parse_args()

    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    try:
        print(f"Backfilling clients (limit={args.limit}, dry_run={args.dry_run})...")
        client_stats = await backfill_clients(conn, args.limit, args.dry_run, args.sleep)
        print(f"  {client_stats}")

        print(f"Backfilling candidates (limit={args.limit}, dry_run={args.dry_run})...")
        candidate_stats = await backfill_candidates(conn, args.limit, args.dry_run, args.sleep)
        print(f"  {candidate_stats}")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
