"""
CV contact-info redaction service.

Flow:
  1. Download PDF from cv_url (GCS public URL).
  2. For each page:
     - If page has selectable text → use PyMuPDF redaction (fast, pixel-perfect).
     - If page is a scanned image  → OCR with pytesseract, then paint black boxes.
  3. Re-upload redacted PDF back to the same GCS path (overwrites original).
  4. Caller marks candidates.cv_phone_redacted = true.
"""

import io
import re
import asyncio
from typing import Optional

import httpx
import fitz  # PyMuPDF


# ── Phone-number regex (Indian-centric + international fallback) ──────────────
_PHONE_RE = re.compile(
    r'(?:'
    r'\+?91[\s\-\.]?[6-9]\d{9}'           # +91 / 91 prefix
    r'|(?<!\d)[6-9]\d{9}(?!\d)'            # bare 10-digit Indian mobile
    r'|\+?1?[\s\-\.]?\(?\d{3}\)?[\s\-\.]\d{3}[\s\-\.]\d{4}'  # intl/US formatted
    r')'
)

_EMAIL_RE = re.compile(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}')

# Minimum text chars on a page to treat it as "text-based"
_TEXT_THRESHOLD = 30


def _redact_text_page(page: fitz.Page) -> int:
    """Redact phone numbers and email addresses on a text-based PDF page. Returns count redacted."""
    count = 0
    text = page.get_text()
    for pattern in (_PHONE_RE, _EMAIL_RE):
        for match in pattern.finditer(text):
            hits = page.search_for(match.group())
            for rect in hits:
                page.add_redact_annot(rect, fill=(0, 0, 0))
                count += 1
    page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)
    return count


def _redact_image_page(page: fitz.Page, doc: fitz.Document) -> int:
    """
    For a scanned page: render to image, OCR it, find phone numbers by bounding
    box, paint black rectangles, replace page content.
    Returns count redacted.
    """
    try:
        import pytesseract
        from PIL import Image, ImageDraw
    except ImportError:
        return 0

    # Check tesseract binary is available before attempting OCR
    try:
        pytesseract.get_tesseract_version()
    except Exception:
        return 0  # tesseract not installed — skip scanned page silently

    try:
        # Render page to image at 200 DPI
        mat = fitz.Matrix(200 / 72, 200 / 72)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        img_bytes = pix.tobytes("png")
        img = Image.open(io.BytesIO(img_bytes))

        # OCR with bounding boxes
        data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
        draw = ImageDraw.Draw(img)

        words = data["text"]
        lefts = data["left"]
        tops = data["top"]
        widths = data["width"]
        heights = data["height"]

        count = 0
        for i, word in enumerate(words):
            if not word.strip():
                continue
            # Email addresses are always single OCR tokens
            if _EMAIL_RE.fullmatch(word.strip()):
                draw.rectangle([lefts[i], tops[i], lefts[i] + widths[i], tops[i] + heights[i]], fill="black")
                count += 1
                continue
            for span in range(1, 4):
                chunk = " ".join(words[i:i+span])
                if _PHONE_RE.fullmatch(chunk.strip()):
                    x = lefts[i];  y = tops[i]
                    x2 = lefts[i+span-1] + widths[i+span-1]
                    y2 = tops[i+span-1] + heights[i+span-1]
                    draw.rectangle([x, y, x2, y2], fill="black")
                    count += 1
                    break

        if count == 0:
            return 0

        img_buf = io.BytesIO()
        img.save(img_buf, format="PNG")
        img_bytes_redacted = img_buf.getvalue()

        new_page = doc.new_page(pno=page.number, width=page.rect.width, height=page.rect.height)
        new_page.insert_image(new_page.rect, stream=img_bytes_redacted)
        doc.delete_page(page.number + 1)

        return count
    except Exception:
        return 0  # OCR failed — skip page silently


def _redact_pdf_bytes(pdf_bytes: bytes) -> tuple[bytes, int]:
    """
    Redact phone numbers from a PDF given as raw bytes.
    Returns (redacted_pdf_bytes, total_redactions).
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    total = 0

    i = 0
    while i < doc.page_count:
        page = doc[i]
        text = page.get_text().strip()
        if len(text) >= _TEXT_THRESHOLD:
            total += _redact_text_page(page)
            i += 1
        else:
            # Scanned page — OCR redaction (may change page count)
            before = doc.page_count
            total += _redact_image_page(page, doc)
            after = doc.page_count
            # page_count stays same (we delete then insert in same position)
            i += 1

    out = io.BytesIO()
    doc.save(out, garbage=4, deflate=True)
    doc.close()
    return out.getvalue(), total


def _gcs_path_from_url(url: str) -> tuple[str, str]:
    """
    Parse 'https://storage.googleapis.com/BUCKET/path/to/file.pdf?v=123'
    → ('BUCKET', 'path/to/file.pdf')
    """
    from urllib.parse import urlparse
    parsed = urlparse(url)
    if "storage.googleapis.com" not in parsed.netloc:
        raise ValueError(f"Not a GCS public URL: {url}")
    # path is /BUCKET/blob/name — strip leading slash then split on first /
    path = parsed.path.lstrip("/")
    bucket, _, blob = path.partition("/")
    return bucket, blob


def _upload_bytes_sync(bucket_name: str, blob_name: str, data: bytes, content_type: str = "application/pdf") -> None:
    from google.cloud import storage
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    blob.upload_from_string(data, content_type=content_type)


async def redact_candidate_cv(cv_url: str) -> dict:
    """
    Download, redact phone numbers, re-upload to same GCS path.
    Returns {"ok": bool, "redactions": int, "error": str|None}
    """
    try:
        async with httpx.AsyncClient(timeout=30) as http:
            resp = await http.get(cv_url)
            resp.raise_for_status()
            pdf_bytes = resp.content
    except Exception as e:
        return {"ok": False, "redactions": 0, "error": f"download failed: {e}"}

    try:
        redacted_bytes, count = await asyncio.get_event_loop().run_in_executor(
            None, _redact_pdf_bytes, pdf_bytes
        )
    except Exception as e:
        return {"ok": False, "redactions": 0, "error": f"redaction failed: {e}"}

    try:
        bucket, blob = _gcs_path_from_url(cv_url)
        await asyncio.get_event_loop().run_in_executor(
            None, _upload_bytes_sync, bucket, blob, redacted_bytes
        )
    except Exception as e:
        return {"ok": False, "redactions": 0, "error": f"upload failed: {e}"}

    return {"ok": True, "redactions": count, "error": None}
