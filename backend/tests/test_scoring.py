"""
Layer 1 — Pure unit tests for mms_service and filter_service.

No DB, no mocks, no external calls. Just Python dicts in → dicts out.
Run with: pytest tests/test_scoring.py -v
"""

import pytest

from services.mms_service import (
    _score_accountant,
    _score_deo,
    _tools_match,
    _gst_tds_match,
    distance_km,
    haversine_km,
    score_candidates,
)
from services.filter_service import (
    filter_candidates,
)


# ── Factory helpers ────────────────────────────────────────────────────────────

def make_client(
    *,
    max_salary: float = 30_000,
    job_title: str = "Senior Accountant",
    tools_requirements: str = "Tally",
    gst_tds: dict | None = None,
    office_type: str = "Corporate",
    job_type: str = "Full Time",
    location_lat: float = 19.076,
    location_lng: float = 72.877,
) -> dict:
    return {
        "max_salary": max_salary,
        "job_title": job_title,
        "tools_requirements": tools_requirements,
        "gst_tds": gst_tds if gst_tds is not None else {"gst": "Filing", "tds": "Filing"},
        "office_type": office_type,
        "job_type": job_type,
        "location_lat": location_lat,
        "location_lng": location_lng,
    }


def make_senior(
    *,
    id: str = "00000000-0000-0000-0000-000000000001",
    category: str | None = "Senior Accountant",
    tech_score: float = 60.0,
    current_salary: float = 15_000,
    experience: str = "3 years",
    job_type: str = "Full Time",
    location_lat: float = 19.076,
    location_lng: float = 72.877,
    working_radius: int = 20,
    work_preference: str = "Corporate, Industrial",
    tools: str = "Tally, Excel",
    gst: str = "GST Filing",
    tds: str = "TDS Filing",
) -> dict:
    return {
        "id": id,
        "category": category,
        "technical_evaluation_score": tech_score,
        "current_salary": current_salary,
        "experience": experience,
        "job_type": job_type,
        "location_lat": location_lat,
        "location_lng": location_lng,
        "working_radius": working_radius,
        "work_preference": work_preference,
        "other_details": {"tools": tools, "gst": gst, "tds": tds},
    }


def make_junior(
    *,
    id: str = "00000000-0000-0000-0000-000000000002",
    current_salary: float = 12_000,
    experience: str = "1 year",
    job_type: str = "Full Time",
    location_lat: float = 19.076,
    location_lng: float = 72.877,
    working_radius: int = 20,
    work_preference: str = "Corporate, Industrial",
    tools: str = "Tally, Excel",
) -> dict:
    return {
        "id": id,
        "category": "Junior Accountant",
        "technical_evaluation_score": None,
        "current_salary": current_salary,
        "experience": experience,
        "job_type": job_type,
        "location_lat": location_lat,
        "location_lng": location_lng,
        "working_radius": working_radius,
        "work_preference": work_preference,
        "other_details": {"tools": tools, "gst": "GST Filing", "tds": "TDS Filing"},
    }


# ── BTS (Benchmark Technical Score) ──────────────────────────────────────────

class TestBTS:
    def test_budget_20k(self):
        # 40 + 20 = 60
        r = score_candidates([make_senior(tech_score=50)], make_client(max_salary=20_000))[0]
        assert r["bts"] == 60.0

    def test_budget_30k(self):
        r = score_candidates([make_senior(tech_score=50)], make_client(max_salary=30_000))[0]
        assert r["bts"] == 70.0

    def test_budget_at_40k_boundary(self):
        # 40k is still ≤ 40k → 40 + 40 = 80
        r = score_candidates([make_senior(tech_score=50)], make_client(max_salary=40_000))[0]
        assert r["bts"] == 80.0

    def test_budget_above_40k(self):
        r = score_candidates([make_senior(tech_score=50)], make_client(max_salary=50_000))[0]
        assert r["bts"] == 80.0

    def test_budget_zero_gives_40(self):
        r = score_candidates([make_senior(tech_score=20)], make_client(max_salary=0))[0]
        assert r["bts"] == 40.0


# ── Technical component (Path A) ──────────────────────────────────────────────

