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
# FLASK APP (✅ CRITICAL: POINT TO CORRECT TEMPLATE FOLDER)
# ------------------------------------------------
app = Flask(__name__, template_folder="/app/web/templates")

# ------------------------------------------------
# PATH CONFIG (DOCKER VOLUMES)
# ------------------------------------------------
DATA_DIR = "/data"
OUTPUT_DIR = "/output"
TEMPLATE_DIR = "/templates"
CONFIG_DIR = "/config"

TEMPLATE_PDF = os.path.join(TEMPLATE_DIR, "NAVPERS_1070_613_TEMPLATE.pdf")
RATES_FILE = os.path.join(CONFIG_DIR, "atgsd_n811.csv")
SHIP_FILE = "/app/ships.txt"

FONT_NAME = "Times-Roman"

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(TEMPLATE_DIR, exist_ok=True)
os.makedirs(CONFIG_DIR, exist_ok=True)

pytesseract.pytesseract.tesseract_cmd = "tesseract"

# ------------------------------------------------
# LOAD SHIP LIST
# ------------------------------------------------
with open(SHIP_FILE, "r", encoding="utf-8") as f:
    SHIP_LIST = [line.strip() for line in f if line.strip()]


def normalize(text):
    text = re.sub(r"\(.*?\)", "", text.upper())
    text = re.sub(r"[^A-Z ]", "", text)
    return " ".join(text.split())


NORMALIZED = {normalize(s): s.upper() for s in SHIP_LIST}
NORMAL_KEYS = list(NORMALIZED.keys())

# ------------------------------------------------
# HELPERS
# ------------------------------------------------
def strip_times(text):
    return re.sub(r"\b\d{3,4}\b", "", text)


def extract_name(text):
    m = re.search(r"NAME:\s*([A-Z\s]+?)\s+SSN", text)
    if not m:
        raise RuntimeError("NAME not found")
    return m.group(1).strip()


def match_ship(text):
    text = normalize(text)
    words = text.split()
    for size in range(len(words), 0, -1):
        for i in range(len(words) - size + 1):
            chunk = " ".join(words[i:i + size])
            match = get_close_matches(chunk, NORMAL_KEYS, n=1, cutoff=0.75)
            if match:
                return NORMALIZED[match[0]]
    return None


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

        raw = line[m.end():]
        ship = match_ship(raw)

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
# RATE CSV
# ------------------------------------------------
def load_rates():
    rates = {}

    if not os.path.exists(RATES_FILE):
        return rates

    with open(RATES_FILE, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)

        for row in reader:
            last = row.get("last", "").upper()
            first = row.get("first", "").upper()
            rate = row.get("rate", "").upper()

            if last and rate:
                rates[f"{last},{first}"] = rate

    return rates


def get_rate(name, rates):
    parts = normalize(name).split()

    if len(parts) < 2:
        return ""

    key = f"{parts[-1]},{parts[0]}"
    return rates.get(key, "")

# ------------------------------------------------
# PDF CREATION
# ------------------------------------------------
def make_pdf(group, name, rate):
    start = group["start"].strftime("%m/%d/%Y")
    end = group["end"].strftime("%m/%d/%Y")
    ship = group["ship"]

    last = name.split()[-1]
    first = " ".join(name.split()[:-1])

    prefix = f"{rate}_" if rate else ""
    filename = f"{prefix}{last}_{first}_{ship}_{start}_TO_{end}.pdf".replace(" ", "_")
    outpath = os.path.join(OUTPUT_DIR, filename)

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.setFont(FONT_NAME, 10)

    c.drawString(39, 689, "AFLOAT TRAINING GROUP SAN DIEGO (UIC. 49365)")
    c.drawString(373, 671, "X")
    c.setFont(FONT_NAME, 8)
    c.drawString(39, 650, "ENTITLEMENT")
    c.drawString(345, 641, "OPNAVINST 7220.14")

    c.setFont(FONT_NAME, 10)
    c.drawString(39, 41, f"{rate} {last}, {first}" if rate else f"{last}, {first}")
    c.drawString(38.8, 595, f"____. REPORT CAREER SEA PAY FROM {start} TO {end}.")
    c.drawString(64, 571, f"Member performed eight continuous hours per day on-board: {ship} Category A vessel.")

    c.drawString(356, 499, "_________________________")
    c.drawString(364, 487, "Certifying Official & Date")
    c.drawString(356, 427, "_________________________")
    c.drawString(385, 415, "FI MI Last Name")
    c.drawString(39, 83, "SEA PAY CERTIFIER")
    c.drawString(504, 41, "USN AD")

    c.save()
    buf.seek(0)

    overlay = PdfReader(buf)

    template_reader = PdfReader(TEMPLATE_PDF)
    base = template_reader.pages[0]
    base.merge_page(overlay.pages[0])

    writer = PdfWriter()
    writer.add_page(base)

    with open(outpath, "wb") as f:
        writer.write(f)

# ------------------------------------------------
# PROCESS
# ------------------------------------------------
def process_all():
    logs = []
    rates = load_rates()

    for file in os.listdir(DATA_DIR):

        if not file.lower().endswith(".pdf"):
            continue

        path = os.path.join(DATA_DIR, file)
        logs.append(f"[OCR] {file}")

        text = strip_times(
            "".join(
                pytesseract.image_to_string(img)
                for img in convert_from_path(path)
            )
        ).upper()

        try:
            name = extract_name(text)
            logs.append(f"[NAME] {name}")
        except:
            logs.append("[ERROR] Name not found")
            continue

        rows = parse_rows(text, year_from_filename(file))
        groups = group_manifests(rows)
        rate = get_rate(name, rates)

        for g in groups:
            make_pdf(g, name, rate)
            logs.append("[PDF] CREATED")

    return logs

# ------------------------------------------------
# ROUTES
# ------------------------------------------------
@app.route("/", methods=["GET", "POST"])
def index():
    logs = []

    if request.method == "POST":

        # upload PDFs
        for f in request.files.getlist("files"):
            if f.filename:
                f.save(os.path.join(DATA_DIR, f.filename))

        # replace template
        template = request.files.get("template_file")
        if template and template.filename:
            template.save(TEMPLATE_PDF)

        # replace CSV
        rate = request.files.get("rate_file")
        if rate and rate.filename:
            rate.save(RATES_FILE)

        logs = process_all()

    return render_template(
        "index.html",
        data_files=os.listdir(DATA_DIR),
        outputs=os.listdir(OUTPUT_DIR),
        logs="\n".join(logs),
        template_path=TEMPLATE_PDF,
        rate_path=RATES_FILE,
        output_path=OUTPUT_DIR,
    )

@app.route("/delete/<folder>/<name>")
def delete(folder, name):
    base = DATA_DIR if folder == "data" else OUTPUT_DIR if folder == "output" else None

    if base:
        path = os.path.join(base, name)
        if os.path.exists(path):
            os.remove(path)

    return redirect("/")

@app.route("/download/<name>")
def download(name):
    return send_from_directory(OUTPUT_DIR, name, as_attachment=True)

# ------------------------------------------------
# ENTRY
# ------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
