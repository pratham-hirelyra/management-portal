"""
Insert active sourced candidates from candidates_batch.txt
source='sourced', is_active=True, no dnd_until
Columns: Name | Phone | Mentioned Location | ID
"""
import asyncio, os, re, csv, httpx, asyncpg
from dotenv import load_dotenv

load_dotenv()

BATCH_FILE = os.path.join(os.path.dirname(__file__), "candidates_batch.txt")


def parse_candidates():
    candidates = []
    with open(BATCH_FILE, encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            name = (row.get("Name") or "").strip()
            phone = (row.get("Phone") or "").strip()
            raw_loc = (row.get("Mentioned Location") or "").strip()
            source_id = (row.get("ID") or "").strip() or None

            if not name:
                continue
            # Skip invalid phones
            phone_digits = re.sub(r"\D", "", phone)
            if len(phone_digits) == 12 and phone_digits.startswith("91"):
                phone_digits = phone_digits[2:]
            if len(phone_digits) != 10:
                print(f"  SKIP (bad phone): {name} — '{phone}'")
                continue

            # Strip "(X.X KM)" and "| Preferred: ..." suffixes
            clean_loc = re.sub(r"\s*\(\d+(?:\.\d+)?\s*KM\)", "", raw_loc, flags=re.I)
            clean_loc = re.sub(r"\s*\|.*$", "", clean_loc).strip()

            candidates.append({
                "name": name,
                "phone": phone_digits,
                "mentioned_location": clean_loc,
                "source_id": source_id,
            })
    return candidates


async def geocode_batch(locations: list[str], api_key: str) -> dict[str, tuple]:
    unique = list(set(locations))
    results = {}
    async with httpx.AsyncClient(timeout=10) as http:
        for loc in unique:
            query = f"{loc}, Ahmedabad, India" if "Ahmedabad" not in loc else loc
            try:
                resp = await http.get(
                    "https://maps.googleapis.com/maps/api/geocode/json",
                    params={"address": query, "key": api_key},
                )
                data = resp.json()
                if data.get("status") == "OK" and data["results"]:
                    geo = data["results"][0]["geometry"]["location"]
                    results[loc] = (geo["lat"], geo["lng"])
                    print(f"  Geocoded: {loc} → {geo['lat']:.4f}, {geo['lng']:.4f}")
                else:
                    print(f"  FAILED geocode: {loc} — {data.get('status')}")
                    results[loc] = (None, None)
            except Exception as e:
                print(f"  ERROR geocode: {loc} — {e}")
                results[loc] = (None, None)
    return results


async def main():
    candidates = parse_candidates()
    print(f"Parsed {len(candidates)} valid candidates")

    api_key = os.environ["GOOGLE_MAPS_API_KEY"]
    unique_locs = list({c["mentioned_location"] for c in candidates if c["mentioned_location"]})
    print(f"Geocoding {len(unique_locs)} unique locations...")
    geo_cache = await geocode_batch(unique_locs, api_key)

    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    inserted = updated = errors = 0

    for c in candidates:
        lat, lng = geo_cache.get(c["mentioned_location"], (None, None))
        loc_parts = c["mentioned_location"].split(",")
        neighbourhood = loc_parts[0].strip()
        current_location = f"{neighbourhood}, Ahmedabad" if neighbourhood else "Ahmedabad"

        try:
            existing = await conn.fetchrow(
                "SELECT id FROM candidates WHERE phone = $1", c["phone"]
            )
            if existing:
                await conn.execute(
                    """
                    UPDATE candidates SET
                        name               = $1,
                        mentioned_location = $2,
                        current_location   = $3,
                        location_lat       = $4,
                        location_lng       = $5,
                        source             = 'sourced',
                        source_id          = $6,
                        is_active          = true,
                        dnd_until          = NULL,
                        updated_at         = now()
                    WHERE phone = $7
                    """,
                    c["name"], c["mentioned_location"], current_location,
                    lat, lng, c["source_id"], c["phone"],
                )
                updated += 1
            else:
                await conn.execute(
                    """
                    INSERT INTO candidates
                        (name, phone, mentioned_location, current_location,
                         location_lat, location_lng, source, source_id,
                         is_active, created_at, updated_at)
                    VALUES ($1,$2,$3,$4,$5,$6,'sourced',$7,true,now(),now())
                    """,
                    c["name"], c["phone"], c["mentioned_location"], current_location,
                    lat, lng, c["source_id"],
                )
                inserted += 1
        except Exception as e:
            print(f"  ERROR {c['name']} ({c['phone']}): {e}")
            errors += 1

    await conn.close()
    print(f"\nDone — inserted: {inserted}, updated: {updated}, errors: {errors}")

    count_conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    count = await count_conn.fetchval("SELECT COUNT(*) FROM candidates")
    await count_conn.close()
    print(f"Total candidates in DB: {count}")


if __name__ == "__main__":
    asyncio.run(main())