class TestTechnicalComponentPathA:
    def _score(self, tech: float, budget: float, dist: float = 5.0) -> dict:
        c = make_client(max_salary=budget, tools_requirements="", gst_tds={"gst": "", "tds": ""})
        return _score_accountant(make_senior(tech_score=tech, current_salary=0), c, dist)

    def test_full_score_equals_70(self):
        # tech == BTS (budget=30k → BTS=70) → component = 70
        r = self._score(70, 30_000)
        assert r["technical_component"] == pytest.approx(70.0)

    def test_half_score_equals_35(self):
        r = self._score(35, 30_000)
        assert r["technical_component"] == pytest.approx(35.0)

    def test_zero_score_equals_zero(self):
        r = self._score(0, 30_000)
        assert r["technical_component"] == pytest.approx(0.0)

    def test_bargain_buy_exceeds_70(self):
        # budget=20k → BTS=60; tech=80 → (80/60)*70 = 93.33
        r = self._score(80, 20_000)
        assert r["technical_component"] == pytest.approx(93.33, rel=0.01)

    def test_bargain_buy_mms_can_exceed_100(self):
        # Same setup but via score_candidates to check full mms
        c = make_client(max_salary=20_000, tools_requirements="", gst_tds={"gst": "", "tds": ""})
        cand = make_senior(tech_score=90, current_salary=0)
        dists = {"00000000-0000-0000-0000-000000000001": 0.0}
        r = score_candidates([cand], c, distances=dists)[0]
        assert r["mms_score"] > 100.0
        assert r["display_mms_score"] == 100.0   # capped for display


# ── Proximity component — NOT floored for either path ─────────────────────────

class TestProximityPathA:
    def test_at_0km_is_15(self):
        assert _score_accountant(make_senior(), make_client(), 0.0)["proximity_component"] == pytest.approx(15.0)

    def test_at_10km_is_7_5(self):
        assert _score_accountant(make_senior(), make_client(), 10.0)["proximity_component"] == pytest.approx(7.5)

    def test_at_20km_is_zero(self):
        assert _score_accountant(make_senior(), make_client(), 20.0)["proximity_component"] == pytest.approx(0.0)

    def test_beyond_20km_goes_negative(self):
        # (1 - 30/20) * 15 = -7.5  — NOT floored
        assert _score_accountant(make_senior(), make_client(), 30.0)["proximity_component"] == pytest.approx(-7.5)

    def test_40km_is_minus_15(self):
        assert _score_accountant(make_senior(), make_client(), 40.0)["proximity_component"] == pytest.approx(-15.0)


class TestProximityPathB:
    def test_at_0km_is_40(self):
        assert _score_deo(make_junior(), make_client(), 0.0)["proximity_component"] == pytest.approx(40.0)

    def test_at_20km_is_zero(self):
        assert _score_deo(make_junior(), make_client(), 20.0)["proximity_component"] == pytest.approx(0.0)

    def test_beyond_20km_goes_negative(self):
        # (1 - 30/20) * 40 = -20
        assert _score_deo(make_junior(), make_client(), 30.0)["proximity_component"] == pytest.approx(-20.0)


# ── Salary component ──────────────────────────────────────────────────────────

class TestSalaryComponent:
    def test_zero_salary_gives_max_10(self):
        r = _score_accountant(make_senior(current_salary=0), make_client(max_salary=30_000), 5.0)
        assert r["salary_component"] == pytest.approx(10.0)

    def test_half_salary_gives_5(self):
        r = _score_accountant(make_senior(current_salary=15_000), make_client(max_salary=30_000), 5.0)
        assert r["salary_component"] == pytest.approx(5.0)

    def test_salary_at_budget_gives_zero(self):
        r = _score_accountant(make_senior(current_salary=30_000), make_client(max_salary=30_000), 5.0)
        assert r["salary_component"] == pytest.approx(0.0)

    def test_salary_above_budget_floored_at_zero(self):
        # Negative would be (1 - 40k/30k)*10 = -3.33 — clamped to 0
        r = _score_accountant(make_senior(current_salary=40_000), make_client(max_salary=30_000), 5.0)
        assert r["salary_component"] == 0.0

    def test_no_budget_gives_zero(self):
        r = _score_accountant(make_senior(), make_client(max_salary=0), 5.0)
        assert r["salary_component"] == 0.0

    def test_path_b_salary_at_half_budget(self):
        # (1 - 12k/24k) * 20 = 10
        r = _score_deo(make_junior(current_salary=12_000), make_client(max_salary=24_000), 5.0)
        assert r["salary_component"] == pytest.approx(10.0)


# ── Skills component ──────────────────────────────────────────────────────────

