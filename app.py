import os
import re
import io
import csv
from datetime import datetime, timedelta
from difflib import get_close_matches

from flask import Flask, render_template, request
from PyPDF2 import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter

import pytesseract
from pdf2image import convert_from_path


# ------------------------------------------------
# FLASK APP
# ------------------------------------------------
app = Flask(__name__)

# ------------------------------------------------
# DEFAULT PATHS INSIDE CONTAINER
# (override in Web UI form)
# ------------------------------------------------
DEFAULT_DATA_DIR = "/data"
DEFAULT_TEMPLATE_PDF = "/templates/NAVPERS_1070_613_TEMPLATE.pdf"
DEFAULT_RATE_FILE = "/config/atgsd_n811.csv"
DEFAULT_OUTPUT_DIR = "/output"

# In container, tesseract is on PATH
pytesseract.pytesseract.tesseract_cmd = "tesseract"

# Use built-in Times font in reportlab
FONT_NAME = "Times-Roman"
FONT_SIZE = 10


# ------------------------------------------------
# SHIP LIST (same as your working script)
# ------------------------------------------------
SHIP_LIST = [
    "America","Anchorage","Arleigh Burke","Arlington","Ashland","Augusta",
    "Bainbridge","Barry","Bataan","Beloit","Benfold","Billings","Blue Ridge",
    "Boxer","Bulkeley","Canberra","Cape St. George","Carl M. Levin","Carney",
    "Carter Hall","Chafee","Charleston","Chief","Chosin","Chung-Hoon",
    "Cincinnati","Cole","Comstock","Cooperstown","Curtis Wilbur",
    "Daniel Inouye","Decatur","Delbert D. Black","Dewey","Donald Cook","Essex",
    "Farragut","Fitzgerald","Forrest Sherman","Fort Lauderdale","Fort Worth",
    "Frank E. Petersen Jr.","Gabrielle Giffords","Germantown","Gettysburg",
    "Gonzalez","Gravely","Green Bay","Gridley","Gunston Hall","Halsey",
    "Harpers Ferry","Higgins","Hopper","Howard","Indianapolis","Iwo Jima",
    "Jackson","Jack H. Lucas","James E. Williams","Jason Dunham",
    "John Basilone","John Finn","John P. Murtha","John Paul Jones",
    "John S. McCain","Kansas City","Kearsarge","Kidd","Kingsville",
    "Laboon","Lake Erie","Lassen","Lenah Sutcliffe Higbee",
    "Mahan","Makin Island","Manchester","Marinette","Mason","McCampbell",
    "McFaul","Mesa Verde","Michael Monsoor","Michael Murphy","Milius",
    "Minneapolis-Saint Paul","Mitscher","Mobile","Momsen","Montgomery",
    "Mount Whitney","Mustin","Nantucket","New Orleans","New York","Nitze",
    "O'Kane","Oak Hill","Oakland","Omaha","Oscar Austin","Patriot",
    "Paul Hamilton","Paul Ignatius","Pearl Harbor","Pinckney","Pioneer",
    "Porter","Portland","Preble","Princeton","Rafael Peralta","Ralph Johnson",
    "Ramage","Richard M. McCool Jr.","Robert Smalls","Roosevelt","Ross",
    "Rushmore","Russell","Sampson","San Antonio","San Diego","Santa Barbara",
    "Savannah","Shiloh","Shoup","Somerset","Spruance","St. Louis","Sterett",
    "Stethem","Stockdale","Stout","The Sullivans","Tortuga","Tripoli",
    "Truxtun","Tulsa","Warrior","Wasp","Wayne E. Meyer",
    "William P. Lawrence","Winston S. Churchill","Wichita","Zumwalt"
]


def normalize(text: str) -> str:
    text = re.sub(r"\(.*?\)", "", text)
    text = re.sub(r"[^A-Z ]", "", text.upper())
    return " ".join(text.split())


NORMALIZED_SHIPS = {normalize(s): s.upper() for s in SHIP_LIST}
NORMAL_KEYS = list(NORMALIZED_SHIPS.keys())


# ------------------------------------------------
# CORE HELPERS
# ------------------------------------------------
def strip_times(text: str) -> str:
    return re.sub(r"\b[0-2]?\d[0-5]\d\b", "", text)


