"""
Generates the recruitment service agreement as PDF and uploads to GCP.
"""

from __future__ import annotations
import io
import os
from weasyprint import HTML as _WeasyHTML
from google.cloud import storage as _gcs


def _position_key_skills(job_title: str) -> str:
    jt = (job_title or "").lower()
    if "junior" in jt:
        return "Data entry (purchase/sales/bank), bank reco, petty cash, other admin work"
    return "Accounting, GST, TDS, coordination with CA"


def build_agreement_html(f: dict, positions: list[dict] | None = None) -> str:
    """
    Build agreement HTML from fields dict and optional positions list.

    f keys (shared): client_name, poc_name, job_location,
                     payment_terms, replacement_period
    Single-position fallback: all legacy keys still work when positions is None.
    positions list items: job_title, open_positions, min_salary, max_salary,
                          fee_amount, key_skills, job_timings, tools_requirements,
                          other_details, age_requirements, gender_requirements,
                          salary_deductions
    """
    def fmt_inr(val) -> str:
        try:
            return f"₹{int(float(val)):,}"
        except (TypeError, ValueError):
            return str(val or "")

    def optional_li(label: str, val) -> str:
        v = str(val or "").strip()
        if not v or v.upper() in ("NA", "N/A", "NONE", ""):
            return ""
        return f"<li><strong>{label}:</strong> {v}</li>"

    # Normalise positions: fall back to single-position from f
    if not positions:
        positions = [{
            "job_title":          f.get("job_title", ""),
            "open_positions":     1,
            "min_salary":         f.get("min_salary"),
            "max_salary":         f.get("max_salary"),
            "fee_amount":         f.get("fee_amount"),
            "key_skills":         f.get("key_skills", ""),
            "job_timings":        f.get("job_timings", ""),
            "tools_requirements": f.get("tools_requirements"),
            "other_details":      f.get("other_details"),
            "age_requirements":   f.get("age_requirements"),
            "gender_requirements":f.get("gender_requirements"),
            "salary_deductions":  f.get("salary_deductions"),
        }]

    multi = len(positions) > 1

    payment_due_days = f.get("payment_due_days") or 10
    payment_clause = f"Invoice will be raised on the date of joining. Payment is due within {payment_due_days} days from the date of invoice."

    replacement_period = f.get("replacement_period") or ""
    if not replacement_period or replacement_period.strip().upper() in ("NA", "N/A", "NONE", ""):
        replacement_period = "3 months"

    backdoor_period = f.get("backdoor_period") or ""
    if not backdoor_period or backdoor_period.strip().upper() in ("NA", "N/A", "NONE", ""):
        backdoor_period = "12 months"

    # Build job-details HTML — one block per position
    def _position_block(p: dict, idx: int) -> str:
        cnt = p.get("open_positions") or 1
        pos_label = f"{cnt} position{'s' if cnt != 1 else ''}"
        title_line = p.get("job_title", "")
        if cnt > 1:
            title_line = f"{title_line} ({pos_label})"
        heading = f"<div class='pos-heading'>Position {idx + 1}: {title_line}</div>" if multi else ""
        return f"""
      {heading}
      <ul>
        <li><strong>Job Title:</strong> {p.get("job_title", "")} ({pos_label})</li>
        <li><strong>Location:</strong> {f.get("job_location", "")}</li>
        <li><strong>Salary:</strong> {fmt_inr(p.get("min_salary"))} – {fmt_inr(p.get("max_salary"))} (in-hand / month)</li>
        <li><strong>Key Skills:</strong> {_position_key_skills(p.get("job_title", ""))}</li>
        <li><strong>Job Timings:</strong> {p.get("job_timings", "")}</li>
        {optional_li("Tools Requirement", p.get("tools_requirements"))}
        {optional_li("Other Details", p.get("other_details"))}
        {optional_li("Age Requirement", p.get("age_requirements"))}
        {optional_li("Gender Requirement", p.get("gender_requirements"))}
      </ul>"""

    job_details_html = ""
    for i, p in enumerate(positions):
        if i > 0:
            job_details_html += "<hr style='border:none;border-top:1px dashed #ccc;margin:12px 0'>"
        job_details_html += _position_block(p, i)

    # Build fees HTML — one line per position + total if multi
    def _fee_lines() -> str:
        lines = []
        total = 0
        for p in positions:
            fee = p.get("fee_amount")
            cnt = p.get("open_positions") or 1
            title = p.get("job_title") or "Position"
            if fee:
                subtotal = float(fee) * cnt
                total += subtotal
                if multi:
                    lines.append(
                        f"<li><strong>{title}</strong>: {fmt_inr(fee)} per hire"
                        + (f" × {cnt} = <strong>{fmt_inr(subtotal)}</strong>" if cnt > 1 else "")
                        + "</li>"
                    )
                else:
                    if cnt > 1:
                        lines.append(
                            f"<li>A fixed fee of <strong>{fmt_inr(fee)}</strong> per hire"
                            f" × {cnt} openings = <strong>{fmt_inr(subtotal)}</strong>.</li>"
                        )
                    else:
                        lines.append(f"<li>A fixed fee of <strong>{fmt_inr(fee)}</strong> will be charged per hire.</li>")
        if total and (multi or (len(positions) == 1 and (positions[0].get("open_positions") or 1) > 1)):
            lines.append(f"<li><strong>Total fee: {fmt_inr(total)}</strong></li>")
        lines.append(f"<li>{payment_clause}</li>")
        return "\n".join(lines)

    # Backdoor fee reference — use first position's fee or list all
    backdoor_fee_ref = fmt_inr(positions[0].get("fee_amount")) if not multi else "the applicable fee per position"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Recruitment Service Agreement</title>