class TestSkillsComponent:
    def test_tools_match_adds_2_5(self):
        cand = make_senior(tools="Tally, Excel", gst="", tds="")
        r = _score_accountant(cand, make_client(tools_requirements="Tally", gst_tds={"gst": "", "tds": ""}), 5.0)
        assert r["skills_component"] == pytest.approx(2.5)

    def test_gst_match_adds_2_5(self):
        cand = make_senior(tools="", gst="GST Filing", tds="")
        r = _score_accountant(cand, make_client(tools_requirements="", gst_tds={"gst": "Filing", "tds": ""}), 5.0)
        assert r["skills_component"] == pytest.approx(2.5)

    def test_tds_match_adds_2_5(self):
        cand = make_senior(tools="", gst="", tds="TDS Filing")
        r = _score_accountant(cand, make_client(tools_requirements="", gst_tds={"gst": "", "tds": "Filing"}), 5.0)
        assert r["skills_component"] == pytest.approx(2.5)

    def test_both_tools_and_gst_gives_5(self):
        cand = make_senior(tools="Tally", gst="GST Filing", tds="")
        r = _score_accountant(cand, make_client(tools_requirements="Tally", gst_tds={"gst": "Filing", "tds": ""}), 5.0)
        assert r["skills_component"] == pytest.approx(5.0)

    def test_no_match_gives_zero(self):
        cand = make_senior(tools="", gst="", tds="")
        r = _score_accountant(cand, make_client(tools_requirements="", gst_tds={"gst": "", "tds": ""}), 5.0)
        assert r["skills_component"] == 0.0

    def test_path_b_skills_always_zero(self):
        # DEO path doesn't have a skills component
        r = _score_deo(make_junior(), make_client(), 5.0)
        assert r["skills_component"] == 0.0

    def test_tools_no_match_different_tool(self):
        cand = make_senior(tools="SAP, Oracle")
        r = _score_accountant(cand, make_client(tools_requirements="Tally"), 5.0)
        assert r["skills_component"] == pytest.approx(2.5)  # gst/tds still match


# ── Classification and DND ────────────────────────────────────────────────────

class TestClassification:
    def test_ratio_above_half_is_senior_accountant(self):
        # tech=36, BTS=70 → ratio=0.514 ≥ 0.5
        r = _score_accountant(make_senior(tech_score=36, current_salary=15_000), make_client(max_salary=30_000), 5.0)
        assert r["classified_category"] == "Senior Accountant"
        assert r["dnd_flagged"] is False

    def test_ratio_exactly_half_is_senior_accountant(self):
        # 35/70 = 0.5 exactly → >= 0.5 → Senior
        r = _score_accountant(make_senior(tech_score=35), make_client(max_salary=30_000), 5.0)
        assert r["classified_category"] == "Senior Accountant"

    def test_low_ratio_low_salary_reclassified_as_junior(self):
        # tech=30, BTS=70 → ratio=0.43 < 0.5; salary=18k ≤ 20k → Junior
        r = _score_accountant(make_senior(tech_score=30, current_salary=18_000), make_client(max_salary=30_000), 5.0)
        assert r["classified_category"] == "Junior Accountant"
        assert r["dnd_flagged"] is False

    def test_low_ratio_high_salary_rejected_and_dnd(self):
        # tech=30, BTS=70 → ratio<0.5; salary=25k > 20k → rejected, DND
        r = _score_accountant(make_senior(tech_score=30, current_salary=25_000), make_client(max_salary=30_000), 5.0)
        assert r["classified_category"] == "rejected"
        assert r["dnd_flagged"] is True

    def test_dnd_salary_at_20k_boundary_is_junior_not_dnd(self):
        # salary exactly 20k → not DND (condition is > 20k)
        r = _score_accountant(make_senior(tech_score=30, current_salary=20_000), make_client(max_salary=30_000), 5.0)
        assert r["classified_category"] == "Junior Accountant"
        assert r["dnd_flagged"] is False


# ── Path B (DEO / Junior Accountant) ─────────────────────────────────────────

class TestPathB:
    def test_tools_match_gives_40(self):
        r = _score_deo(make_junior(tools="Tally, Excel"), make_client(tools_requirements="Tally"), 5.0)
        assert r["technical_component"] == 40.0

    def test_no_tools_match_gives_zero(self):
        r = _score_deo(make_junior(tools=""), make_client(tools_requirements="Tally"), 5.0)
        assert r["technical_component"] == 0.0

    def test_bts_is_none(self):
        assert _score_deo(make_junior(), make_client(), 5.0)["bts"] is None

    def test_scoring_path_label(self):
        assert _score_deo(make_junior(), make_client(), 5.0)["scoring_path"] == "deo"

    def test_dnd_flagged_always_false(self):
        assert _score_deo(make_junior(), make_client(), 5.0)["dnd_flagged"] is False


# ── Routing by candidate category (NOT by JD title) ──────────────────────────

