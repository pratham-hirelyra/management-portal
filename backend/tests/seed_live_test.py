"""
Live test seed — inserts candidates covering every test case.

Run:    python tests/seed_live_test.py
Clean:  python tests/seed_live_test.py --clean

All candidates placed at Andheri East, Mumbai (19.1136, 72.8697).
Use the same coordinates on the test client so distance = 0 km → max proximity.

────────────────────────────────────────────────────────────────────
 Group 1  — 12 Senior Accountant, HIGH MMS  → triggers Path 1
            Candidate #1 = +917405552929 (YOU receive WA messages)
            Candidates #2–12 = fake numbers (DB logic runs, no WA delivery)

 Group 2  — Senior Accountant, LOW MMS cases (edge cases)
            #13  DND      : tech=20, salary=25k → ratio<0.5 + salary>20k → DND
            #14  Reclass  : tech=20, salary=15k → ratio<0.5 + salary≤20k → Junior

 Group 3  — Junior Accountant / DEO (Path B)
            #15  HIGH MMS : tools match → MMS ≈ 92
            #16  LOW MMS  : no tools match → MMS ≈ 52

 Group 4  — 36 null-eval Senior Accountants → TC2 Path 2 Branch A
            #20–55 : no evaluation yet, pass all hard filters
────────────────────────────────────────────────────────────────────
"""

import asyncio
import json
import os
import sys

import asyncpg
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))

BASE_LAT = 19.1136
BASE_LNG = 72.8697

# ── Group 1: 12 Senior Accountants — all score ≥ 70 for TC1 client ──────────
# Client TC1: max_salary=30 000, BTS=70
# MMS = (70/70)*70 + (1-0/20)*15 + (1-5k/30k)*10 + tools(2.5) + gst(2.5) = 98.3

GROUP1 = [
    {
        "name":   "Test 01 — Senior HIGH (you)",
        "phone":  "+917405552929",      # ← YOUR number
        "_label": "★ YOU receive WA here",
    },
    {"name": "Test 02 — Senior HIGH", "phone": "+917405500002"},
    {"name": "Test 03 — Senior HIGH", "phone": "+917405500003"},
    {"name": "Test 04 — Senior HIGH", "phone": "+917405500004"},
    {"name": "Test 05 — Senior HIGH", "phone": "+917405500005"},
    {"name": "Test 06 — Senior HIGH", "phone": "+917405500006"},
    {"name": "Test 07 — Senior HIGH", "phone": "+917405500007"},
    {"name": "Test 08 — Senior HIGH", "phone": "+917405500008"},
    {"name": "Test 09 — Senior HIGH", "phone": "+917405500009"},
    {"name": "Test 10 — Senior HIGH", "phone": "+917405500010"},
    {"name": "Test 11 — Senior HIGH", "phone": "+917405500011"},
    {"name": "Test 12 — Senior HIGH", "phone": "+917405500012"},
]

GROUP1_BASE = {
    "category":                    "Senior Accountant",
    "technical_evaluation_score":  70,
    "evaluation_status":           "Pass",
    "current_salary":              5_000,
    "experience":                  "3 years",
    "job_type":                    "Full Time",
    "work_preference":             "Corporate, Industrial",
    "location_lat":                BASE_LAT,
    "location_lng":                BASE_LNG,
    "working_radius":              20,
    "other_details":               json.dumps({"tools": "Tally, Excel", "gst": "GST Filing", "tds": "TDS Filing"}),
}

# ── Group 2: Senior Accountant edge cases ────────────────────────────────────