<style>
@page {{ size: A4; margin: 40px; }}
body {{ font-family: "Times New Roman", serif; font-size: 13px; line-height: 1.6; color: #000; }}
.container {{ max-width: 700px; margin: auto; border: 2.5px solid #000; padding: 32px 40px; box-sizing: border-box; }}
.header {{ text-align: center; margin-bottom: 15px; }}
.logo {{ max-height: 50px; }}
h1 {{ font-size: 18px; margin: 8px 0; letter-spacing: 1px; }}
p {{ margin: 8px 0; text-align: justify; }}
.section {{ margin-top: 18px; }}
.section-title {{ font-weight: bold; margin-bottom: 6px; text-transform: uppercase; }}
ul {{ margin: 8px 0 0 20px; }}
li {{ margin-bottom: 6px; }}
.pos-heading {{ font-weight: bold; margin: 10px 0 4px; text-decoration: underline; }}
.highlight-box {{ border: 1px solid #000; padding: 10px 14px; margin-top: 10px; background-color: #f9f9f9; }}
hr {{ border: none; border-top: 1px dashed #ccc; margin: 12px 0; }}
</style>
</head>
<body>
<div class="container">

  <div class="header">
    <img src="https://storage.googleapis.com/hirelyra-website/images/unnamed%20(1).jpg" class="logo">
    <h1>RECRUITMENT SERVICE AGREEMENT</h1>
  </div>

  <p>
    This agreement is made between <strong>Tatvic Digital Analytics Pvt. Ltd.</strong>
    (Brand: JustAccountants) ("Consultant") and
    <strong>{f.get("client_name", "")}</strong>
    (represented by <strong>{f.get("poc_name", "")}</strong>) ("Client").
  </p>
  <p>
    The Client agrees to take recruitment services from the Consultant, and the Consultant will provide suitable candidates based on the requirements shared below.
  </p>

  <div class="section">
    <div class="section-title">1. Job Details</div>
    {job_details_html}
    <p>The Consultant will share suitable candidate profiles based on the above requirements.</p>
  </div>

  <div class="section">
    <div class="section-title">2. Fees &amp; Payment</div>
    <ul>
      {_fee_lines()}
    </ul>
  </div>

  <div class="section">
    <div class="section-title">3. Replacement &amp; Refund</div>
    <ul>
      <li>
        If the candidate leaves within <strong>{replacement_period}</strong>
        of joining, one free replacement will be provided at no extra cost.
      </li>
      <li>No refund will be applicable once the candidate has joined. The replacement clause above is the sole remedy available to the Client.</li>
    </ul>
  </div>

  <div class="section">
    <div class="section-title">4. Backdoor Hiring Protection</div>
    <ul>
      <li>All candidate profiles shared by the Consultant are strictly confidential.</li>
      <li>
        If the Client hires any candidate introduced by the Consultant — directly or indirectly —
        within <strong>{backdoor_period}</strong> of the introduction, without going through the Consultant's
        formal process, the Client shall be liable to pay {backdoor_fee_ref} immediately upon such hire being discovered.
      </li>
      <li>This clause applies regardless of whether the original placement was completed or the candidate was ultimately rejected.</li>
    </ul>
  </div>

  <div class="section">
    <div class="section-title">5. Governing Law &amp; Jurisdiction</div>
    <ul>
      <li>This agreement shall be governed by the laws of India.</li>
      <li>Any disputes arising out of this agreement shall be subject to the exclusive jurisdiction of the courts in <strong>Ahmedabad</strong>.</li>
    </ul>
  </div>


</div>
</body>
</html>"""


def generate_pdf(html: str) -> bytes:
    return _WeasyHTML(string=html).write_pdf()


def _safe(name: str) -> str:
    return name.replace("/", "_").replace(" ", "_").replace("\\", "_")


def upload_to_gcp(
    content: bytes,
    client_name: str,
    ext: str = "pdf",
    blob_path: str | None = None,
) -> str:
    """
    Upload content to GCS and return a public URL.

    blob_path overrides the default path of {client_name}/{client_name}.{ext}.
    Bucket is read from GCP_AGREEMENT_BUCKET env var (default: hirelyra-clients GCS bucket).

    GCS structure:
      agreements/{CompanyName}/{CompanyName}.pdf
      interview_confirmations/{CompanyName}/{CandidateName}_{YYYY-MM-DD}.pdf
    """
    bucket_name = os.environ.get("GCP_AGREEMENT_BUCKET", "hirelyra-clients")
    if blob_path is None:
        safe = _safe(client_name)
        blob_path = f"agreements/{safe}/{safe}.{ext}"

    content_type = "application/pdf" if ext == "pdf" else "text/html"

    gcs_client = _gcs.Client()
    bucket = gcs_client.bucket(bucket_name)
    blob = bucket.blob(blob_path)
    # Agreements get regenerated in place at the same URL — disable caching so
    # edited clauses (payment terms, backdoor period, etc.) show up immediately.
    blob.cache_control = "no-cache, max-age=0, must-revalidate"
    blob.upload_from_string(content, content_type=content_type)

    # Make the individual object publicly readable (uniform-access buckets
    # need IAM; fine-grained buckets support per-object ACL).
    try:
        blob.make_public()
    except Exception:
        try:
            policy = bucket.get_iam_policy(requested_policy_version=3)
            already_public = any(
                b.get("role") == "roles/storage.objectViewer"
                and "allUsers" in b.get("members", [])
                for b in policy.bindings
            )
            if not already_public:
                policy.bindings.append({
                    "role": "roles/storage.objectViewer",
                    "members": frozenset(["allUsers"]),
                })
                bucket.set_iam_policy(policy)
        except Exception:
            pass

    return f"https://storage.googleapis.com/{bucket_name}/{blob_path}"


def build_interview_confirmation_html(f: dict) -> str:
    """
    Build an interview confirmation PDF for the client.

    f keys: company_name, poc_name, job_title, job_location,
            candidate_name, candidate_experience, candidate_location,
            candidate_expected_salary, candidate_category,
            candidate_evaluation_status, candidate_total_score,
            candidate_skills (str), interview_slot,
            cv_url, report_url
    """
    def fmt_inr(val) -> str:
        try:
            return f"₹{int(float(val)):,}"
        except (TypeError, ValueError):
            return str(val or "—")

    def row(label: str, val) -> str:
        v = str(val or "").strip()
        if not v or v.upper() in ("NA", "N/A", "NONE", ""):
            return ""
        return f"""
        <tr>
          <td class="label">{label}</td>
          <td>{v}</td>
        </tr>"""

    score = f.get("candidate_total_score")
    score_html = ""
    if score is not None:
        try:
            s = float(score)
            colour = "#16a34a" if s >= 75 else ("#d97706" if s >= 50 else "#dc2626")
            score_html = f'<span style="color:{colour};font-weight:bold">{s:.0f} / 100</span>'
        except (TypeError, ValueError):
            score_html = str(score)

    cv_url = f.get("cv_url", "")
    report_url = f.get("report_url", "")
    link_rows = ""
    if cv_url:
        link_rows += f'<tr><td class="label">CV / Resume</td><td><a href="{cv_url}">{cv_url}</a></td></tr>'
    if report_url:
        link_rows += f'<tr><td class="label">Evaluation Report</td><td><a href="{report_url}">{report_url}</a></td></tr>'

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Interview Confirmation</title>
<style>
@page {{ size: A4; margin: 40px; }}
body {{ font-family: "Helvetica Neue", Arial, sans-serif; font-size: 13px;
       line-height: 1.6; color: #111; }}
.container {{ max-width: 700px; margin: auto; }}
.header {{ display: flex; align-items: center; gap: 16px;
           border-bottom: 2px solid #1d4ed8; padding-bottom: 14px; margin-bottom: 20px; }}
.logo {{ width: 52px; height: 52px; border-radius: 8px; object-fit: cover; }}
.header-text h1 {{ margin: 0; font-size: 18px; color: #1d4ed8; }}
.header-text p  {{ margin: 2px 0 0; font-size: 11px; color: #6b7280; }}
.badge {{ display: inline-block; background: #dbeafe; color: #1d4ed8;
          border-radius: 20px; padding: 4px 14px; font-size: 12px;
          font-weight: 600; margin-bottom: 18px; }}
.section-title {{ font-size: 11px; font-weight: 700; text-transform: uppercase;
                  letter-spacing: .06em; color: #6b7280; margin: 18px 0 6px; }}
table {{ width: 100%; border-collapse: collapse; margin-bottom: 6px; }}
td {{ padding: 6px 8px; vertical-align: top; font-size: 13px; }}
td.label {{ width: 38%; font-weight: 600; color: #374151;
            background: #f9fafb; border-radius: 4px; }}
tr:nth-child(even) td {{ background: #f3f4f6; }}
tr:nth-child(even) td.label {{ background: #e5e7eb; }}
a {{ color: #1d4ed8; word-break: break-all; }}
.footer {{ margin-top: 28px; padding-top: 12px; border-top: 1px solid #e5e7eb;
           font-size: 11px; color: #9ca3af; text-align: center; }}
</style>
</head>
<body>
<div class="container">

  <div class="header">
    <img class="logo"
         src="https://storage.googleapis.com/hirelyra-website/images/unnamed%20(1).jpg"
         alt="Just Accountants">
    <div class="header-text">
      <h1>Interview Confirmation</h1>
      <p>Just Accountants</p>
    </div>
  </div>

  <div class="badge">Slot Confirmed ✓</div>

  <div class="section-title">Interview Details</div>
  <table>
    {row("Company", f.get("company_name"))}
    {row("Role", f.get("job_title"))}
    {row("Location", f.get("job_location"))}
    {row("Interviewer", f.get("poc_name"))}
    {row("Confirmed Slot", f.get("interview_slot"))}
  </table>

  <div class="section-title">Candidate Profile</div>
  <table>
    {row("Name", f.get("candidate_name"))}
    {row("Experience", f.get("candidate_experience"))}
    {row("Current Location", f.get("candidate_location"))}
    {row("Category", f.get("candidate_category"))}
    {row("Key Skills", f.get("candidate_skills"))}
    {row("Expected Salary", fmt_inr(f.get("candidate_expected_salary")))}
    <tr>
      <td class="label">Evaluation Score</td>
      <td>{score_html or "—"}</td>
    </tr>
  </table>

  {f'<div class="section-title">Documents</div><table>{link_rows}</table>' if link_rows else ''}

  <div class="footer">
    This confirmation was auto-generated by Just Accountants on {f.get("generated_date", "")}.
    For queries, contact your recruiter.
  </div>

</div>
</body>
</html>"""


def build_intent_letter_html(f: dict) -> str:
    """
    Build the candidate's intent letter PDF, generated the moment the client
    clicks "Final Hire" in the portal.

    f keys: company_name, candidate_name, job_title, job_location,
            offered_salary, joining_note (optional), generated_date
    """
    def fmt_inr(val) -> str:
        try:
            return f"₹{int(float(val)):,} / month"
        except (TypeError, ValueError):
            return str(val or "—")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Intent Letter</title>
<style>
@page {{ size: A4; margin: 40px; }}
body {{ font-family: "Times New Roman", serif; font-size: 13px; line-height: 1.6; color: #000; }}
.container {{ max-width: 700px; margin: auto; border: 2.5px solid #000; padding: 32px 40px; box-sizing: border-box; }}
.header {{ text-align: center; margin-bottom: 15px; }}
.logo {{ max-height: 50px; }}
h1 {{ font-size: 18px; margin: 8px 0; letter-spacing: 1px; }}
p {{ margin: 8px 0; text-align: justify; }}
.section {{ margin-top: 18px; }}
.section-title {{ font-weight: bold; margin-bottom: 6px; text-transform: uppercase; }}
ul {{ margin: 8px 0 0 20px; }}
li {{ margin-bottom: 6px; }}
.highlight-box {{ border: 1px solid #000; padding: 10px 14px; margin-top: 10px; background-color: #f9f9f9; }}
.footer {{ margin-top: 24px; font-size: 11px; color: #555; }}
</style>
</head>
<body>
<div class="container">

  <div class="header">
    <img src="https://storage.googleapis.com/hirelyra-website/images/unnamed%20(1).jpg" class="logo">
    <h1>LETTER OF INTENT</h1>
  </div>

  <p>Dear <strong>{f.get("candidate_name", "")}</strong>,</p>

  <p>
    We are pleased to confirm that <strong>{f.get("company_name", "")}</strong> would like to move forward
    with your candidacy for the position of <strong>{f.get("job_title", "")}</strong>, following your
    interview process facilitated by JustAccountants.
  </p>

  <div class="section">
    <div class="section-title">Role Details</div>
    <div class="highlight-box">
      <ul>
        <li><strong>Company:</strong> {f.get("company_name", "")}</li>
        <li><strong>Position:</strong> {f.get("job_title", "")}</li>
        <li><strong>Location:</strong> {f.get("job_location", "")}</li>
        <li><strong>Offered Compensation:</strong> {fmt_inr(f.get("offered_salary"))}</li>
      </ul>
    </div>
  </div>

  <p>
    This letter of intent confirms {f.get("company_name", "")}'s intention to extend an offer of employment
    on the terms above, subject to mutual confirmation. A formal offer/appointment letter with complete
    terms will follow directly from {f.get("company_name", "")} upon your acceptance.
  </p>

  <p>Please respond in your JustAccountants portal to accept or decline this offer.</p>

  <p>We wish you the very best and look forward to your response.</p>

  <div class="footer">
    Generated by JustAccountants on {f.get("generated_date", "")}. This letter is issued on behalf of the
    hiring client as part of the JustAccountants recruitment process.
  </div>

</div>
</body>
</html>"""


def client_to_fields(client: dict) -> dict:
    """Convert a DB client record to agreement field dict."""
    key_skills = client.get("key_skills") or []
    if isinstance(key_skills, (list, tuple)):
        key_skills = ", ".join(key_skills)
    return {
        "client_name": client.get("company_name", ""),
        "poc_name": client.get("poc_name", ""),
        "job_title": client.get("job_title", ""),
        "job_location": client.get("job_location") or client.get("location", ""),
        "min_salary": client.get("min_salary"),
        "max_salary": client.get("max_salary"),
        "salary_deductions": client.get("salary_deductions", ""),
        "key_skills": key_skills,
        "job_timings": client.get("job_timings", ""),
        "tools_requirements": client.get("tools_requirements", ""),
        "other_details": client.get("other_details", ""),
        "age_requirements": client.get("age_requirements", ""),
        "gender_requirements": client.get("gender_requirements", ""),
        "fee_amount": client.get("fee_amount") or 7000,
        "payment_terms": client.get("payment_terms", "immediate"),
        "replacement_period": client.get("replacement_period", "3 months"),
        "payment_due_days": client.get("payment_due_days"),
        "backdoor_period": client.get("backdoor_period"),
    }
