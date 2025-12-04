import re

import pytesseract
from pdf2image import convert_from_path

# ------------------------------------------------
# OCR CONFIG
# ------------------------------------------------

pytesseract.pytesseract.tesseract_cmd = "tesseract"


# ------------------------------------------------
# OCR FUNCTIONS
# ------------------------------------------------

def strip_times(text):
    return re.sub(r"\b[0-2]?\d[0-5]\d\b", "", text)


def ocr_pdf(path):
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