class TestRouting:
    def test_junior_accountant_gets_path_b(self):
        r = score_candidates([make_junior()], make_client())[0]
        assert r["scoring_path"] == "deo"

    def test_senior_accountant_gets_path_a(self):
        r = score_candidates([make_senior(category="Senior Accountant")], make_client())[0]
        assert r["scoring_path"] == "accountant"

    def test_null_category_defaults_to_path_a(self):
        r = score_candidates([make_senior(category=None)], make_client())[0]
        assert r["scoring_path"] == "accountant"

    def test_empty_category_defaults_to_path_a(self):
        r = score_candidates([make_senior(category="")], make_client())[0]
        assert r["scoring_path"] == "accountant"

    def test_deo_jd_with_senior_candidate_still_path_a(self):
        # Routing is by CANDIDATE category, not JD title
        c = make_client(job_title="Data Entry Operator")
        r = score_candidates([make_senior(category="Senior Accountant")], c)[0]
        assert r["scoring_path"] == "accountant"

    def test_senior_jd_with_junior_candidate_still_path_b(self):
        # Routing is by CANDIDATE category, not JD title
        c = make_client(job_title="Senior Accountant")
        r = score_candidates([make_junior()], c)[0]
        assert r["scoring_path"] == "deo"

    def test_results_sorted_by_mms_desc(self):
        c = make_client(max_salary=30_000, tools_requirements="", gst_tds={"gst": "", "tds": ""})
        high = make_senior(id="00000000-0000-0000-0000-000000000001", tech_score=70, current_salary=0)
        low  = make_senior(id="00000000-0000-0000-0000-000000000002", tech_score=10, current_salary=0)
        results = score_candidates(
            [low, high], c,
            distances={
                "00000000-0000-0000-0000-000000000001": 5.0,
                "00000000-0000-0000-0000-000000000002": 5.0,
            },
        )
        assert results[0]["mms_score"] > results[1]["mms_score"]


# ── Distance helpers ──────────────────────────────────────────────────────────

class TestDistanceHelpers:
    def test_distances_map_overrides_haversine(self):
        cand = {"id": "abc-123", "location_lat": 0.0, "location_lng": 0.0}
        client = {"location_lat": 0.0, "location_lng": 0.0}
        assert distance_km(cand, client, distances={"abc-123": 15.0}) == 15.0

    def test_haversine_fallback_same_point(self):
        cand = {"id": "x", "location_lat": 19.076, "location_lng": 72.877}
        client = {"location_lat": 19.076, "location_lng": 72.877}
        assert distance_km(cand, client) == pytest.approx(0.0, abs=0.1)

    def test_neutral_fallback_when_coords_missing(self):
        # No coords → 20.0 → proximity = 0 (neither penalised nor rewarded)
        cand = {"id": "x", "location_lat": None, "location_lng": None}
        client = {"location_lat": 19.0, "location_lng": 72.0}
        assert distance_km(cand, client) == 20.0

    def test_road_distance_used_in_scoring(self):
        # Pass a distances dict and verify distance_km on the result reflects it
        c = make_client(max_salary=30_000, tools_requirements="", gst_tds={"gst": "", "tds": ""})
        cand = make_senior(tech_score=60, current_salary=0)
        r_near = score_candidates([cand], c, distances={"00000000-0000-0000-0000-000000000001": 2.0})[0]
        r_far  = score_candidates([cand], c, distances={"00000000-0000-0000-0000-000000000001": 30.0})[0]
        assert r_near["proximity_component"] > r_far["proximity_component"]
        assert r_near["distance_km"] == pytest.approx(2.0)
        assert r_far["distance_km"] == pytest.approx(30.0)


# ── Filter 1: Role alignment ──────────────────────────────────────────────────

class TestFilter1RoleAlignment:
    def test_senior_jd_excludes_junior_candidate(self):
        passed, rejected = filter_candidates([make_junior()], make_client(job_title="Senior Accountant"))
        assert len(passed) == 0
        assert any("Role mismatch" in r["reason"] for r in rejected)

    def test_senior_jd_passes_senior_candidate(self):
        passed, _ = filter_candidates([make_senior()], make_client(job_title="Senior Accountant"))
        assert len(passed) == 1

    def test_junior_jd_passes_both_categories(self):
        s = make_senior(id="a1", experience="2 years")
        j = make_junior(id="b1")
        passed, _ = filter_candidates([s, j], make_client(job_title="Junior Accountant"))
        assert len(passed) == 2

    def test_deo_jd_passes_both_categories(self):
        s = make_senior(id="a1", experience="2 years")
        j = make_junior(id="b1")
        passed, _ = filter_candidates([s, j], make_client(job_title="Data Entry Operator"))
        assert len(passed) == 2

    def test_unknown_jd_passes_all(self):
        # job_title doesn't match senior or junior → no role filter applied
        s = make_senior(id="a1")
        j = make_junior(id="b1")
        passed, _ = filter_candidates([s, j], make_client(job_title="HR Manager"))
        assert len(passed) == 2


