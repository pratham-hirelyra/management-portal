"""
JD card image generator.
Renders the Design-B HTML template → Playwright screenshot → GCS upload.
Returns a public GCS URL usable as a WhatsApp template image header.
"""
import asyncio
import base64
import os
import random
import re
import urllib.parse
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

_TEMPLATE_DIR = Path(__file__).parent.parent / "templates"
_ASSET_DIR = Path(__file__).parent.parent / "assets"
_BUCKET = "hirelyra-clients"
_JD_PREFIX = "jd_cards/"
_GCS_PUBLIC_BASE = f"https://storage.googleapis.com/{_BUCKET}"

# Load logo once at import time
_LOGO_B64: str = base64.b64encode((_ASSET_DIR / "ja_logo.png").read_bytes()).decode()
_THEMES = ["navy", "teal", "plum", "forest", "indigo", "slate"]

_jinja_env = Environment(loader=FileSystemLoader(str(_TEMPLATE_DIR)), autoescape=False)


def _build_template_context(client: dict) -> dict:
    company_name = client.get("company_name") or ""
    job_title = client.get("job_title") or ""
    industry = client.get("industry") or ""
    business_type = client.get("business_type") or ""

    _ot_map = {"corporate": "Corporate Office", "industrial": "Industrial Area"}
    raw_ot = (client.get("office_type") or "").strip()
    office_str = "CA Firm" if client.get("is_ca_firm") else _ot_map.get(raw_ot.lower(), raw_ot)

    num_employees = client.get("num_employees") or ""

    female_pct = client.get("female_employee_pct")
    female_str = str(int(female_pct)) if female_pct is not None else ""

    # Salary
    from services.mms_service import salary_ceiling
    sal_min = float(client["min_salary"]) if client.get("min_salary") else None
    sal_max = salary_ceiling(client.get("max_salary"))
    if sal_min and sal_max:
        salary_str = f"₹{int(sal_min):,} – ₹{int(sal_max):,}"
    elif sal_max:
        salary_str = f"up to ₹{int(sal_max):,}"
    elif sal_min:
        salary_str = f"from ₹{int(sal_min):,}"
    else:
        salary_str = ""
    if client.get("diwali_bonus") and str(client["diwali_bonus"]).strip().lower() not in ("no", "false", "0", "n", ""):
        salary_str = (salary_str + " + Diwali Bonus").strip()

    # Location
    loc_text = client.get("job_location") or client.get("location") or ""
    lat = client.get("location_lat")
    lng = client.get("location_lng")
    if lat and lng:
        maps_url = f"https://maps.google.com/?q={round(float(lat), 6)},{round(float(lng), 6)}"
    elif loc_text:
        maps_url = f"https://maps.google.com/?q={urllib.parse.quote(loc_text)}"
    else:
        maps_url = ""

    location_photo_url = ""  # resolved async in generate_jd_card

    # Skills (same logic as _build_jd_components)
    _jt = job_title.lower()
    key_skills = (
        "Data entry (purchase/sales/bank), bank reco, petty cash, other admin work"
        if "junior" in _jt
        else "Accounting, GST, TDS, coordination with CA"
    )

    tools = client.get("tools_requirements") or ""
    job_timings = client.get("job_timings") or ""

    job_type_raw = (client.get("job_type") or "").strip().lower()
    if "full" in job_type_raw:
        job_type_label = "Full-time"
    elif "part" in job_type_raw:
        job_type_label = "Part-time"
    else:
        job_type_label = ""

    other_raw = re.sub(r'\s*\n+\s*', ' | ', (client.get("other_details") or "").strip())
    other_notes = other_raw or ""

    words = company_name.split()
    company_initials = "".join(w[0].upper() for w in words if w)[:2]

    return {
        "logo_b64": _LOGO_B64,
        "theme": random.choice(_THEMES),
        "company_name": company_name,
        "company_initials": company_initials,
        "job_title": job_title,
        "industry": industry,
        "business_type": business_type,
        "office_str": office_str,
        "num_employees": num_employees,
        "female_str": female_str,
        "salary_str": salary_str,
        "job_timings": job_timings,
        "loc_text": loc_text,
        "maps_url": maps_url,
        "location_photo_url": location_photo_url,
        "key_skills": key_skills,
        "tools": tools,
        "job_type_label": job_type_label,
        "other_notes": other_notes,
    }


