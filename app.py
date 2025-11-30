import os
import re
import io
import csv
from datetime import datetime, timedelta
from difflib import get_close_matches

from PyPDF2 import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

import pytesseract
from pdf2image import convert_from_path

# ------------------------------------------------
# CONFIG (DOCKER-SAFE)
# ------------------------------------------------

# Project root
ROOT = os.path.dirname(os.path.abspath(__file__))

# OCR Engine (Linux/Docker)
pytesseract.pytesseract.tesseract_cmd = "tesseract"

# Input / Output / Template (env driven for Docker)
BASE = os.environ.get("SEA_PAY_INPUT", os.path.join(ROOT, "Data"))
TEMPLATE = os.environ.get(
    "SEA_PAY_TEMPLATE",
    os.path.join(ROOT, "pdf_template", "NAVPERS_1070_613_TEMPLATE.pdf")
)
OUTDIR = os.environ.get("SEA_PAY_OUTPUT", os.path.join(ROOT, "OUTPUT"))

# Ensure output directory exists
os.makedirs(OUTDIR, exist_ok=True)

# Font (Linux path installed in container)
FONT_PATH = os.path.join(ROOT, "Times_New_Roman.ttf")
pdfmetrics.registerFont(TTFont("TimesNewRoman", FONT_PATH))
FONT_NAME = "TimesNewRoman"
FONT_SIZE = 10

# ------------------------------------------------
# LOAD RATE CSV
# ------------------------------------------------

RATE_FILE = os.path.join(ROOT, "atgsd_n811.csv")


def _clean_header(h: str) -> str:
    if h is None:
        return ""
    return h.lstrip("\ufeff").strip().strip('"').lower()


def load_rates():
    rates = {}

    with open(RATE_FILE, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)

        # Safety: avoid crash if file is empty or malformed
        if not reader.fieldnames:
            return {}

        reader.fieldnames = [_clean_header(h) for h in reader.fieldnames]

        for raw_row in reader:
            row = {}
            for k, v in raw_row.items():
                key = _clean_header(k)
                if not key:
                    continue
                row[key] = (v or "").replace("\t", "").strip()

            last = row.get("last", "").upper()
            first = row.get("first", "").upper()
            rate = row.get("rate", "").upper()

            if not last or not rate:
                continue

            rates[f"{last},{first}"] = rate

    print(f"✅ RATES LOADED: {len(rates)}")
    return rates


RATES = load_rates()

# ------------------------------------------------
# SHIP LIST
# ------------------------------------------------

SHIP_FILE = os.path.join(ROOT, "ships.txt")
with open(SHIP_FILE, "r", encoding="utf-8") as f:
    SHIP_LIST = [line.strip() for line in f if line.strip()]


def normalize(text: str) -> str:
    text = re.sub(r"\(.*?\)", "", text or "")
    text = re.sub(r"[^A-Z ]", "", text.upper())
    return " ".join(text.split())


NORMALIZED_SHIPS = {normalize(s): s.upper() for s in SHIP_LIST}
NORMAL_KEYS = list(NORMALIZED_SHIPS.keys())

# ------------------------------------------------
# OCR
# ------------------------------------------------

def ocr_pdf(path):
    images = convert_from_path(path)  # Docker-safe (no poppler_path)
    out = ""
    for img in images:
        out += pytesseract.image_to_string(img)
    return out.upper()


def strip_times(text):
    return re.sub(r"\b[0-2]?\d[0-5]\d\b", "", text)


# ------------------------------------------------
# NAME
# ------------------------------------------------

def extract_member_name(text):
    m = re.search(r"NAME:\s*([A-Z\s]+?)\s+SSN", text)
    if not m:
        raise RuntimeError("NAME not found.")
    return " ".join(m.group(1).split())


# ------------------------------------------------
# SHIP MATCH
# ------------------------------------------------

def match_ship(raw_text):
    candidate = normalize(raw_text)
    if not candidate:
        return None

    words = candidate.split()

    for size in range(len(words), 0, -1):
        for i in range(len(words) - size + 1):
            chunk = " ".join(words[i:i + size])
            match = get_close_matches(chunk, NORMAL_KEYS, n=1, cutoff=0.75)
            if match:
                return NORMALIZED_SHIPS[match[0]]

    return None


# ------------------------------------------------
# PARSE DATES
# ------------------------------------------------

def extract_year_from_filename(path):
    m = re.search(r"(20\d{2})", os.path.basename(path))
    return m.group(1) if m else str(datetime.now().year)


def parse_rows(text, year):
    rows = []
    seen = set()
    lines = text.splitlines()

    for i, line in enumerate(lines):
        m = re.match(r"\s*(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?", line)
        if not m:
            continue

        mm, dd, yy = m.groups()
        y = ("20" + yy) if (yy and len(yy) == 2) else (yy if yy else year)
        date = f"{mm.zfill(2)}/{dd.zfill(2)}/{y}"

        raw = line[m.end():]
        if i + 1 < len(lines):
            raw += " " + lines[i + 1]

        ship = match_ship(raw)
        if not ship:
            continue

        if (date, ship) not in seen:
            rows.append({"date": date, "ship": ship})
            seen.add((date, ship))

    return rows


