import os
import csv
import re
import io
from datetime import datetime, timedelta
from difflib import get_close_matches

from flask import Flask, render_template, request, redirect, send_from_directory
from PyPDF2 import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
import pytesseract
from pdf2image import convert_from_path

# ------------------------------------------------
# PATH CONFIG
# ------------------------------------------------
DATA_DIR = "/data"
OUTPUT_DIR = "/output"
TEMPLATE_DIR = "/templates"
CONFIG_DIR = "/config"

TEMPLATE_PDF = os.path.join(TEMPLATE_DIR, "NAVPERS_1070_613_TEMPLATE.pdf")
RATES_FILE = os.path.join(CONFIG_DIR, "atgsd_n811.csv")
SHIP_FILE = "/app/ships.txt"

FONT_NAME = "Times-Roman"

for p in [DATA_DIR, OUTPUT_DIR, TEMPLATE_DIR, CONFIG_DIR]:
    os.makedirs(p, exist_ok=True)

pytesseract.pytesseract.tesseract_cmd = "tesseract"

# ------------------------------------------------
# LOAD SHIPS
# ------------------------------------------------
if not os.path.exists(SHIP_FILE):
    raise RuntimeError("ships.txt not found in container")

with open(SHIP_FILE, "r", encoding="utf-8") as f:
    SHIP_LIST = [s.strip().upper() for s in f if s.strip()]

def normalize(text):
    return re.sub(r"[^A-Z ]", "", text.upper()).strip()

NORMALIZED = {normalize(s): s for s in SHIP_LIST}
NORMAL_KEYS = list(NORMALIZED.keys())

# ------------------------------------------------
# HELPERS
# ------------------------------------------------
def strip_times(text):
    return re.sub(r"\b\d{3,4}\b", "", text)

def extract_name(text):
    m = re.search(r"NAME[:\s]+([A-Z ]+?)\s+SSN", text)
    if not m:
        raise RuntimeError("NAME field not found")
    return normalize(m.group(1))

def match_ship(text):
    text = normalize(text)
    matches = get_close_matches(text, NORMAL_KEYS, n=1, cutoff=0.85)
    if not matches:
        return None
    return NORMALIZED[matches[0]]

def year_from_filename(fn):
    m = re.search(r"(20\d{2})", fn)
    return m.group(1) if m else str(datetime.now().year)

def parse_rows(text, year):
    rows = []
    seen = set()

    for line in text.splitlines():
        m = re.match(r"\s*(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?", line)
        if not m:
            continue

        mm, dd, yy = m.groups()
        y = ("20" + yy) if yy and len(yy) == 2 else yy if yy else year
        date = f"{mm.zfill(2)}/{dd.zfill(2)}/{y}"

        ship = match_ship(line)
        if ship and (date, ship) not in seen:
            rows.append({"date": date, "ship": ship})
            seen.add((date, ship))

    return rows

def group_manifests(rows):
    groups = {}
    for r in rows:
        dt = datetime.strptime(r["date"], "%m/%d/%Y")
        groups.setdefault(r["ship"], []).append(dt)

    out = []
    for ship, dates in groups.items():
        dates = sorted(set(dates))
        start = prev = dates[0]

        for day in dates[1:]:
            if day == prev + timedelta(days=1):
                prev = day
            else:
                out.append({"ship": ship, "start": start, "end": prev})
                start = prev = day

        out.append({"ship": ship, "start": start, "end": prev})
    return out

# ------------------------------------------------
# RATE FILE
# ------------------------------------------------
def load_rates():
    rates = {}
    if not os.path.exists(RATES_FILE):
        return rates

    with open(RATES_FILE, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            last = normalize(row.get("last", ""))
            first = normalize(row.get("first", ""))
            rate = normalize(row.get("rate", ""))
            if last and rate:
                rates[f"{last},{first}"] = rate
    return rates

def get_rate(name, rates):
    parts = name.split()
    if len(parts) < 2:
        return ""
    return rates.get(f"{parts[-1]},{parts[0]}", "")

# ------------------------------------------------
# PDF GENERATION
# ------------------------------------------------
def sanitize_filename(text):
    return re.sub(r"[^A-Z0-9_]", "_", text)

def make_pdf(group, name, rate):
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    start_str = group["start"].strftime("%Y-%m-%d")
    end_str = group["end"].strftime("%Y-%m-%d")
    ship = sanitize_filename(group["ship"])
    last = name.split()[-1]
    first = name.split()[0]

    rate = rate or "UNKNOWN"
    filename = f"{rate}_{last}_{first}_{ship}_{start_str}_TO_{end_str}.pdf"
    outpath = os.path.join(OUTPUT_DIR, filename)

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.setFont(FONT_NAME, 10)

    # PLACEHOLDER LOCATIONS — WILL ALIGN IMMEDIATELY WHEN YOU GIVE COORDS
    c.drawString(60, 720, name)
    c.drawString(60, 700, rate)
    c.drawString(60, 680, ship)
    c.drawString(60, 660, f"{start_str} TO {end_str}")

    c.save()
    buf.seek(0)

    template = PdfReader(TEMPLATE_PDF)
    overlay = PdfReader(buf)

    base = template.pages[0]
    base.merge_page(overlay.pages[0])

    writer = PdfWriter()
    writer.add_page(base)

    with open(outpath, "wb") as f:
        writer.write(f)

    return filename

# ------------------------------------------------
# PROCESSOR
# ------------------------------------------------
def process_all():
    logs = []
    rates = load_rates()

    if not os.path.exists(TEMPLATE_PDF):
        return "[ERROR] NAVPERS template missing"

    input_files = [f for f in os.listdir(DATA_DIR) if f.lower().endswith(".pdf")]
    if not input_files:
        return "[INFO] No PDFs uploaded"

    for file in input_files:
        path = os.path.join(DATA_DIR, file)
        logs.append(f"[OCR] {file}")

        text = strip_times("".join(
            pytesseract.image_to_string(img)
            for img in convert_from_path(path)
        )).upper()

        try:
            name = extract_name(text)
        except:
            logs.append("[ERROR] NAME not found")
            continue

        rows = parse_rows(text, year_from_filename(file))
        groups = group_manifests(rows)
        if not groups:
            logs.append("[ERROR] No ship match found")
            continue

        rate = get_rate(name, rates)

        for g in groups:
            fname = make_pdf(g, name, rate)
            logs.append(f"[OK] {fname}")

    return "\n".join(logs)

# ------------------------------------------------
# FLASK
# ------------------------------------------------
app = Flask(__name__, template_folder="web/frontend")

@app.route("/", methods=["GET", "POST"])
def index():
    logs = ""

    if request.method == "POST":
        for f in request.files.getlist("files"):
            if f.filename:
                f.save(os.path.join(DATA_DIR, f.filename))

        if request.files.get("template_file"):
            request.files["template_file"].save(TEMPLATE_PDF)

        if request.files.get("rate_file"):
            request.files["rate_file"].save(RATES_FILE)

        logs = process_all()

    return render_template(
        "index.html",
        data_files=os.listdir(DATA_DIR),
        outputs=os.listdir(OUTPUT_DIR),
        logs=logs,
        template_path=TEMPLATE_PDF,
        rate_path=RATES_FILE,
        output_path=OUTPUT_DIR
    )

@app.route("/download/<name>")
def download(name):
    return send_from_directory(OUTPUT_DIR, name, as_attachment=True)

@app.route("/delete/<folder>/<name>")
def delete(folder, name):
    base = DATA_DIR if folder == "data" else OUTPUT_DIR
    try:
        os.remove(os.path.join(base, name))
    except:
        pass
    return redirect("/")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
