"""
Phase 3 — MMS (Matchmaking Score) Calculation.

score_candidates(candidates, client, distances=None) -> list of scored dicts
distances: optional dict mapping str(candidate_id) → road distance in km
           (from Google Maps Distance Matrix). Falls back to haversine if absent.

Path A — Accountant (70 technical + 15 proximity + 10 salary + 5 skills)
Path B — DEO        (40 software  + 40 proximity + 20 salary)

Proximity uses km / 20, NOT minutes / 40. Negative scores (distance > 20 km) are
NOT floored at 0 — distant candidates correctly carry a penalty.

Bargain-buy: normalised technical score is NOT capped at 100% (a 100% AI score
against a 60% BTS yields 116.67% in the technical component). Final mms can
exceed 100 — useful for sorting; clamped only for display via display_mms_score.

Post-score classification:
  ratio = technical_evaluation_score / BTS
  >= 0.5               → classify as Accountant
  < 0.5, salary ≤ 20k  → classify as Data Entry Operator
  < 0.5, salary > 20k  → rejected, flag DND 1 year
"""

import math


def _is_junior_jd(job_title: str | None) -> bool:
    if not job_title:
        return False
    jt = job_title.lower()
    if "data entry" in jt or "deo" in jt:
        return True
    return "junior" in jt and ("accountant" in jt or "accounts" in jt)


# ── helpers ───────────────────────────────────────────────────────────────────

def _clamp_low(val: float) -> float:
    return max(0.0, val)


def salary_ceiling(max_salary) -> float | None:
    """client max_salary + 15% buffer, final result rounded to nearest thousand."""
    if not max_salary:
        return None
    base = float(max_salary)
    buffer = math.floor(base * 0.15 / 1000 + 0.5) * 1000
    result = base + buffer
    return math.floor(result / 1000 + 0.5) * 1000


def salary_floor(min_salary) -> float | None:
    """client min_salary - 30% buffer, final result rounded to nearest thousand."""
    if not min_salary:
        return None
    base = float(min_salary)
    buffer = math.floor(base * 0.30 / 1000 + 0.5) * 1000
    result = max(0.0, base - buffer)
    return math.floor(result / 1000 + 0.5) * 1000


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def distance_km(candidate: dict, client: dict, distances: dict | None = None) -> float:
    """
    Returns road distance (km) if `distances` map contains the candidate.
    Falls back to straight-line haversine. Defaults to 20.0 (proximity = 0)
    when coordinates are missing on either side.
    """
    if distances is not None:
        d = distances.get(str(candidate.get("id")))
        if d is not None:
            return float(d)
    clat = candidate.get("location_lat")
    clng = candidate.get("location_lng")
    jlat = client.get("location_lat")
    jlng = client.get("location_lng")
    if all([clat, clng, jlat, jlng]):
        return haversine_km(float(clat), float(clng), float(jlat), float(jlng))
    return 20.0  # neutral fallback → proximity = 0 (not penalised)


def _get_od(candidate: dict) -> dict:
    od = candidate.get("other_details") or candidate.get("candidate_other_details") or {}
    if isinstance(od, str):
        import json as _json
        try: od = _json.loads(od)
        except Exception: od = {}
    return od if isinstance(od, dict) else {}


def _tools_match(candidate: dict, client: dict) -> bool:
    client_tools = (client.get("tools_requirements") or "").lower()
    od = _get_od(candidate)
    cand_tools = (od.get("tools") or "").lower()
    if not client_tools or not cand_tools:
        return False
    client_list = {t.strip() for t in client_tools.replace(",", " ").split() if len(t.strip()) > 2}
    return any(ct in cand_tools for ct in client_list)


# Filing is a higher skill than Preparation — a Filing candidate satisfies a Preparation requirement.
_GST_TDS_RANK = {"preparation": 1, "filing": 2}


def _gst_tds_skill_ok(client_val: str, cand_val: str) -> bool:
    """True if candidate's skill level meets or exceeds what the client requires."""
    if not client_val or not cand_val:
        return False
    client_rank = _GST_TDS_RANK.get(client_val, 0)
    cand_rank   = _GST_TDS_RANK.get(cand_val, 0)
    if client_rank == 0:
        return client_val in cand_val  # unknown value — fall back to substring
    return cand_rank >= client_rank


def _gst_tds_match(candidate: dict, client: dict) -> bool:
    import json as _json
    raw = client.get("gst_tds") or {}
    if isinstance(raw, str):
        try: raw = _json.loads(raw)
        except Exception: raw = {}
    client_gst_tds = raw
    od = _get_od(candidate)
    if not isinstance(od, dict) or not isinstance(client_gst_tds, dict):
        return False
    cand_gst   = (od.get("gst") or "").lower()
    cand_tds   = (od.get("tds") or "").lower()
    client_gst = (client_gst_tds.get("gst") or "").lower()
    client_tds = (client_gst_tds.get("tds") or "").lower()
    return _gst_tds_skill_ok(client_gst, cand_gst) or _gst_tds_skill_ok(client_tds, cand_tds)


# ── scoring paths ─────────────────────────────────────────────────────────────

