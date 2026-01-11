import re

import pytesseract
from pdf2image import convert_from_path
from PyPDF2 import PdfReader

# PATCH: normalize ship names using ships.txt matching (closest match)
from app.core.ships import match_ship


# ------------------------------------------------
# OCR CONFIG
# ------------------------------------------------

pytesseract.pytesseract.tesseract_cmd = "tesseract"


# ------------------------------------------------
# OCR FUNCTIONS
# ------------------------------------------------

def strip_times(text):
    return re.sub(r"\b[0-2]?\d[0-5]\d\b", "", text)


def _extract_pdf_text(path: str) -> str:
    """Best-effort digital text extraction (does NOT replace OCR for names)."""
    try:
        reader = PdfReader(path)
        parts = []
        for p in reader.pages:
            parts.append(p.extract_text() or "")
        return "\n".join(parts)
    except Exception:
        return ""


def _build_table_lines_from_pdf_text(pdf_text: str):
    """
    Build synthetic lines like:
      09/09/2025 CHAFEE (ASW T-3)
      08/25/2025 PAUL HAMILTON (ASW T-2)
      10/07/2025 CURTIS WILBUR (ASW READ-E3)

    This pulls clean event text from the PDF's embedded text layer,
    avoiding OCR mistakes like (ASW 1).
    """
    if not pdf_text:
        return []

    flat = " ".join(pdf_text.split())
    up = flat.upper()

    # PATCH: ship names can be multi-word; capture lazily up to '('
    # Example: "8/25/2025 PAUL HAMILTON (ASW T-2) ..."
    # FIX: Changed (?:ASW|ASTAC)[^)]* to [^)]+ to capture ALL event codes
    # This fixes the bug where entries with event codes like (FBP), (M1), (CV), etc. were being dropped
    pat = re.compile(
        r"\b(\d{1,2}/\d{1,2}/\d{4})\b\s+([A-Z0-9][A-Z0-9 ]{2,}?)\s*\(\s*([^)]+)\)",
        re.IGNORECASE,
    )

    lines = []
    seen = set()

    for m in pat.finditer(up):
        date = m.group(1)
        ship_raw = " ".join(m.group(2).split()).strip()
        evt = m.group(3).strip()

        # Normalize spaces inside evt
        evt = " ".join(evt.split())

        # Guardrail: avoid accidentally capturing headers as "ship"
        if "SEA DUTY" in ship_raw or "CERTIFICATION" in ship_raw or "SHEET" in ship_raw:
            continue

        # PATCH: normalize ship against ships.txt (closest match)
        ship = match_ship(ship_raw) or ship_raw

        line = f"{date} {ship} ({evt})"
        if line not in seen:
            seen.add(line)
            lines.append(line)

    return lines


def _strip_date_lines(text: str) -> str:
    """
    Remove OCR lines that start with a date so the parser doesn't ingest
    bad OCR event tokens. Keeps the rest (NAME/SSN/header/etc).
    """
    out_lines = []
    for ln in (text or "").splitlines():
        if re.match(r"^\s*\d{1,2}/\d{1,2}(?:/\d{2,4})?", ln):
            continue
        out_lines.append(ln)
    return "\n".join(out_lines)


def ocr_pdf(path):
    # 1) Always OCR for NAME/SSN fields (these are often not in embedded text)
    images = convert_from_path(path)
    ocr_out = ""
    for img in images:
        ocr_out += pytesseract.image_to_string(img)

    # 2) Pull clean table event lines from PDF embedded text (if available)
    pdf_text = _extract_pdf_text(path)
    table_lines = _build_table_lines_from_pdf_text(pdf_text)

    # If we got clean table lines, prevent OCR date-lines from polluting parsing
    if table_lines:
        ocr_out = _strip_date_lines(ocr_out)
        combined = (ocr_out + "\n\n" + "\n".join(table_lines)).strip()
        return combined.upper()

    # Otherwise fall back to pure OCR behavior
    return ocr_out.upper()


# ------------------------------------------------
# NAME EXTRACTION
# ------------------------------------------------

def extract_member_name(text):
    m = re.search(r"NAME:\s*([A-Z\s]+?)\s+SSN", text)
    if not m:
        raise RuntimeError("NAME NOT FOUND")
    return " ".join(m.group(1).split())
