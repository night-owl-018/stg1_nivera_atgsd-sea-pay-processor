import os
import csv
import re
import io
from datetime import datetime, timedelta
from difflib import get_close_matches

from flask import Flask, request, send_from_directory
from PyPDF2 import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
import pytesseract
from pdf2image import convert_from_path


# ------------------------------------------------
# PATH CONFIG (DOCKER MOUNTED)
# ------------------------------------------------
ROOT = "/app"
DATA_DIR = "/data"
OUTPUT_DIR = "/output"
TEMPLATE_DIR = "/templates"
CONFIG_DIR = "/config"

TEMPLATE_PDF = os.path.join(TEMPLATE_DIR, "NAVPERS_1070_613_TEMPLATE.pdf")
RATES_FILE = os.path.join(CONFIG_DIR, "atgsd_n811.csv")
SHIP_FILE = os.path.join(ROOT, "ships.txt")

FONT_NAME = "Times-Roman"


os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(TEMPLATE_DIR, exist_ok=True)
os.makedirs(CONFIG_DIR, exist_ok=True)

pytesseract.pytesseract.tesseract_cmd = "tesseract"


# ------------------------------------------------
# SHIP MATCHING
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
    return rates.get(f"{parts[-1]},{parts[0]}", "")


# ------------------------------------------------
# PDF GENERATION
# ------------------------------------------------

def make_pdf(group, name, rate):
    start = group["start"].strftime("%m/%d/%Y")
    end = group["end"].strftime("%m/%d/%Y")
    ship = group["ship"]

    last = name.split()[-1]
    first = " ".join(name.split()[:-1])

    fname = f"{rate + '_' if rate else ''}{last}_{first}_{ship}_{start}_TO_{end}.pdf"
    fname = fname.replace(" ", "_")

    outpath = os.path.join(OUTPUT_DIR, fname)

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.setFont(FONT_NAME, 10)

    c.drawString(39, 689, "AFLOAT TRAINING GROUP SAN DIEGO (UIC 49365)")
    c.drawString(373, 671, "X")
    c.drawString(39, 650, "ENTITLEMENT")
    c.drawString(345, 641, "OPNAVINST 7220.14")

    c.drawString(38, 595, f"REPORT CAREER SEA PAY FROM {start} TO {end}")
    c.drawString(64, 571, f"Member performed eight continuous hours aboard {ship}")
    c.drawString(39, 41, f"{last}, {first}")

    c.save()
    buf.seek(0)

    overlay = PdfReader(buf)
    base = PdfReader(TEMPLATE_PDF).pages[0]
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
        logs.append(f"OCR: {file}")

        text = strip_times(
            "".join(pytesseract.image_to_string(img) for img in convert_from_path(path))
        ).upper()

        try:
            name = extract_name(text)
        except:
            logs.append("ERROR: Name not found")
            continue

        rows = parse_rows(text, year_from_filename(file))
        groups = group_manifests(rows)
        rate = get_rate(name, rates)

        for g in groups:
            make_pdf(g, name, rate)
            logs.append("PDF CREATED")

    return logs


# ------------------------------------------------
# FLASK SERVER
# ------------------------------------------------

app = Flask(
    __name__,
    static_folder="/app/web/frontend",
    template_folder=None
)


@app.route("/", methods=["GET", "POST"])
def index():

    if request.method == "POST":

        for f in request.files.getlist("files"):
            if f.filename:
                f.save(os.path.join(DATA_DIR, f.filename))

        tpl = request.files.get("template_file")
        if tpl and tpl.filename:
            tpl.save(TEMPLATE_PDF)

        rate = request.files.get("rate_file")
        if rate and rate.filename:
            rate.save(RATES_FILE)

        process_all()

    return send_from_directory("/app/web/frontend", "index.html")


@app.route("/download/<name>")
def download(name):
    return send_from_directory(OUTPUT_DIR, name, as_attachment=True)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
