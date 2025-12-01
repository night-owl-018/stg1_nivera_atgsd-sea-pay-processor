import os
import re
import io
import csv
import zipfile
from datetime import datetime, timedelta
from difflib import get_close_matches

from flask import Flask, render_template, request, redirect, send_from_directory
from PyPDF2 import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter

import pytesseract
from pdf2image import convert_from_path

# ------------------------------------------------
# GLOBAL LIVE LOG BUFFER ✅
# ------------------------------------------------
LOG_BUFFER = []

# ------------------------------------------------
# PATH CONFIG (DOCKER)
# ------------------------------------------------
DATA_DIR = "/data"
OUTPUT_BASE = "/output"
OUTPUT_DIR = OUTPUT_BASE
TEMPLATE_DIR = "/templates"
CONFIG_DIR = "/config"

TEMPLATE = os.path.join(TEMPLATE_DIR, "NAVPERS_1070_613_TEMPLATE.pdf")
RATE_FILE = os.path.join(CONFIG_DIR, "atgsd_n811.csv")
SHIP_FILE = "/app/ships.txt"

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(OUTPUT_BASE, exist_ok=True)
os.makedirs(TEMPLATE_DIR, exist_ok=True)
os.makedirs(CONFIG_DIR, exist_ok=True)

pytesseract.pytesseract.tesseract_cmd = "tesseract"
FONT_NAME = "Times-Roman"
FONT_SIZE = 10

# ------------------------------------------------
# OUTPUT SUBFOLDER CREATOR ✅
# ------------------------------------------------
def ensure_subfolder(name):
    safe = re.sub(r"[^A-Za-z0-9_\-]", "_", name)
    path = os.path.join(OUTPUT_BASE, safe)
    os.makedirs(path, exist_ok=True)
    return path

# ------------------------------------------------
# LOAD RATE CSV
# ------------------------------------------------
def _clean_header(h):
    return h.lstrip("\ufeff").strip().lower() if h else ""