def _bearing(from_lat: float, from_lng: float, to_lat: float, to_lng: float) -> float:
    import math
    d_lng = math.radians(to_lng - from_lng)
    x = math.sin(d_lng) * math.cos(math.radians(to_lat))
    y = (math.cos(math.radians(from_lat)) * math.sin(math.radians(to_lat))
         - math.sin(math.radians(from_lat)) * math.cos(math.radians(to_lat)) * math.cos(d_lng))
    return (math.degrees(math.atan2(x, y)) + 360) % 360


async def _resolve_location_photo_b64(lat: float, lng: float) -> str:
    """
    Fetches a location image from Google.
    Finds the nearest outdoor Street View panorama and points the camera toward
    the building coordinates so it shows the façade, not the road.
    Falls back to satellite if no outdoor coverage within 500 m.
    Returns a base64 data URI for direct HTML embedding.
    """
    import httpx as _httpx
    key = os.environ.get("GOOGLE_MAPS_API_KEY", "")
    if not key:
        return ""
    loc = f"{round(lat, 6)},{round(lng, 6)}"
    async with _httpx.AsyncClient(timeout=10.0) as http:
        image_url = None
        for radius in (50, 100, 200, 500):
            meta = await http.get(
                "https://maps.googleapis.com/maps/api/streetview/metadata",
                params={"location": loc, "radius": radius, "source": "outdoor", "key": key},
            )
            data = meta.json()
            if data.get("status") == "OK":
                pano_id = data["pano_id"]
                pano_loc = data["location"]
                heading = _bearing(pano_loc["lat"], pano_loc["lng"], lat, lng)
                image_url = (
                    f"https://maps.googleapis.com/maps/api/streetview"
                    f"?size=556x200&pano={pano_id}&fov=120&pitch=20"
                    f"&heading={heading:.1f}&key={key}"
                )
                break

        if not image_url:
            image_url = (
                f"https://maps.googleapis.com/maps/api/staticmap"
                f"?center={loc}&zoom=18&size=556x200&maptype=satellite&key={key}"
            )

        resp = await http.get(image_url)
        if not resp.is_success:
            return ""
        mime = resp.headers.get("content-type", "image/jpeg").split(";")[0]
        b64 = base64.b64encode(resp.content).decode()
        return f"data:{mime};base64,{b64}"


def _render_html(client: dict, location_photo_url: str = "") -> str:
    ctx = _build_template_context(client)
    ctx["location_photo_url"] = location_photo_url
    tmpl = _jinja_env.get_template("jd_card.html")
    return tmpl.render(**ctx)


async def _screenshot_html(html: str) -> bytes:
    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(args=[
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
        ])
        page = await browser.new_page(viewport={"width": 600, "height": 900})
        # Image is base64-embedded so no network needed — domcontentloaded is enough
        await page.set_content(html, wait_until="domcontentloaded", timeout=15000)
        card = page.locator(".card")
        box = await card.bounding_box()
        clip = {"x": 0, "y": 0, "width": 600, "height": int(box["height"])} if box else None
        png = await page.screenshot(type="png", clip=clip)
        await browser.close()
    return png


def _upload_sync(blob_name: str, data: bytes) -> str:
    from google.cloud import storage
    client = storage.Client()
    bucket = client.bucket(_BUCKET)
    blob = bucket.blob(blob_name)
    blob.upload_from_string(data, content_type="image/png")
    # Bucket uses uniform IAM access — public read is set at bucket level, no per-object ACL needed
    return f"{_GCS_PUBLIC_BASE}/{blob_name}"


async def _upload_to_gcs(client_id: str, png: bytes) -> str:
    blob_name = f"{_JD_PREFIX}{client_id}.png"
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _upload_sync, blob_name, png)


async def generate_jd_card(client: dict) -> str:
    """
    Render JD card for `client`, screenshot it, upload to GCS.
    Returns public URL. Safe to call concurrently — each client gets its own blob.
    """
    lat = client.get("location_lat")
    lng = client.get("location_lng")
    photo_url = ""
    if lat and lng:
        try:
            photo_url = await _resolve_location_photo_b64(float(lat), float(lng))
        except Exception as e:
            print(f"[jd_card] location photo lookup failed: {e}")
    html = _render_html(client, location_photo_url=photo_url)
    png = await _screenshot_html(html)
    url = await _upload_to_gcs(str(client["id"]), png)
    print(f"[jd_card] generated {url}")
    return url
