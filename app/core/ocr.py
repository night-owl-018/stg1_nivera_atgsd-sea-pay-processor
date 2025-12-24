import re

import pytesseract
from pdf2image import convert_from_path

# PATCH: digital text extraction first (born-digital PDFs)
from PyPDF2 import PdfReader


# ------------------------------------------------
# OCR CONFIG
# ------------------------------------------------

pytesseract.pytesseract.tesseract_cmd = "tesseract"


# ------------------------------------------------
# OCR FUNCTIONS
# ------------------------------------------------

def strip_times(text):
    return re.sub(r"\b[0-2]?\d[0-5]\d\b", "", text)


def _pdf_has_usable_text(txt: str) -> bool:
    """
    Conservative check: only trust PDF text if it looks like the Sea Duty sheet.
    This avoids false positives where extract_text() returns junk.
    """
    if not txt:
        return False

    t = txt.upper()

    # must have some real volume
    if len(t.strip()) < 200:
        return False

    # must look like the document (header keywords OR date patterns)
    has_header = ("SEA DUTY" in t and "CERTIFICATION" in t) or ("SEA DUTY CERTIFICATION SHEET" in t)
    has_dates = re.search(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b", t) is not None

    return has_header or has_dates


def _extract_pdf_text(path: str) -> str:
    """
    Extract selectable text from the PDF (best quality if PDF is born-digital).
    Returns "" if not usable.
    """
    try:
        reader = PdfReader(path)
        parts = []
        for page in reader.pages:
            parts.append(page.extract_text() or "")
        txt = "\n".join(parts)
        return txt if _pdf_has_usable_text(txt) else ""
    except Exception:
        return ""


def ocr_pdf(path):
    # PATCH: try digital text first (prevents T-3 turning into 1)
    txt = _extract_pdf_text(path)
    if txt:
        return txt.upper()

    # Fallback: image OCR
    images = convert_from_path(path)
    out = ""
    for img in images:
        out += pytesseract.image_to_string(img)
    return out.upper()


# ------------------------------------------------
# NAME EXTRACTION
# ------------------------------------------------

def extract_member_name(text):
    m = re.search(r"NAME:\s*([A-Z\s]+?)\s+SSN", text)
    if not m:
        raise RuntimeError("NAME NOT FOUND")
    return " ".join(m.group(1).split())