GROUP2 = [
    {
        "name":    "Test 13 — Senior LOW DND",
        "phone":   "+917405500013",
        "_label":  "Path A low — tech=20, salary=25k → ratio<0.5 + salary>20k → DND flagged",
        "category":                   "Senior Accountant",
        "technical_evaluation_score": 20,
        "evaluation_status":          "Pass",
        "current_salary":             25_000,    # > 20k → DND when ratio < 0.5
        "experience":                 "2 years",
        "job_type":                   "Full Time",
        "work_preference":            "Corporate, Industrial",
        "location_lat":               BASE_LAT,
        "location_lng":               BASE_LNG,
        "working_radius":             20,
        "other_details":              json.dumps({"tools": "Tally", "gst": "GST Filing", "tds": "TDS Filing"}),
    },
    {
        "name":    "Test 14 — Senior LOW Reclassified Junior",
        "phone":   "+917405500014",
        "_label":  "Path A low — tech=20, salary=15k → ratio<0.5 + salary≤20k → classified Junior, not DND",
        "category":                   "Senior Accountant",
        "technical_evaluation_score": 20,
        "evaluation_status":          "Pass",
        "current_salary":             15_000,    # ≤ 20k → reclassified Junior (no DND)
        "experience":                 "2 years",
        "job_type":                   "Full Time",
        "work_preference":            "Corporate, Industrial",
        "location_lat":               BASE_LAT,
        "location_lng":               BASE_LNG,
        "working_radius":             20,
        "other_details":              json.dumps({"tools": "Tally", "gst": "GST Filing", "tds": "TDS Filing"}),
    },
]

# ── Group 3: Junior Accountant / DEO (Path B) ────────────────────────────────

GROUP3 = [
    {
        "name":    "Test 15 — Junior HIGH MMS",
        "phone":   "+917405500015",
        "_label":  "Path B — tools match → soft=40, prox=40, salary=12 → MMS=92",
        "category":                   "Junior Accountant",
        "technical_evaluation_score": 0,
        "evaluation_status":          "Pass",
        "current_salary":             8_000,
        "experience":                 "1 year",
        "job_type":                   "Full Time",
        "work_preference":            "Corporate, Industrial",
        "location_lat":               BASE_LAT,
        "location_lng":               BASE_LNG,
        "working_radius":             20,
        "other_details":              json.dumps({"tools": "Tally, Excel", "gst": "GST Filing", "tds": "TDS Filing"}),
    },
    {
        "name":    "Test 16 — Junior LOW MMS",
        "phone":   "+917405500016",
        "_label":  "Path B — no tools match → soft=0, prox=40, salary=12 → MMS=52",
        "category":                   "Junior Accountant",
        "technical_evaluation_score": 0,
        "evaluation_status":          "Pass",
        "current_salary":             8_000,
        "experience":                 "1 year",
        "job_type":                   "Full Time",
        "work_preference":            "Corporate, Industrial",
        "location_lat":               BASE_LAT,
        "location_lng":               BASE_LNG,
        "working_radius":             20,
        "other_details":              json.dumps({"tools": "SAP, Oracle", "gst": "", "tds": ""}),
    },
]


# ── Group 4: 36 null-eval Senior Accountants → TC2 Path 2 Branch A ───────────
# No evaluation yet — appear in _free_pool_null_eval, pass hard filters for
# any Senior Accountant client in Mumbai with budget ≥ 20k.

GROUP4 = [
    {
        "name":    f"Test {20 + i:02d} — Null-Eval Senior",
        "phone":   f"+9174055{(20 + i):05d}",
        "_label":  "TC2 null-eval pool",
        "category":                   "Senior Accountant",
        "technical_evaluation_score": None,
        "evaluation_status":          None,
        "current_salary":             15_000,
        "experience":                 "2 years",
        "job_type":                   "Full Time",
        "work_preference":            "Corporate, Industrial",
        "location_lat":               BASE_LAT,
        "location_lng":               BASE_LNG,
        "working_radius":             20,
        "other_details":              json.dumps({"tools": "Tally", "gst": "GST Filing", "tds": ""}),
    }
    for i in range(36)
]


