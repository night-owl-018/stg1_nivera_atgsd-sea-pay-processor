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


# -------------------------------------------------
# PATH CONFIG
# -------------------------------------------------
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


# -------------------------------------------------
# SHIP LIST
# -------------------------------------------------
SHIP_LIST = []
if os.path.exists(SHIP_FILE):
    with open(SHIP_FILE, "r", encoding="utf-8") as f:
        SHIP_LIST = [line.strip() for line in f if line.strip()]

def normalize(text):
    text = re.sub(r"\(.*?\)", "", text.upper())
    text = re.sub(r"[^A-Z ]", "", text)
    return " ".join(text.split())

NORMALIZED = {normalize(s): s for s in SHIP_LIST}
NORMAL_KEYS = list(NORMALIZED.keys())


# -------------------------------------------------
# HELPERS
# -------------------------------------------------
def strip_times(text):
    return re.sub(r"\b\d{3,4}\b", "", text)

def extract_name(text):
    m = re.search(r"NAME:\s*([A-Z\s]+?)\s+SSN", text)
    if not m:
        raise RuntimeError("NAME NOT FOUND")
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


# -------------------------------------------------
# PARSER
# -------------------------------------------------
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

    output = []
    for ship, dates in groups.items():
        dates = sorted(set(dates))
        start = prev = dates[0]

        for d in dates[1:]:
            if d == prev + timedelta(days=1):
                prev = d
            else:
                output.append({"ship": ship, "start": start, "end": prev})
                start = prev = d

        output.append({"ship": ship, "start": start, "end": prev})

    return output


# -------------------------------------------------
# RATES
# -------------------------------------------------
def load_rates():
    rates = {}
    if not os.path.exists(RATES_FILE):
        return rates

    with open(RATES_FILE, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
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


# -------------------------------------------------
# PDF ENGINE
# -------------------------------------------------
def make_pdf(group, name, rate):
    start = group["start"].strftime("%m/%d/%Y")
    end = group["end"].strftime("%m/%d/%Y")
    ship = group["ship"]

    last = name.split()[-1]
    first = " ".join(name.split()[:-1])

    filename = f"{last}_{first}_{ship}_{start}_TO_{end}.pdf".replace(" ", "_")
    outpath = os.path.join(OUTPUT_DIR, filename)

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.setFont(FONT_NAME, 10)

    c.drawString(39, 689, "AFLOAT TRAINING GROUP SAN DIEGO (UIC 49365)")
    c.drawString(373, 671, "X")
    c.drawString(39, 650, "ENTITLEMENT")
    c.drawString(345, 641, "OPNAVINST 7220.14")
    c.drawString(39, 41, f"{rate} {last}, {first}")
    c.drawString(38, 595, f"REPORT CAREER SEA PAY FROM {start} TO {end}")
    c.drawString(64, 571, f"Member performed eight continuous hours per day on-board: {ship} Category A vessel")

    c.save()
    buf.seek(0)

    overlay = PdfReader(buf)
    base = PdfReader(TEMPLATE_PDF).pages[0]
    base.merge_page(overlay.pages[0])

    writer = PdfWriter()
    writer.add_page(base)

    with open(outpath, "wb") as f:
        writer.write(f)

    return outpath


# -------------------------------------------------
# PROCESSOR
# -------------------------------------------------
def process_all():
    logs = []
    rates = load_rates()

    logs.append("[START] Processing started")

    if not os.path.exists(TEMPLATE_PDF):
        return "[ERROR] TEMPLATE MISSING"

    pdfs = [f for f in os.listdir(DATA_DIR) if f.lower().endswith(".pdf")]
    logs.append(f"[INFO] PDFs found: {len(pdfs)}")

    for file in pdfs:
        path = os.path.join(DATA_DIR, file)
        logs.append(f"[OCR] {file}")

        text = strip_times("".join(
            pytesseract.image_to_string(img)
            for img in convert_from_path(path)
        )).upper()

        try:
            name = extract_name(text)
            logs.append(f"[NAME] {name}")
        except:
            logs.append("[ERROR] NAME NOT FOUND")
            continue

        rows = parse_rows(text, year_from_filename(file))
        groups = group_manifests(rows)
        rate = get_rate(name, rates)

        logs.append(f"[ROWS] {len(rows)}")
        logs.append(f"[GROUPS] {len(groups)}")

        if not groups:
            logs.append("[FALLBACK] GENERATING SINGLE PDF")
            today = datetime.today()
            groups = [{"ship": "UNKNOWN", "start": today, "end": today}]

        for g in groups:
            out = make_pdf(g, name, rate)
            logs.append(f"[PDF] CREATED -> {out}")

    return "\n".join(logs)


# -------------------------------------------------
# FLASK
# -------------------------------------------------
app = Flask(__name__, template_folder="web/frontend")

@app.route("/", methods=["GET", "POST"])
def index():
    logs = ""

    if request.method == "POST":
        for f in request.files.getlist("files"):
            if f.filename:
                f.save(os.path.join(DATA_DIR, f.filename))

        if "template_file" in request.files and request.files["template_file"].filename:
            request.files["template_file"].save(TEMPLATE_PDF)

        if "rate_file" in request.files and request.files["rate_file"].filename:
            request.files["rate_file"].save(RATES_FILE)

        logs = process_all()

    return render_template(
        "index.html",
        data_files=os.listdir(DATA_DIR),
        outputs=os.listdir(OUTPUT_DIR),
        logs=logs,
        template_path=TEMPLATE_PDF,
        rate_path=RATES_FILE
    )


@app.route("/download/<name>")
def download(name):
    return send_from_directory(OUTPUT_DIR, name, as_attachment=True)


@app.route("/delete/<folder>/<name>")
def delete(folder, name):
    base = DATA_DIR if folder == "data" else OUTPUT_DIR
    path = os.path.join(base, name)
    if os.path.exists(path):
        os.remove(path)
    return redirect("/")


@app.route("/browse_output")
def browse_output():
    files = os.listdir(OUTPUT_DIR)
    html = "<h2>/output folder</h2><ul>"
    for f in files:
        html += f'<li><a href="/download/{f}">{f}</a></li>'
    html += "</ul><br><a href='/'>BACK</a>"
    return html


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