# ── Filter 2: Employment type ─────────────────────────────────────────────────

class TestFilter2EmploymentType:
    def test_fulltime_jd_excludes_parttime_candidate(self):
        cand = {**make_senior(), "job_type": "Part Time"}
        passed, rejected = filter_candidates([cand], make_client(job_type="Full Time"))
        assert len(passed) == 0
        assert any("Employment type" in r["reason"] for r in rejected)

    def test_fulltime_jd_passes_fulltime_candidate(self):
        cand = {**make_senior(), "job_type": "Full Time"}
        passed, _ = filter_candidates([cand], make_client(job_type="Full Time"))
        assert len(passed) == 1

    def test_fulltime_jd_passes_null_job_type(self):
        # Null means candidate is flexible
        cand = {**make_senior(), "job_type": None}
        passed, _ = filter_candidates([cand], make_client(job_type="Full Time"))
        assert len(passed) == 1


# ── Filter 3: Salary ceiling ──────────────────────────────────────────────────

class TestFilter3SalaryCeiling:
    def test_salary_above_max_excluded(self):
        cand = {**make_senior(), "current_salary": 35_000}
        passed, rejected = filter_candidates([cand], make_client(max_salary=30_000))
        assert len(passed) == 0
        assert any("Salary ceiling" in r["reason"] for r in rejected)

    def test_salary_at_max_passed(self):
        cand = {**make_senior(), "current_salary": 30_000}
        passed, _ = filter_candidates([cand], make_client(max_salary=30_000))
        assert len(passed) == 1

    def test_salary_below_max_passed(self):
        cand = {**make_senior(), "current_salary": 20_000}
        passed, _ = filter_candidates([cand], make_client(max_salary=30_000))
        assert len(passed) == 1


# ── Filter 4: Travel radius ───────────────────────────────────────────────────

class TestFilter4TravelRadius:
    def test_outside_radius_excluded(self):
        # radius=10, grace=2 → limit=12km; actual=15km → excluded
        cand = {**make_senior(working_radius=10), "id": "test-cand"}
        passed, rejected = filter_candidates([cand], make_client(), distances={"test-cand": 15.0})
        assert len(passed) == 0
        assert any("Travel radius" in r["reason"] for r in rejected)

    def test_exactly_at_limit_passed(self):
        # actual == limit (12km) → NOT strictly > → passed
        cand = {**make_senior(working_radius=10), "id": "test-cand"}
        passed, _ = filter_candidates([cand], make_client(), distances={"test-cand": 12.0})
        assert len(passed) == 1

    def test_within_radius_passed(self):
        cand = {**make_senior(working_radius=10), "id": "test-cand"}
        passed, _ = filter_candidates([cand], make_client(), distances={"test-cand": 8.0})
        assert len(passed) == 1

    def test_no_working_radius_skips_filter(self):
        cand = {**make_senior(), "working_radius": None, "id": "test-cand"}
        passed, _ = filter_candidates([cand], make_client(), distances={"test-cand": 100.0})
        assert len(passed) == 1


# ── Filter 5: Work zone ───────────────────────────────────────────────────────

class TestFilter5WorkZone:
    def test_industrial_client_excludes_corporate_only(self):
        c = make_client(office_type="Industrial")
        cand = {**make_senior(), "work_preference": "Corporate"}
        passed, rejected = filter_candidates([cand], c)
        assert len(passed) == 0
        assert any("Work zone" in r["reason"] for r in rejected)

    def test_corporate_client_excludes_industrial_only(self):
        c = make_client(office_type="Corporate")
        cand = {**make_senior(), "work_preference": "Industrial"}
        passed, rejected = filter_candidates([cand], c)
        assert len(passed) == 0

    def test_industrial_client_passes_both_preferences(self):
        c = make_client(office_type="Industrial")
        cand = {**make_senior(), "work_preference": "Corporate, Industrial"}
        passed, _ = filter_candidates([cand], c)
        assert len(passed) == 1

    def test_industrial_client_passes_any_preference(self):
        c = make_client(office_type="Industrial")
        cand = {**make_senior(), "work_preference": "any"}
        passed, _ = filter_candidates([cand], c)
        assert len(passed) == 1

    def test_industrial_client_passes_industrial_only(self):
        c = make_client(office_type="Industrial")
        cand = {**make_senior(), "work_preference": "Industrial"}
        passed, _ = filter_candidates([cand], c)
        assert len(passed) == 1