def extract_member_name(text: str) -> str:
    m = re.search(r"NAME:\s*([A-Z\s]+?)\s+SSN", text)
    if not m:
        raise RuntimeError("NAME not found.")
    return " ".join(m.group(1).split())


def match_ship(raw_text: str):
    candidate = normalize(raw_text)
    if not candidate:
        return None

    words = candidate.split()

    for size in range(len(words), 0, -1):
        for i in range(len(words) - size + 1):
            chunk = " ".join(words[i:i+size])
            match = get_close_matches(chunk, NORMAL_KEYS, n=1, cutoff=0.75)
            if match:
                return NORMALIZED_SHIPS[match[0]]

    return None


def extract_year_from_filename(path: str) -> str:
    m = re.search(r"(20\d{2})", os.path.basename(path))
    return m.group(1) if m else str(datetime.now().year)


def parse_rows(text: str, year: str):
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


def load_rates(rate_file: str, log):
    rates = {}

    if not os.path.exists(rate_file):
        log(f"[RATES] CSV not found: {rate_file}")
        return rates

    log(f"[RATES] Loading from {rate_file}")

    with open(rate_file, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)

        if not reader.fieldnames:
            log("[RATES] No header row detected.")
            return rates

        def _clean_header(h: str) -> str:
            if h is None:
                return ""
            return h.lstrip("\ufeff").strip().strip('"').lower()

        reader.fieldnames = [_clean_header(h) for h in reader.fieldnames]

        for raw_row in reader:
            row = {}
            for k, v in raw_row.items():
                key = _clean_header(k)
                if not key:
                    continue
                row[key] = (v or "").strip()

            last = row.get("last", "").upper()
            first = row.get("first", "").upper()
            rate = row.get("rate", "").upper()

            if not last or not rate:
                continue

            rates[f"{last},{first}"] = rate

    log(f"[RATES] Loaded {len(rates)} entries.")
    return rates


def get_rate(name: str, rates: dict) -> str:
    parts = normalize(name).split()
    if len(parts) < 2:
        return ""

    first = parts[0]
    last = parts[-1]

    key = f"{last},{first}"
    if key in rates:
        return rates[key]

    for k in rates:
        if k.startswith(last + ","):
            return rates[k]

    return ""


def ocr_pdf(path: str, log) -> str:
    log(f"[OCR] Reading {path}")
    # poppler-utils is in PATH inside container
    images = convert_from_path(path)
    out = ""
    for img in images:
        out += pytesseract.image_to_string(img)
    return out.upper()


def make_pdf(group, name, rate, template_pdf: str, output_dir: str, log):
    start = group["start"]
    end = group["end"]
    ship = group["ship"]

    parts = name.split()
    last = parts[-1]
    first = " ".join(parts[:-1])

    prefix = f"{rate}_" if rate else ""
    filename = f"{prefix}{last}_{first}_{ship}_{start.replace('/','-')}_TO_{end.replace('/','-')}.pdf"
    filename = filename.replace(" ", "_")

    path = os.path.join(output_dir, filename)

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.setFont(FONT_NAME, FONT_SIZE)

    # ---------------- HEADER ----------------
    c.setFont(FONT_NAME, 10)
    c.drawString(39, 689, "AFLOAT TRAINING GROUP SAN DIEGO (UIC. 49365)")

    # CHECKBOX
    c.drawString(373, 671, "X")

    # ENTITLEMENT
    c.setFont(FONT_NAME, 8)
    c.drawString(39, 650, "ENTITLEMENT")

    # OPNAV INST
    c.drawString(345, 641, "OPNAVINST 7220.14")

    # Restore default font
    c.setFont(FONT_NAME, FONT_SIZE)

    # ---------------- NAME ----------------
    if rate:
        c.drawString(39, 41, f"{rate} {last}, {first}")
    else:
        c.drawString(39, 41, f"{last}, {first}")

    # ---------------- REMARKS ----------------
    c.drawString(38.84, 595, f"____. REPORT CAREER SEA PAY FROM {start} TO {end}.")
    c.drawString(64, 571, f"Member performed eight continuous hours per day on-board: {ship} Category A vessel.")

    # ---------------- CERTIFYING OFFICIAL BLOCK ----------------
    c.setFont(FONT_NAME, 10)

    # Signature line
    c.drawString(356.26, 499.5, "_________________________")

    # Label under it
    c.drawString(363.8, 487.5, "Certifying Official & Date")

    # Secondary signature line
    c.drawString(356.26, 427.5, "_________________________")

    # Name label
    c.drawString(384.1, 415.2, "FI MI Last Name")

    # SEA PAY CERTIFIER LABEL
    c.drawString(38.8, 83, "SEA PAY CERTIFIER")

    # USN AD LABEL
    c.drawString(503.5, 41, "USN AD")

    c.save()
    buf.seek(0)

    overlay = PdfReader(buf)
    template = PdfReader(template_pdf)

    base = template.pages[0]
    base.merge_page(overlay.pages[0])

    writer = PdfWriter()
    writer.add_page(base)

    for i in range(1, len(template.pages)):
        writer.add_page(template.pages[i])

    os.makedirs(output_dir, exist_ok=True)
    with open(path, "wb") as f:
        writer.write(f)

    log(f"[PDF] Created {path}")
    return path