def _build_rows():
    rows = []
    for g in GROUP1:
        r = dict(GROUP1_BASE)
        r["name"]  = g["name"]
        r["phone"] = g["phone"]
        r["_label"] = g.get("_label", "")
        rows.append(r)
    for g in GROUP2 + GROUP3 + GROUP4:
        r = dict(g)
        rows.append(r)
    return rows


INSERT_SQL = """
    INSERT INTO candidates (
        name, phone, category, technical_evaluation_score,
        evaluation_status, current_salary, experience,
        job_type, work_preference,
        location_lat, location_lng, working_radius,
        current_location, total_score,
        is_active, other_details
    )
    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,true,$15::jsonb)
    ON CONFLICT (phone) DO UPDATE
        SET name                       = EXCLUDED.name,
            category                   = EXCLUDED.category,
            technical_evaluation_score = EXCLUDED.technical_evaluation_score,
            evaluation_status          = EXCLUDED.evaluation_status,
            current_salary             = EXCLUDED.current_salary,
            experience                 = EXCLUDED.experience,
            job_type                   = EXCLUDED.job_type,
            work_preference            = EXCLUDED.work_preference,
            location_lat               = EXCLUDED.location_lat,
            location_lng               = EXCLUDED.location_lng,
            working_radius             = EXCLUDED.working_radius,
            current_location           = EXCLUDED.current_location,
            total_score                = EXCLUDED.total_score,
            is_active                  = true,
            dnd_until                  = NULL,
            other_details              = EXCLUDED.other_details,
            updated_at                 = now()
    RETURNING id, name, phone
"""


async def seed():
    conn = await asyncpg.connect(dsn=os.environ["DATABASE_URL"])
    rows = _build_rows()
    try:
        print(f"\n{'─'*62}")
        print(f"  Seeding {len(rows)} test candidates")
        print(f"{'─'*62}\n")
        for r in rows:
            label = r.pop("_label", "")
            try:
                row = await conn.fetchrow(
                    INSERT_SQL,
                    r["name"], r["phone"], r["category"],
                    r["technical_evaluation_score"], r["evaluation_status"],
                    r["current_salary"], r["experience"], r["job_type"],
                    r["work_preference"], r["location_lat"], r["location_lng"],
                    r["working_radius"],
                    r.get("current_location", "Andheri East, Mumbai"),
                    r["technical_evaluation_score"],  # total_score mirrors tech score for portal display
                    r["other_details"],
                )
                tag = "★" if r["phone"] == "+917405552929" else " "
                print(f"  {tag} {row['name']}")
                if label:
                    print(f"     {label}")
                print(f"     id={row['id']}  phone={row['phone']}\n")
            except Exception as e:
                print(f"  ✗  {r['name']} — {e}\n")
    finally:
        await conn.close()

    print(f"{'─'*62}")
    print("  Done. Summary:")
    print("   Group 1  (12 Senior HIGH)   → sufficient for Path 1 (THRESHOLD=12)")
    print("   Group 2  (2 edge cases)     → DND + reclassified junior")
    print("   Group 3  (2 Junior/DEO)     → Path B high + low MMS")
    print("   Group 4  (36 null-eval)     → TC2 Path 2 Branch A pool")
    print(f"{'─'*62}\n")


async def clean():
    conn = await asyncpg.connect(dsn=os.environ["DATABASE_URL"])
    phones = [r["phone"] for r in _build_rows()]
    try:
        deleted = await conn.fetch(
            "DELETE FROM candidates WHERE phone = ANY($1) RETURNING name, phone",
            phones,
        )
        print(f"\n{'─'*62}")
        print(f"  Removed {len(deleted)} test candidates")
        print(f"{'─'*62}")
        for d in deleted:
            print(f"  ✗  {d['name']}  ({d['phone']})")
        if not deleted:
            print("  (none found — already clean)")
        print()
    finally:
        await conn.close()


if __name__ == "__main__":
    if "--clean" in sys.argv:
        asyncio.run(clean())
    else:
        asyncio.run(seed())