def _score_accountant(candidate: dict, client: dict, dist_km: float) -> dict:
    budget = float(client.get("max_salary") or 0)
    if budget <= 40_000:
        bts = 40 + (budget / 1000) if budget else 40.0
    else:
        bts = 80.0

    tech_score = float(candidate.get("technical_evaluation_score") or 0)
    current_salary = float(candidate.get("current_salary") or 0)

    technical_component = (tech_score / bts) * 70 if bts > 0 else 0
    # Proximity: km / 20, NOT floored — distance > 20 km yields negative score
    proximity_component = (1 - (dist_km / 20.0)) * 15
    salary_component    = _clamp_low((1 - (current_salary / budget)) * 10) if budget > 0 else 0
    skills_component    = (2.5 if _tools_match(candidate, client) else 0) + \
                          (2.5 if _gst_tds_match(candidate, client) else 0)

    mms = technical_component + proximity_component + salary_component + skills_component

    ratio = tech_score / bts if bts > 0 else 0
    if ratio >= 0.5:
        classified_category = "Senior Accountant"
        dnd_flagged = False
    elif current_salary <= 20_000:
        classified_category = "Junior Accountant"
        dnd_flagged = False
    else:
        classified_category = "rejected"
        dnd_flagged = True

    return {
        "mms_score": round(mms, 2),
        "display_mms_score": round(min(mms, 100.0), 2),
        "technical_component": round(technical_component, 2),
        "proximity_component": round(proximity_component, 2),
        "salary_component": round(salary_component, 2),
        "skills_component": round(skills_component, 2),
        "scoring_path": "accountant",
        "distance_km": round(dist_km, 2),
        "bts": round(bts, 2),
        "classified_category": classified_category,
        "dnd_flagged": dnd_flagged,
    }


def _score_deo(candidate: dict, client: dict, dist_km: float) -> dict:
    budget = float(client.get("max_salary") or 0)
    current_salary = float(candidate.get("current_salary") or 0)

    # BTS (same formula as Path A)
    if budget <= 40_000:
        bts = 40 + (budget / 1000) if budget else 40.0
    else:
        bts = 80.0

    tech_score = float(candidate.get("technical_evaluation_score") or 0)

    # AI Score — 50 pts
    technical_component = (tech_score / bts) * 50 if bts > 0 and tech_score > 0 else 0.0
    # Accounting Software — 15 pts (binary)
    software_component  = 15.0 if _tools_match(candidate, client) else 0.0
    # Proximity — 25 pts, NOT floored
    proximity_component = (1 - (dist_km / 20.0)) * 25
    # Salary — 10 pts
    salary_component    = _clamp_low((1 - (current_salary / budget)) * 10) if budget > 0 else 0

    mms = technical_component + software_component + proximity_component + salary_component

    return {
        "mms_score": round(mms, 2),
        "display_mms_score": round(min(mms, 100.0), 2),
        "technical_component": round(technical_component, 2),
        "proximity_component": round(proximity_component, 2),
        "salary_component": round(salary_component, 2),
        "skills_component": round(software_component, 2),
        "scoring_path": "deo",
        "distance_km": round(dist_km, 2),
        "bts": round(bts, 2),
        "classified_category": candidate.get("category") or "Junior Accountant",
        "dnd_flagged": False,
    }


# ── breakdown for mappings display ───────────────────────────────────────────

def compute_score_breakdown(candidate: dict, client: dict) -> dict:
    """Return a {criterion: {score, max}} dict that sums to the stored mms_score."""
    dist = distance_km(candidate, client)
    if _is_junior_jd(client.get("job_title")):
        s = _score_deo(candidate, client, dist)
        return {
            "technical":  {"score": s["technical_component"], "max": 50},
            "software":   {"score": s["skills_component"],    "max": 15},
            "proximity":  {"score": s["proximity_component"], "max": 25},
            "salary_pos": {"score": s["salary_component"],    "max": 10},
        }
    else:
        s = _score_accountant(candidate, client, dist)
        return {
            "technical":  {"score": s["technical_component"], "max": 70},
            "proximity":  {"score": s["proximity_component"], "max": 15},
            "salary_pos": {"score": s["salary_component"],    "max": 10},
            "software":   {"score": s["skills_component"],    "max": 5},
        }


# ── public interface ──────────────────────────────────────────────────────────

def score_candidates(
    candidates: list[dict],
    client: dict,
    distances: dict | None = None,
) -> list[dict]:
    """
    Score each candidate and return enriched dicts sorted by mms_score DESC.
    Each returned dict includes all original candidate fields plus scoring fields.
    `distances` (optional): dict[str(candidate_id) → road distance km].
    """
    # Scoring path is driven by the CLIENT's JD type, not the candidate's category.
    # Junior JD → all candidates use Path B (DEO). Senior JD → Path A (Accountant).
    client_is_junior = _is_junior_jd(client.get("job_title"))

    results = []
    for c in candidates:
        dist = distance_km(c, client, distances)
        scores = _score_deo(c, client, dist) if client_is_junior else _score_accountant(c, client, dist)
        results.append({**c, **scores})

    results.sort(key=lambda x: x["mms_score"], reverse=True)
    return results