def merge_with_bookmarks(output_dir: str, log):
    pdfs = sorted(
        f for f in os.listdir(output_dir)
        if f.lower().endswith(".pdf") and not f.startswith("MASTER")
    )

    if not pdfs:
        log("[MERGE] No PDFs to merge.")
        return None

    writer = PdfWriter()
    page = 0
    for file in pdfs:
        full = os.path.join(output_dir, file)
        reader = PdfReader(full)
        writer.add_outline_item(file.replace(".pdf", ""), page)
        for p in reader.pages:
            writer.add_page(p)
            page += 1

    out_path = os.path.join(output_dir, "MASTER_SEA_PAY_PACKET.pdf")
    with open(out_path, "wb") as f:
        writer.write(f)

    log(f"[MERGE] Master packet created: {out_path}")
    return out_path


def run_processor(data_dir, template_pdf, rate_file, output_dir):
    logs = []

    def log(msg):
        print(msg)
        logs.append(msg)

    log(f"[CONFIG] DATA       = {data_dir}")
    log(f"[CONFIG] TEMPLATE   = {template_pdf}")
    log(f"[CONFIG] RATE CSV   = {rate_file}")
    log(f"[CONFIG] OUTPUT DIR = {output_dir}")

    rates = load_rates(rate_file, log)

    if not os.path.isdir(data_dir):
        log(f"[ERROR] Data directory not found: {data_dir}")
        return logs

    files = [
        f for f in os.listdir(data_dir)
        if f.lower().endswith(".pdf") and "navpers" not in f.lower()
    ]

    if not files:
        log("[PROCESS] No input PDFs found.")
        return logs

    for file in files:
        path = os.path.join(data_dir, file)
        log(f"[PROCESS] ---- {file} ----")

        raw = strip_times(ocr_pdf(path, log))

        try:
            name = extract_member_name(raw)
            log(f"[NAME] {name}")
        except RuntimeError as e:
            log(f"[ERROR] {e}")
            continue

        year = extract_year_from_filename(path)
        rows = parse_rows(raw, year)
        groups = group_by_ship(rows)
        rate = get_rate(name, rates)

        if groups:
            for g in groups:
                make_pdf(g, name, rate, template_pdf, output_dir, log)
        else:
            log("[WARN] No valid sea-pay rows found in this file.")

    merge_with_bookmarks(output_dir, log)
    log("✅ ALL FILES COMPLETE")

    return logs


# ------------------------------------------------
# FLASK ROUTES
# ------------------------------------------------
@app.route("/", methods=["GET", "POST"])
def index():
    data_dir = DEFAULT_DATA_DIR
    template_pdf = DEFAULT_TEMPLATE_PDF
    rate_file = DEFAULT_RATE_FILE
    output_dir = DEFAULT_OUTPUT_DIR
    logs = []

    if request.method == "POST":
        data_dir = request.form.get("data_dir", data_dir)
        template_pdf = request.form.get("template_pdf", template_pdf)
        rate_file = request.form.get("rate_file", rate_file)
        output_dir = request.form.get("output_dir", output_dir)

        logs = run_processor(data_dir, template_pdf, rate_file, output_dir)

    return render_template(
        "index.html",
        data_dir=data_dir,
        template_pdf=template_pdf,
        rate_file=rate_file,
        output_dir=output_dir,
        logs="\n".join(logs)
    )


if __name__ == "__main__":
    # In Docker we will map container port 8080 → host 8092
    app.run(host="0.0.0.0", port=8080)
