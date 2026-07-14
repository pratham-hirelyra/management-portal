"""
Google Maps helpers — Geocoding + Distance Matrix + Places enrichment.
"""
import os
import httpx

_GEOCODE_URL  = "https://maps.googleapis.com/maps/api/geocode/json"
_MATRIX_URL   = "https://maps.googleapis.com/maps/api/distancematrix/json"
_FIND_URL     = "https://maps.googleapis.com/maps/api/place/findplacefromtext/json"
_DETAILS_URL  = "https://maps.googleapis.com/maps/api/place/details/json"


def _key() -> str:
    k = os.environ.get("GOOGLE_MAPS_API_KEY", "")
    if not k:
        raise RuntimeError("GOOGLE_MAPS_API_KEY not set")
    return k


async def find_place_id(company_name: str, location: str | None) -> str | None:
    """
    Step 1 of enrichment: find a Google Maps place_id for a company.
    Returns place_id string or None if not found.
    """
    query = f"Company Name: {company_name}"
    if location:
        query += f"\nCompany Location: {location}"
    async with httpx.AsyncClient(timeout=10) as http:
        resp = await http.get(_FIND_URL, params={
            "input":     query,
            "inputtype": "textquery",
            "fields":    "place_id",
            "key":       _key(),
        })
        resp.raise_for_status()
    candidates = resp.json().get("candidates") or []
    return (candidates[0].get("place_id") or None) if candidates else None


async def get_place_details(place_id: str) -> dict | None:
    """
    Step 2 of enrichment: fetch phone, website, and coordinates for a place_id.
    Returns {"phone": str|None, "website": str|None, "lat": float|None, "lng": float|None}
    or None on API error.
    """
    async with httpx.AsyncClient(timeout=10) as http:
        resp = await http.get(_DETAILS_URL, params={
            "place_id": place_id,
            "fields":   "name,formatted_phone_number,international_phone_number,website,geometry/location",
            "key":      _key(),
        })
        resp.raise_for_status()
    result = resp.json().get("result") or {}
    if not result:
        return None
    loc = (result.get("geometry") or {}).get("location") or {}
    return {
        "phone":   result.get("formatted_phone_number") or result.get("international_phone_number"),
        "website": result.get("website"),
        "lat":     loc.get("lat"),
        "lng":     loc.get("lng"),
    }


async def geocode_address(address: str) -> tuple[float, float] | None:
    """
    Returns (lat, lng) for a free-text address, or None on failure.
    Appends ', Ahmedabad, India' if not already present to bias results.
    """
    if "ahmedabad" not in address.lower():
        address = address + ", Ahmedabad, India"
    async with httpx.AsyncClient(timeout=10) as http:
        resp = await http.get(_GEOCODE_URL, params={"address": address, "key": _key()})
        resp.raise_for_status()
        data = resp.json()
    results = data.get("results") or []
    if not results:
        return None
    loc = results[0]["geometry"]["location"]
    return float(loc["lat"]), float(loc["lng"])


async def road_distances(
    origin_lat: float,
    origin_lng: float,
    destinations: list[tuple[str, float, float]],  # (id, lat, lng)
) -> dict[str, dict]:
    """
    Calls Distance Matrix API.  Returns {id: {"distance_km": float, "duration_min": int}}.
    Batches 25 destinations per request.  Falls back silently on errors.
    """
    if not destinations:
        return {}

    result: dict[str, dict] = {}
    destination_str = f"{origin_lat},{origin_lng}"

    async with httpx.AsyncClient(timeout=10) as http:
        for i in range(0, len(destinations), 25):
            batch = destinations[i : i + 25]
            origins_str = "|".join(f"{lat},{lng}" for _, lat, lng in batch)
            try:
                resp = await http.get(
                    _MATRIX_URL,
                    params={
                        "origins":      origins_str,
                        "destinations": destination_str,
                        "mode":         "driving",
                        "key":          _key(),
                    },
                )
                if resp.status_code != 200:
                    continue
                rows = resp.json().get("rows", [])
                for j, (uid, _, _) in enumerate(batch):
                    try:
                        el = rows[j]["elements"][0]
                        if el.get("status") == "OK":
                            result[uid] = {
                                "distance_km":  round(el["distance"]["value"] / 1000, 2),
                                "duration_min": round(el["duration"]["value"] / 60),
                            }
                    except (IndexError, KeyError):
                        pass
            except Exception as e:
                print(f"[gmaps] distance matrix batch {i} failed: {e}")

    return result
