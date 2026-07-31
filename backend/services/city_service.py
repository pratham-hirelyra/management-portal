"""
Multi-city (pan-India) helpers. active_cities is the single DB-backed source
of truth for which cities client sourcing/scraping is currently allowed in —
turning on a new city later is a data change only (INSERT a row), never a
code deploy.
"""
import os
import asyncpg
import httpx


async def active_cities(conn: asyncpg.Connection) -> list[dict]:
    """All currently-active rows from active_cities, e.g. for cron.py's per-city scrape loop."""
    rows = await conn.fetch(
        "SELECT city, state, naukri_code, is_active FROM active_cities WHERE is_active = true"
    )
    return [dict(r) for r in rows]


def parse_city_state(address_components: list[dict] | None) -> tuple[str | None, str | None]:
    """
    Extract (city, state) from a Google Geocoding API result's address_components.

    `locality` alone fragments what should be one metro area into many tiny
    buckets — small villages/census towns near a big city (e.g. Khodiyar,
    Chharodi, Dabhoda, Rakanpur near Ahmedabad/Gandhinagar) each get their own
    `locality` name rather than their parent city's. `administrative_area_level_3`
    (India's taluka/city-cluster level) consistently resolves these back to the
    right metro name (confirmed against live geocoding: Khodiyar -> Gandhinagar,
    Chharodi/Dabhoda -> Ahmedabad), while for actual major cities it just repeats
    the same name as `locality` (Ahmedabad -> Ahmedabad, Surat -> Surat) — so
    preferring it over `locality` loses nothing and fixes the fragmentation.
    `administrative_area_level_2` is NOT used as a fallback here — it varies too
    inconsistently by state to trust (e.g. Mumbai's level_2 is "Konkan Division",
    a multi-city region, while its level_3 "Mumbai City" is the useful one).
    """
    import re
    locality = admin3 = state = None
    for comp in address_components or []:
        types = comp.get("types") or []
        if "locality" in types and not locality:
            locality = comp.get("long_name")
        if "administrative_area_level_3" in types and not admin3:
            admin3 = comp.get("long_name")
        if "administrative_area_level_1" in types:
            state = comp.get("long_name")
    city = admin3 or locality
    if city:
        city = re.sub(r"\s+City$", "", city)  # "Mumbai City" -> "Mumbai"
    return city, state


async def reverse_geocode(lat: float, lng: float) -> tuple[str | None, str | None]:
    """(city, state) from coordinates already on hand — used by the one-time
    backfill so rows with existing lat/lng don't need a fresh forward-geocode."""
    key = os.environ.get("GOOGLE_MAPS_API_KEY", "")
    if not key:
        return None, None
    try:
        async with httpx.AsyncClient(timeout=8.0) as http:
            resp = await http.get(
                "https://maps.googleapis.com/maps/api/geocode/json",
                params={"latlng": f"{lat},{lng}", "key": key},
            )
        results = resp.json().get("results", [])
        if results:
            return parse_city_state(results[0].get("address_components"))
    except Exception:
        pass
    return None, None


async def is_active_city(conn: asyncpg.Connection, city: str | None, fallback_text: str = "") -> bool:
    """
    True if `city` (case-insensitive exact) matches an active row.
    If `city` isn't available (e.g. legacy scrape data with no city field yet),
    falls back to a substring match of `fallback_text` against active city names.
    """
    if city:
        match = await conn.fetchval(
            "SELECT 1 FROM active_cities WHERE is_active = true AND LOWER(city) = LOWER($1)",
            city,
        )
        return bool(match)

    if not fallback_text:
        return False

    rows = await conn.fetch("SELECT city FROM active_cities WHERE is_active = true")
    text = fallback_text.lower()
    return any(r["city"].lower() in text for r in rows)