# ------------------------------------------------
# GROUP CONTIGUOUS DAYS
# ------------------------------------------------

def group_by_ship(rows):
    groups = {}
    for r in rows:
        dt = datetime.strptime(r["date"], "%m/%d/%Y")
        groups.setdefault(r["ship"], []).append(dt)

    results = []

    for ship, dates in groups.items():
        dates = sorted(set(dates))

        start = prev = dates[0]
        for day in dates[1:]:
            if day == prev + timedelta(days=1):
                prev = day
            else:
                results.append({
                    "ship": ship,
                    "start": start.strftime("%m/%d/%Y"),
                    "end": prev.strftime("%m/%d/%Y")
                })
                start = prev = day

        results.append({
            "ship": ship,
            "start": start.strftime("%m/%d/%Y"),
            "end": prev.strftime("%m/%d/%Y")
        })

    return results


# ------------------------------------------------
# RATE MATCH
# ------------------------------------------------

def get_rate(name):
    parts = normalize(name).split()
    if len(parts) < 2:
        return ""

    first = parts[0]
    last = parts[-1]

    key = f"{last},{first}"
    if key in RATES:
        return RATES[key]

    for k in RATES:
        if k.startswith(last + ","):
            return RATES[k]

    return ""


# ------------------------------------------------
# CREATE PDF
# ------------------------------------------------

def make_pdf(group, name):
    start = group["start"]
    end = group["end"]
    ship = group["ship"]

    parts = name.split()
    last = parts[-1]
    first = " ".join(parts[:-1])

    rate = get_rate(name)

    prefix = f"{rate}_" if rate else ""
    filename = f"{prefix}{last}_{first}_{ship}_{start.replace('/','-')}_TO_{end.replace('/','-')}.pdf"
    filename = filename.replace(" ", "_")

    path = os.path.join(OUTDIR, filename)

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.setFont(FONT_NAME, FONT_SIZE)

    # HEADER
    c.setFont(FONT_NAME, 10)
    c.drawString(39, 689, "AFLOAT TRAINING GROUP SAN DIEGO (UIC. 49365)")

    # CHECKBOX
    c.drawString(373, 671, "X")

    # ENTITLEMENT
    c.setFont(FONT_NAME, 8)
    c.drawString(39, 650, "ENTITLEMENT")

    # OPNAV
    c.drawString(345, 641, "OPNAVINST 7220.14")

    # NAME
    c.setFont(FONT_NAME, FONT_SIZE)
    if rate:
        c.drawString(39, 41, f"{rate} {last}, {first}")
    else:
        c.drawString(39, 41, f"{last}, {first}")

    # REMARKS
    c.drawString(38.84, 595, f"____. REPORT CAREER SEA PAY FROM {start} TO {end}.")
    c.drawString(64, 571,
                 f"Member performed eight continuous hours per day on-board: {ship} Category A vessel.")

    # CERTIFYING BLOCK
    c.drawString(356.26, 499.5, "_________________________")
    c.drawString(363.8, 487.5, "Certifying Official & Date")
    c.drawString(356.26, 427.5, "_________________________")
    c.drawString(384.1, 415.2, "FI MI Last Name")
    c.drawString(38.8, 83, "SEA PAY CERTIFIER")
    c.drawString(503.5, 40, "USN AD")

    c.save()
    buf.seek(0)

    overlay = PdfReader(buf)
    template = PdfReader(TEMPLATE)

    base = template.pages[0]
    base.merge_page(overlay.pages[0])

    writer = PdfWriter()
    writer.add_page(base)
    for i in range(1, len(template.pages)):
        writer.add_page(template.pages[i])

    with open(path, "wb") as f:
        writer.write(f)

    print("CREATED:", path)


# ------------------------------------------------
# MERGE MASTER PDF WITH BOOKMARKS
# ------------------------------------------------

def merge_with_bookmarks():
    PDFs = sorted(f for f in os.listdir(OUTDIR)
                  if f.lower().endswith(".pdf") and not f.startswith("MASTER"))
    writer = PdfWriter()

    page = 0
    for file in PDFs:
        reader = PdfReader(os.path.join(OUTDIR, file))
        writer.add_outline_item(file.replace(".pdf", ""), page)
        for p in reader.pages:
            writer.add_page(p)
            page += 1

    output = os.path.join(OUTDIR, "MASTER_SEA_PAY_PACKET.pdf")
    with open(output, "wb") as f:
        writer.write(f)

    print("\n✅ MASTER FILE CREATED:", output)


# ------------------------------------------------
# MAIN
# ------------------------------------------------

def main():
    files = [f for f in os.listdir(BASE)
             if f.lower().endswith(".pdf") and "navpers" not in f.lower()]

    for file in files:
        print("\nPROCESSING:", file)
        path = os.path.join(BASE, file)

        raw = strip_times(ocr_pdf(path))
        name = extract_member_name(raw)
        year = extract_year_from_filename(path)

        rows = parse_rows(raw, year)
        groups = group_by_ship(rows)

        for g in groups:
            make_pdf(g, name)

    merge_with_bookmarks()
    print("\n✅ ALL FILES COMPLETE")


if __name__ == "__main__":
    main()
