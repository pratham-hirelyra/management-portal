"""
Phase 2 — Hard Filter Gate.

filter_candidates(candidates, client, distances=None) -> (passed, rejected)
distances: optional dict mapping str(candidate_id) → road distance km.

Filters (a candidate is excluded if it fails ANY):
  1. Role alignment    — Accountant JD → exclude DEO. DEO JD → include both.
  2. Employment type   — Full-Time JD → exclude Part-Time only candidates.
  3. Salary ceiling    — current_salary > client.max_salary → exclude.
  4. Travel radius     — 4a: distance > client.travel_radius → exclude. 4b: distance > (candidate.working_radius + 2) → exclude.
  5. Work zone         — Industrial client + corporate-only candidate → exclude.
  6. CA firm preference — CA firm client requires candidate open to CA office (or "any").
  7. Gender requirement — client.gender_requirements (Male/Female) → candidate.gender must match.
"""

from services.mms_service import distance_km, salary_ceiling, salary_floor


# ── helpers ───────────────────────────────────────────────────────────────────

def _is_junior_jd(job_title: str | None) -> bool:
    """Junior Accountant / DEO position."""
    if not job_title:
        return False
    jt = job_title.lower()
    if "data entry" in jt or "deo" in jt:
        return True
    return "junior" in jt and ("accountant" in jt or "accounts" in jt)


def _is_senior_jd(job_title: str | None) -> bool:
    """Senior Accountant position (Accountant JD that is NOT junior/DEO)."""
    if not job_title:
        return False
    jt = job_title.lower()
    if _is_junior_jd(job_title):
        return False
    return "accountant" in jt or "accounts" in jt or "senior" in jt


def _is_industrial_client(client: dict) -> bool:
    ot = (client.get("office_type") or "").lower()
    return "industrial" in ot


def _is_corporate_client(client: dict) -> bool:
    ot = (client.get("office_type") or "").lower()
    return "corporate" in ot


def _is_corporate_only_candidate(candidate: dict) -> bool:
    wp = (candidate.get("work_preference") or "").lower()
    if not wp or "any" in wp:
        return False
    has_corporate = "corporate" in wp
    has_industrial = "industrial" in wp
    return has_corporate and not has_industrial


def _is_industrial_only_candidate(candidate: dict) -> bool:
    wp = (candidate.get("work_preference") or "").lower()
    if not wp or "any" in wp:
        return False
    has_corporate = "corporate" in wp
    has_industrial = "industrial" in wp
    return has_industrial and not has_corporate


def _is_ca_firm_client(client: dict) -> bool:
    return bool(client.get("is_ca_firm"))


def _wants_ca_only(candidate: dict) -> bool:
    """Candidate explicitly selected CA office and nothing else (not 'any')."""
    wp = (candidate.get("work_preference") or "").lower()
    if not wp or "any" in wp:
        return False
    has_ca = "ca" in wp
    has_corporate = "corporate" in wp
    has_industrial = "industrial" in wp
    return has_ca and not has_corporate and not has_industrial


# ── main filter function ───────────────────────────────────────────────────────