def load_rates():
    rates = {}
    if not os.path.exists(RATE_FILE):
        return {}

    with open(RATE_FILE, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        reader.fieldnames = [_clean_header(h) for h in reader.fieldnames]

        for row in reader:
            last = row.get("last","").upper()
            first = row.get("first","").upper()
            rate = row.get("rate","").upper()
            if last and rate:
                rates[f"{last},{first}"] = rate
    return rates

RATES = load_rates()

# ------------------------------------------------
# LOAD SHIP LIST
# ------------------------------------------------
with open(SHIP_FILE, "r", encoding="utf-8") as f:
    SHIP_LIST = [line.strip() for line in f if line.strip()]

def normalize(text):
    text = re.sub(r"\(.*?\)", "", text)
    text = re.sub(r"[^A-Z ]", "", text.upper())
    return " ".join(text.split())

NORMALIZED_SHIPS = {normalize(s): s.upper() for s in SHIP_LIST}
NORMAL_KEYS = list(NORMALIZED_SHIPS.keys())

# ------------------------------------------------
# OCR
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
# NAME
# ------------------------------------------------
def extract_member_name(text):
    m = re.search(r"NAME:\s*([A-Z\s]+?)\s+SSN", text)
    if not m:
        raise RuntimeError("NAME not found")
    return " ".join(m.group(1).split())

# ------------------------------------------------
# SHIP MATCH
# ------------------------------------------------
def match_ship(raw_text):
    cleaned = normalize(raw_text)
    words = cleaned.split()

    for size in range(len(words), 0, -1):
        for i in range(len(words) - size + 1):
            chunk = " ".join(words[i:i+size])
            match = get_close_matches(chunk, NORMAL_KEYS, n=1, cutoff=0.75)
            if match:
                return NORMALIZED_SHIPS[match[0]]
    return None

# ------------------------------------------------
# DATE PARSING
# ------------------------------------------------
def extract_year_from_filename(fn):
    m = re.search(r"(20\d{2})", fn)
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
        y = ("20"+yy) if yy and len(yy)==2 else yy if yy else year
        date = f"{mm.zfill(2)}/{dd.zfill(2)}/{y}"

        raw = line[m.end():]
        if i + 1 < len(lines):
            raw += " " + lines[i+1]

        ship = match_ship(raw)
        if ship and (date, ship) not in seen:
            rows.append({"date":date,"ship":ship})
            seen.add((date,ship))
    return rows

# ------------------------------------------------
# GROUP DAYS
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

        for d in dates[1:]:
            if d == prev + timedelta(days=1):
                prev = d
            else:
                results.append({"ship":ship,"start":start,"end":prev})
                start = prev = d

        results.append({"ship":ship,"start":start,"end":prev})
    return results

# ------------------------------------------------
# RATE LOOKUP
# ------------------------------------------------
def get_rate(name):
    parts = normalize(name).split()
    if len(parts) < 2:
        return ""
    key = f"{parts[-1]},{parts[0]}"
    return RATES.get(key, "")

# ------------------------------------------------
# PDF
# ------------------------------------------------
def make_pdf(group, name):
    global OUTPUT_DIR

    start = group["start"].strftime("%m/%d/%Y")
    end = group["end"].strftime("%m/%d/%Y")
    ship = group["ship"]

    parts = name.split()
    last = parts[-1]
    first = " ".join(parts[:-1])

    rate = get_rate(name)
    prefix = f"{rate}_" if rate else ""

    filename = f"{prefix}{last}_{first}_{ship}_{start.replace('/','-')}_TO_{end.replace('/','-')}.pdf"
    filename = filename.replace(" ", "_")

    path = os.path.join(OUTPUT_DIR, filename)
    os.makedirs(os.path.dirname(path), exist_ok=True)

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.setFont(FONT_NAME, FONT_SIZE)

    c.drawString(39, 689, "AFLOAT TRAINING GROUP SAN DIEGO (UIC. 49365)")
    c.drawString(373, 671, "X")
    c.setFont(FONT_NAME, 8)
    c.drawString(39, 650, "ENTITLEMENT")
    c.drawString(345, 641, "OPNAVINST 7220.14")

    c.setFont(FONT_NAME, FONT_SIZE)

    c.drawString(39, 41, f"{rate} {last}, {first}" if rate else f"{last}, {first}")
    c.drawString(38.84, 595, f"____. REPORT CAREER SEA PAY FROM {start} TO {end}.")
    c.drawString(64, 571, f"Member performed eight continuous hours per day on-board: {ship} Category A vessel.")

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

    return filename

# ------------------------------------------------
# PROCESS
# ------------------------------------------------
def process_all():
    global LOG_BUFFER
    LOG_BUFFER = []

    files = [f for f in os.listdir(DATA_DIR) if f.lower().endswith(".pdf")]
    if not files:
        LOG_BUFFER.append("[INFO] No input PDFs found.")
        return

    for file in files:
        LOG_BUFFER.append(f"[OCR] {file}")
        raw = strip_times(ocr_pdf(os.path.join(DATA_DIR,file)))
        name = extract_member_name(raw)
        year = extract_year_from_filename(file)

        rows = parse_rows(raw, year)
        if not rows:
            ship = match_ship(raw)
            if ship:
                LOG_BUFFER.append(f"[FALLBACK] Ship found: {ship}")
                today = datetime.now()
                rows = [{"ship":ship,"date":today.strftime("%m/%d/%Y")}]
            else:
                LOG_BUFFER.append("[ERROR] No ship detected")
                continue

        for g in group_by_ship(rows):
            fname = make_pdf(g, name)
            LOG_BUFFER.append(f"[CREATED] {fname}")

    LOG_BUFFER.append("===== DONE =====")

# ------------------------------------------------
# FLASK ROUTES
# ------------------------------------------------
app = Flask(__name__, template_folder="web/frontend")

@app.route("/", methods=["GET","POST"])
def index():
    global OUTPUT_DIR

    if request.method == "POST":

        for f in request.files.getlist("files"):
            if f.filename:
                f.save(os.path.join(DATA_DIR,f.filename))

        tpl = request.files.get("template_file")
        if tpl and tpl.filename:
            tpl.save(TEMPLATE)

        csvf = request.files.get("rate_file")
        if csvf and csvf.filename:
            csvf.save(RATE_FILE)

        folder = request.form.get("output_folder","").strip()
        OUTPUT_DIR = ensure_subfolder(folder) if folder else OUTPUT_BASE

        process_all()

    return render_template("index.html", logs="\n".join(LOG_BUFFER),
                           template_path=TEMPLATE,
                           rate_path=RATE_FILE,
                           output_path=OUTPUT_DIR)

@app.route("/logs")
def logs():
    return "\n".join(LOG_BUFFER)

@app.route("/download_all")
def download_all():
    zip_path = "/tmp/sea_pay_output.zip"
    with zipfile.ZipFile(zip_path,"w",zipfile.ZIP_DEFLATED) as zipf:
        for f in os.listdir(OUTPUT_DIR):
            full = os.path.join(OUTPUT_DIR,f)
            if os.path.isfile(full):
                zipf.write(full,f)
    return send_from_directory("/tmp","sea_pay_output.zip",as_attachment=True)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
