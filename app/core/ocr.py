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
# NAME EXTRACTION  (patched: multi-pattern + filename fallback)
# ------------------------------------------------

def extract_member_name(text: str, filename: str = "") -> str:
    """
    Try multiple OCR patterns to find the member name.
    If all OCR attempts fail, fall back to deriving the name from the filename.
    Raises RuntimeError only if every strategy fails.
    """
    # --- Strategy 1: standard "NAME: ... SSN" pattern ---
    m = re.search(r"NAME:\s*([A-Z][A-Z\s'.,-]+?)\s+SSN", text, re.IGNORECASE)
    if m:
        name = " ".join(m.group(1).split())
        if len(name) >= 3:
            return name

    # --- Strategy 2: "NAME: ... (line break)" without requiring SSN ---
    m = re.search(
        r"(?:LAST|FIRST|MEMBER|MEMBER'?S?)?\s*NAME[:\s]+([A-Z][A-Z\s'.,-]{2,}?)(?:\n|SOCIAL|SSN|RATE|RANK|\d{3})",
        text,
        re.IGNORECASE,
    )
    if m:
        name = " ".join(m.group(1).split()).strip(" ,")
        if len(name) >= 3:
            return name

    # --- Strategy 3: "FIRST, LAST" or "LAST, FIRST" after common labels ---
    m = re.search(r"(?:SOCIAL\s+SECURITY\s+NUMBER|SSN)[:.\s]*(?:FIRST,?\s*\(?LAST)?\s*([A-Z][A-Z\s'.,]{3,30})", text, re.IGNORECASE)
    if m:
        name = " ".join(m.group(1).split()).strip(" ,")
        if len(name) >= 3:
            return name

    # --- Strategy 4: filename-based derivation ---
    if filename:
        name = _name_from_filename(filename)
        if name:
            import app.core.logger as _lgr
            try:
                _lgr.log(f"NAME FALLBACK FROM FILENAME → '{filename}' → '{name}'")
            except Exception:
                pass
            return name

    raise RuntimeError("NAME NOT FOUND")


def _name_from_filename(filename: str) -> str:
    """
    Derive a member name from common filename patterns:
      - "RATE LAST, FIRST.pdf"  → "FIRST LAST"
      - "LAST Sea Pay ...pdf"   → "LAST"
      - "LAST_Sea_Pay ...pdf"   → "LAST"
    Returns empty string if no pattern matches.
    """
    base = re.sub(r"\.pdf$", "", filename, flags=re.IGNORECASE).strip()

    # Pattern A: "RATE LAST, FIRST" e.g. "GM1 BELL, RICHARD"
    m = re.match(
        r"^[A-Z0-9]{1,6}\s+([A-Z][A-Z']+),\s*([A-Z][A-Z']+)",
        base,
        re.IGNORECASE,
    )
    if m:
        return f"{m.group(2).upper()} {m.group(1).upper()}"

    # Pattern B: "RATE LAST, FIRST MIDDLE"
    m = re.match(
        r"^[A-Z0-9]{1,6}\s+([A-Z][A-Z']+),\s*([A-Z][A-Z'\s]+)",
        base,
        re.IGNORECASE,
    )
    if m:
        first_parts = m.group(2).strip().split()
        first = first_parts[0] if first_parts else m.group(2).strip()
        return f"{first.upper()} {m.group(1).upper()}"

    # Pattern C: "LASTNAME Sea Pay ..." or "LASTNAME_Sea_Pay_..."
    m = re.match(r"^([A-Z][A-Z']{1,})\s+Sea\s*Pay", base, re.IGNORECASE)
    if m:
        return m.group(1).upper()

    m = re.match(r"^([A-Z][A-Z']{1,})_Sea_Pay", base, re.IGNORECASE)
    if m:
        return m.group(1).upper()

    return ""