def filter_candidates(
    candidates: list[dict],
    client: dict,
    distances: dict | None = None,
    include_muslim: bool = True,
    skip_radius_for: set[str] | None = None,
) -> tuple[list[dict], list[dict]]:
    """Returns (passed_candidates, rejection_log).

    skip_radius_for: candidate ids that bypass filter 4 (travel radius) entirely —
    for city-only candidates (no area given, so their lat/lng is a city centroid
    and not trustworthy for a precise km cutoff) considered as a same-city fallback
    when the normal radius-filtered pool comes up short. Every other filter still
    applies to these candidates.
    """
    passed: list[dict] = []
    rejected: list[dict] = []
    skip_radius_for = skip_radius_for or set()

    is_senior_jd = _is_senior_jd(client.get("job_title"))     # → Path A (Accountant)
    is_junior_jd = _is_junior_jd(client.get("job_title"))     # → Path B (DEO/Junior)
    is_industrial = _is_industrial_client(client)
    is_corporate = _is_corporate_client(client)
    is_ca_firm = _is_ca_firm_client(client)
    client_full_time = (client.get("job_type") or "").lower() == "full time"
    max_salary = client.get("max_salary")
    min_salary = client.get("min_salary")
    salary_cap   = salary_ceiling(max_salary)
    salary_floor_ = salary_floor(min_salary)

    for c in candidates:
        cid = str(c.get("id", ""))
        name = c.get("name") or cid

        # Filter 0 — religion (only applied when caller opts out of Muslim candidates)
        if not include_muslim and (c.get("religion") or "").strip().upper() == "M":
            rejected.append({"candidate_id": cid, "name": name,
                             "reason": "Religion filter: Muslim candidates excluded for this match"})
            continue

        # Filter 1 — role alignment
        # Senior JD ("Accountant needed") → exclude DEO/Junior Accountant candidates
        # Junior JD ("DEO needed") → include both Senior + Junior
        if is_senior_jd and (c.get("category") or "").strip() == "Junior Accountant":
            rejected.append({"candidate_id": cid, "name": name,
                             "reason": "Role mismatch: Senior Accountant JD excludes DEO/Junior candidates"})
            continue

        # Filter 2 — employment type
        if client_full_time and (c.get("job_type") or "").lower() == "part time":
            rejected.append({"candidate_id": cid, "name": name,
                             "reason": "Employment type: JD is Full Time, candidate wants Part Time only"})
            continue

        # Filter 3 — salary range (ceiling: max, as-is; floor: min -30% buffer)
        cand_salary = c.get("current_salary")
        if cand_salary:
            cand_sal_f = float(cand_salary)
            if salary_cap and cand_sal_f > salary_cap:
                rejected.append({"candidate_id": cid, "name": name,
                                 "reason": f"Salary ceiling: candidate salary {cand_salary} > cap {salary_cap:.0f} (budget {max_salary})"})
                continue
            if salary_floor_ and cand_sal_f < salary_floor_:
                rejected.append({"candidate_id": cid, "name": name,
                                 "reason": f"Salary floor: candidate salary {cand_salary} < floor {salary_floor_:.0f} (budget {min_salary} -30%)"})
                continue

        # Filter 4 — travel radius (km)
        # Skipped entirely for same-city, no-area candidates passed in via skip_radius_for —
        # their lat/lng is a city centroid, not precise enough for a km cutoff to be trusted.
        if cid not in skip_radius_for:
            # If client has no geocoded coordinates, distance cannot be determined — exclude.
            if not client.get("location_lat") or not client.get("location_lng"):
                rejected.append({"candidate_id": cid, "name": name,
                                 "reason": "Travel radius: client location not geocoded yet"})
                continue
            if not c.get("location_lat") or not c.get("location_lng"):
                rejected.append({"candidate_id": cid, "name": name,
                                 "reason": "Travel radius: candidate location not geocoded"})
                continue
            try:
                actual_km = distance_km(c, client, distances)
                # 4a — Client radius: candidate must be within the distance the client accepts
                client_radius = client.get("travel_radius")
                if client_radius is not None and actual_km > float(client_radius):
                    rejected.append({"candidate_id": cid, "name": name,
                                     "reason": f"Client radius: {actual_km:.1f} km > client limit {client_radius} km"})
                    continue
                # 4b — Candidate radius: candidate must be willing to travel that far (2 km grace)
                # If candidate has no working_radius, assume they're willing to travel as far as the client expects.
                # If the client also has no travel_radius, fall back to 5 km.
                working_radius = c.get("working_radius")
                if working_radius is not None:
                    default_radius = float(working_radius)
                elif client.get("travel_radius") is not None:
                    default_radius = float(client["travel_radius"])
                else:
                    default_radius = 5.0
                limit_km = default_radius + 2.0
                if actual_km > limit_km:
                    rejected.append({"candidate_id": cid, "name": name,
                                     "reason": f"Travel radius: {actual_km:.1f} km > limit {limit_km:.0f} km"})
                    continue
            except (TypeError, ValueError):
                pass

        # Filter 5 — work zone (commented out: work_preference not enforced as hard filter)
        # if is_industrial and _is_corporate_only_candidate(c):
        #     rejected.append({"candidate_id": cid, "name": name,
        #                      "reason": "Work zone: candidate prefers corporate only, client is in industrial area"})
        #     continue
        # if is_corporate and _is_industrial_only_candidate(c):
        #     rejected.append({"candidate_id": cid, "name": name,
        #                      "reason": "Work zone: candidate prefers industrial only, client is in corporate area"})
        #     continue

        # Filter 7 — CA firm preference (commented out: work_preference not enforced as hard filter)
        # if is_ca_firm:
        #     wp = (c.get("work_preference") or "").lower()
        #     if wp and "any" not in wp and "ca" not in wp:
        #         rejected.append({"candidate_id": cid, "name": name,
        #                          "reason": "CA firm: candidate did not select CA office or Any"})
        #         continue
        # else:
        #     if _wants_ca_only(c):
        #         rejected.append({"candidate_id": cid, "name": name,
        #                          "reason": "Work zone: candidate prefers CA firm only, client is not a CA firm"})
        #         continue

        # Filter 8 — gender requirement
        gender_req = (client.get("gender_requirements") or "").strip().lower()
        if gender_req and gender_req != "any":
            cand_gender = (c.get("gender") or "").strip().lower()
            if cand_gender != gender_req:
                rejected.append({"candidate_id": cid, "name": name,
                                 "reason": f"Gender requirement: client requires {client.get('gender_requirements')}, "
                                           f"candidate is {c.get('gender') or 'unspecified'}"})
                continue

        passed.append(c)

    return passed, rejected
