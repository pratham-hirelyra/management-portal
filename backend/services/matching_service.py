import math
import json as _json
from typing import Optional

# ── Constants ─────────────────────────────────────────────────────────────────
MMS_THRESHOLD    = 40   # minimum score to enter pool
MMS_HIGH_QUALITY = 70   # Rule of 10 threshold (Phase 4)
TOP_N            = 12
MIN_TO_NOTIFY    = 5    # Phase 6: min interested responses before handover
TRAVEL_GRACE_KM  = 2    # Phase 2 hard filter grace
MAX_PROXIMITY_KM = 20   # denominator in proximity formula


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 6371
    d_lat = (lat2 - lat1) * math.pi / 180
    d_lng = (lng2 - lng1) * math.pi / 180
    a = (math.sin(d_lat / 2) ** 2 +
         math.cos(lat1 * math.pi / 180) * math.cos(lat2 * math.pi / 180) *
         math.sin(d_lng / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _parse_other_details(candidate: dict) -> dict:
    od = candidate.get('other_details') or candidate.get('candidate_other_details') or {}
    if isinstance(od, str):
        try:
            od = _json.loads(od)
        except Exception:
            od = {}
    return od if isinstance(od, dict) else {}


# ── Role helpers ──────────────────────────────────────────────────────────────

def _candidate_tier(category: str) -> str:
    """Returns 'senior', 'junior', or 'rejected'."""
    c = category.strip().lower()
    if 'rejected' in c:
        return 'rejected'
    if 'junior' in c:
        return 'junior'
    if 'senior' in c or 'accountant' in c:
        return 'senior'
    # legacy DEO / data entry labels
    if 'data entry' in c or 'deo' in c:
        return 'junior'
    return 'senior'


def _client_tier(job_title: str) -> str:
    """Returns 'senior' or 'junior'."""
    t = job_title.strip().lower()
    if 'junior' in t or 'data entry' in t or 'deo' in t:
        return 'junior'
    return 'senior'


# ── Phase 2: Hard Filter Gate (all must pass) ─────────────────────────────────

def _passes_hard_filters(
    candidate: dict,
    client: dict,
    already_mapped_ids: set,
    j_lat: float = 0,
    j_lng: float = 0,
) -> bool:
    # Pre-conditions (portal invariants)
    if str(candidate.get('id', '')) in already_mapped_ids:
        return False
    if candidate.get('is_active') is False:
        return False

    category = (candidate.get('category') or '').strip()
    c_tier   = _candidate_tier(category)

    # 0. Rejected candidates are never matched
    if c_tier == 'rejected':
        return False

    status = (candidate.get('evaluation_status') or '').strip().lower()
    if status not in ('pass', 'passed'):
        return False

    job_title = (client.get('job_title') or '').strip()
    cl_tier   = _client_tier(job_title)

    # 1. Role Alignment
    #    Senior Accountant client → Senior Accountant candidates only
    #    Junior Accountant client → both Senior and Junior candidates
    if cl_tier == 'senior' and c_tier != 'senior':
        return False

    # 2. Employment Type — must match exactly (Full Time ↔ Full Time, Part Time ↔ Part Time)
    client_jt = (client.get('job_type') or '').strip().lower()
    cand_jt   = (
        candidate.get('ov_job_type') or candidate.get('job_type') or ''
    ).strip().lower()
    if client_jt and cand_jt and client_jt != cand_jt:
        return False

    # 3. Salary Ceiling — current salary must be ≤ client budget
    current = float(candidate.get('current_salary') or 0)
    budget  = float(client.get('max_salary') or 0)
    if current and budget and current > budget:
        return False

    # 4. Travel Radius — if client has no geocoded location, exclude candidate
    if not j_lat or not j_lng:
        return False
    c_lat = float(candidate.get('location_lat') or 0)
    c_lng = float(candidate.get('location_lng') or 0)
    road_dist = candidate.get('_road_distance_km')
    if road_dist is not None:
        dist = float(road_dist)
    else:
        dist = haversine_km(j_lat, j_lng, c_lat, c_lng) if (c_lat and c_lng) else None

    # 4a — Client radius: only candidates within the distance the client accepts
    client_radius = client.get('travel_radius')
    if client_radius is not None and dist is not None and dist > float(client_radius):
        return False

    # 4b — Candidate radius: candidate must be willing to travel that far
    radius = float(
        candidate.get('ov_working_radius') or
        candidate.get('working_radius') or 7
    )
    if dist is not None and dist > radius + TRAVEL_GRACE_KM:
        return False

    # 5. Work Zone Match
    office_type = (client.get('office_type') or '').strip().lower()
    work_pref = (
        candidate.get('ov_work_preference') or
        candidate.get('work_preference') or ''
    ).strip().lower()
    if work_pref and 'any' not in work_pref:
        if 'industrial' in office_type and 'industrial' not in work_pref:
            return False  # industrial client, candidate won't work industrial
        if 'corporate' in office_type and 'corporate' not in work_pref and 'industrial' in work_pref:
            return False  # corporate client, candidate only wants industrial

    return True


# ── Scoring helpers ───────────────────────────────────────────────────────────

def _get_distance(candidate: dict, j_lat: float, j_lng: float) -> Optional[float]:
    road = candidate.get('_road_distance_km')
    if road is not None:
        return float(road)
    c_lat = float(candidate.get('location_lat') or 0)
    c_lng = float(candidate.get('location_lng') or 0)
    if j_lat and j_lng and c_lat and c_lng:
        return haversine_km(j_lat, j_lng, c_lat, c_lng)
    return None


def _tools_match(candidate: dict, client: dict) -> bool:
    req_str = (client.get('tools_requirements') or '').strip()
    if not req_str:
        return True
    od = _parse_other_details(candidate)
    cand_tools = (od.get('tools') or '').lower()
    if not cand_tools:
        return False
    # Split client requirements into individual tokens (by space and comma)
    # and check if any token appears as a substring in the candidate's tools string
    client_tokens = {t.strip().lower() for t in req_str.replace(',', ' ').split() if len(t.strip()) > 2}
    return any(token in cand_tools for token in client_tokens)


def _has_gst_tds(candidate: dict) -> bool:
    od = _parse_other_details(candidate)
    return bool((od.get('gst') or '').strip() or (od.get('tds') or '').strip())


# ── Phase 3: Path A — Senior Accountant scoring ───────────────────────────────

def _score_path_a(
    candidate: dict,
    client: dict,
    dist_km: Optional[float],
) -> tuple[float, dict]:
    budget  = float(client.get('max_salary') or 0)
    current = float(candidate.get('current_salary') or 0)

    # BTS (Benchmark Technical Score)
    # Budget ≤ ₹40K → 40 + (budget / 1000)
    # Budget > ₹40K → 80 (capped)
    if budget > 40000:
        bts = 80.0
    else:
        bts = 40.0 + (budget / 1000) if budget else 40.0

    # Technical — 70 pts (no cap — bargain-buy rule rewards high AI vs low BTS)
    raw_ai = float(candidate.get('technical_evaluation_score') or candidate.get('total_score') or 0)
    tech   = (raw_ai / bts) * 70 if bts > 0 and raw_ai > 0 else 0.0

    # Proximity — 15 pts (no floor — negative score if dist > 20 km)
    if dist_km is not None:
        prox = (1 - dist_km / MAX_PROXIMITY_KM) * 15
    else:
        prox = 7.5  # neutral when coordinates unavailable

    # Salary Position — 10 pts
    if budget and current:
        sal = (1 - current / budget) * 10
    else:
        sal = 5.0  # neutral

    # Binary Skills — 5 pts
    software = 2.5 if _tools_match(candidate, client) else 0.0
    gst_tds  = 2.5 if _has_gst_tds(candidate) else 0.0

    total = tech + prox + sal + software + gst_tds
    breakdown = {
        'technical':  {'score': round(tech, 1),     'max': 70,  'bts': round(bts, 1)},
        'proximity':  {'score': round(prox, 1),     'max': 15},
        'salary_pos': {'score': round(sal, 1),      'max': 10},
        'software':   {'score': round(software, 1), 'max': 2.5},
        'gst_tds':    {'score': round(gst_tds, 1),  'max': 2.5},
    }
    return round(total, 1), breakdown


# ── Phase 3: Path B — Junior Accountant scoring ──────────────────────────────

def _score_path_b(
    candidate: dict,
    client: dict,
    dist_km: Optional[float],
) -> tuple[float, dict]:
    budget  = float(client.get('max_salary') or 0)
    current = float(candidate.get('current_salary') or 0)

    # BTS (same formula as Path A)
    if budget > 40000:
        bts = 80.0
    else:
        bts = 40.0 + (budget / 1000) if budget else 40.0

    # AI Score — 50 pts
    raw_ai = float(candidate.get('technical_evaluation_score') or candidate.get('total_score') or 0)
    tech   = (raw_ai / bts) * 50 if bts > 0 and raw_ai > 0 else 0.0

    # Accounting Software — 15 pts (binary)
    software = 15.0 if _tools_match(candidate, client) else 0.0

    # Proximity — 25 pts (no floor — negative if dist > 20 km)
    if dist_km is not None:
        prox = (1 - dist_km / MAX_PROXIMITY_KM) * 25
    else:
        prox = 12.5  # neutral

    # Salary Position — 10 pts
    if budget and current:
        sal = (1 - current / budget) * 10
    else:
        sal = 5.0  # neutral

    total = tech + software + prox + sal
    breakdown = {
        'technical':  {'score': round(tech, 1),     'max': 50,  'bts': round(bts, 1)},
        'software':   {'score': round(software, 1), 'max': 15},
        'proximity':  {'score': round(prox, 1),     'max': 25},
        'salary_pos': {'score': round(sal, 1),      'max': 10},
    }
    return round(total, 1), breakdown


# ── Score breakdown (for list_mappings display) ───────────────────────────────

def compute_score_breakdown(candidate: dict, client: dict) -> dict:
    job_title = (client.get('job_title') or '').strip()
    j_lat = float(client.get('location_lat') or 0)
    j_lng = float(client.get('location_lng') or 0)
    dist  = _get_distance(candidate, j_lat, j_lng)
    if _client_tier(job_title) == 'junior':
        _, bd = _score_path_b(candidate, client, dist)
    else:
        _, bd = _score_path_a(candidate, client, dist)
    return bd


# ── Main entry point ──────────────────────────────────────────────────────────

def match_candidates(
    client: dict,
    candidates: list[dict],
    already_mapped_ids: Optional[set] = None,
    top_n: int = TOP_N,
    threshold: int = MMS_THRESHOLD,
) -> tuple[list[dict], str, int]:
    """
    Phase 2 + 3 + 4 of the matchmaking spec.

    Returns:
        (top_candidates, flag, high_quality_count)

        top_candidates      — up to top_n, all with MMS >= threshold, sorted desc
        flag                — 'ok'           : high_quality_count >= 10 (Rule of 10 met)
                              'insufficient' : 0 < high_quality_count < 10
                              'none'         : zero candidates passed threshold
        high_quality_count  — candidates with MMS >= MMS_HIGH_QUALITY (70)
    """
    mapped_ids = already_mapped_ids or set()
    j_lat      = float(client.get('location_lat') or 0)
    j_lng      = float(client.get('location_lng') or 0)
    job_title  = (client.get('job_title') or '').strip()
    use_path_b = _client_tier(job_title) == 'junior'

    scored: list[dict] = []

    for raw in candidates:
        c = dict(raw)
        if not _passes_hard_filters(c, client, mapped_ids, j_lat, j_lng):
            continue

        dist = _get_distance(c, j_lat, j_lng)
        score, breakdown = (
            _score_path_b(c, client, dist) if use_path_b
            else _score_path_a(c, client, dist)
        )

        if score < threshold:
            continue

        c['_distance_km']    = round(dist, 1) if dist is not None else None
        c['_match_score']    = score
        c['_score_breakdown'] = breakdown
        scored.append(c)

    # Highest score first; tie-break: fewer prior interviews → more available
    scored.sort(key=lambda c: (-c['_match_score'], int(c.get('interview_count') or 0)))

    high_quality = sum(1 for c in scored if c['_match_score'] >= MMS_HIGH_QUALITY)

    top = scored[:top_n]

    if high_quality == 0:
        flag = 'none'
    elif high_quality < 10:
        flag = 'insufficient'
    else:
        flag = 'ok'

    return top, flag, high_quality
