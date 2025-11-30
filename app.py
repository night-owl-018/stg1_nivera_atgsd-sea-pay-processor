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
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

import pytesseract
from pdf2image import convert_from_path

# -----------------------------------------
# FOLDER LAYOUT
# -----------------------------------------
DATA_DIR = "/data"
OUTPUT_DIR = "/output"
TEMPLATE_DIR = "/templates"
CONFIG_DIR = "/config"
APP_DIR = "/app"

TEMPLATE_PDF = os.path.join(TEMPLATE_DIR, "NAVPERS_1070_613_TEMPLATE.pdf")
RATES_FILE = os.path.join(CONFIG_DIR, "atgsd_n811.csv")
SHIP_FILE = os.path.join(APP_DIR, "ships.txt")
FONT_FILE = os.path.join(APP_DIR, "Times_New_Roman.ttf")

for p in [DATA_DIR, OUTPUT_DIR, TEMPLATE_DIR, CONFIG_DIR]:
    os.makedirs(p, exist_ok=True)

# -----------------------------------------
# FONT
# -----------------------------------------
pdfmetrics.registerFont(TTFont("TNR", FONT_FILE))
FONT_NAME = "TNR"
FONT_SIZE = 10

# -----------------------------------------
# OCR
# -----------------------------------------
pytesseract.pytesseract.tesseract_cmd = "tesseract"

# -----------------------------------------
# SHIPS
# -----------------------------------------
with open(SHIP_FILE, "r", encoding="utf-8") as f:
    SHIP_LIST = [line.strip() for line in f if line.strip()]

def normalize(text):
    text = re.sub(r"\(.*?\)", "", text.upper())
    text = re.sub(r"[^A-Z ]", "", text)
    return " ".join(text.split())

NORMALIZED = {normalize(s): s.upper() for s in SHIP_LIST}
NORMAL_KEYS = list(NORMALIZED.keys())

# -----------------------------------------
# RATES
# -----------------------------------------
def load_rates():
    rates = {}
    if not os.path.exists(RATES_FILE): return rates

    with open(RATES_FILE, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for r in reader:
            last = r.get("last","").upper().strip()
            first = r.get("first","").upper().strip()
            rate = r.get("rate","").upper().strip()
            if last:
                rates[f"{last},{first}"] = rate
    return rates

def get_rate(name, rates):
    parts = normalize(name).split()
    if len(parts) < 2: return ""
    return rates.get(f"{parts[-1]},{parts[0]}", "")

# -----------------------------------------
# OCR
# -----------------------------------------
def ocr_pdf(path):
    text = ""
    for img in convert_from_path(path):
        text += pytesseract.image_to_string(img)
    return text.upper()

def strip_times(text):
    return re.sub(r"\b\d{3,4}\b", "", text)

def extract_name(text):
    m = re.search(r"NAME:\s*([A-Z\s]+?)\s+SSN", text)
    return m.group(1).strip() if m else "UNKNOWN"

def match_ship(text):
    text = normalize(text)
    words = text.split()
    for size in range(len(words), 0, -1):
        for i in range(len(words) - size + 1):
            chunk = " ".join(words[i:i+size])
            m = get_close_matches(chunk, NORMAL_KEYS, n=1, cutoff=0.75)
            if m:
                return NORMALIZED[m[0]]
    return None

# -----------------------------------------
# DATES
# -----------------------------------------
def year_from_filename(fn):
    m = re.search(r"(20\d{2})", fn)
    return m.group(1) if m else str(datetime.now().year)

def parse_rows(text, year):
    rows, seen = [], set()

    for line in text.splitlines():
        m = re.match(r"\s*(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?", line)
        if not m: continue

        mm, dd, yy = m.groups()
        y = ("20"+yy) if yy and len(yy)==2 else yy if yy else year
        date = f"{mm.zfill(2)}/{dd.zfill(2)}/{y}"

        ship = match_ship(line[m.end():])
        if ship and (date,ship) not in seen:
            seen.add((date,ship))
            rows.append({"date":date,"ship":ship})
    return rows

def group(rows):
    out={}
    for r in rows:
        dt = datetime.strptime(r["date"],"%m/%d/%Y")
        out.setdefault(r["ship"],[]).append(dt)

    results=[]
    for ship,dates in out.items():
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

# -----------------------------------------
# PDF CORE (LOCKED COORDINATES)
# -----------------------------------------
def make_pdf(group, name, rate):
    start = group["start"].strftime("%m/%d/%Y")
    end = group["end"].strftime("%m/%d/%Y")
    ship = group["ship"]

    last = name.split()[-1]
    first = " ".join(name.split()[:-1])

    filename = f"{last}, {first} - {ship} {start} - {end}.pdf"
    outpath = os.path.join(OUTPUT_DIR, filename)

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.setFont(FONT_NAME, FONT_SIZE)

    # Header
    c.drawString(39,689,"AFLOAT TRAINING GROUP SAN DIEGO (UIC 49365)")
    c.drawString(373,671,"X")
    c.setFont(FONT_NAME,8)
    c.drawString(39,650,"ENTITLEMENT")
    c.drawString(345,641,"OPNAVINST 7220.14")

    # Body
    c.setFont(FONT_NAME,10)
    c.drawString(39,595,f"____. REPORT CAREER SEA PAY FROM {start} TO {end}.")
    c.drawString(64,571,f"Member performed eight continuous hours per day on-board: {ship} Category A vessel.")
    c.drawString(39,41,f"{rate} {last}, {first}" if rate else f"{last}, {first}")

    # Footer
    c.drawString(356,499,"_________________________")
    c.drawString(364,487,"Certifying Official & Date")
    c.drawString(356,427,"_________________________")
    c.drawString(385,415,"FI MI Last Name")
    c.drawString(504,41,"USN AD")

    c.save()
    buf.seek(0)

    overlay = PdfReader(buf)
    base = PdfReader(TEMPLATE_PDF).pages[0]
    base.merge_page(overlay.pages[0])

    writer = PdfWriter()
    writer.add_page(base)

    with open(outpath,"wb") as f:
        writer.write(f)

# -----------------------------------------
# ENGINE
# -----------------------------------------
def process_all():
    logs=[]
    rates = load_rates()

    pdfs=[f for f in os.listdir(DATA_DIR) if f.lower().endswith(".pdf")]
    if not pdfs:
        return "[INFO] No input PDFs found."

    for file in pdfs:
        path=os.path.join(DATA_DIR,file)
        logs.append(f"[OCR] {file}")

        raw = strip_times(ocr_pdf(path))
        name = extract_name(raw)
        logs.append(f"[NAME] {name}")

        rows=parse_rows(raw, year_from_filename(file))
        groups=group(rows)
        rate=get_rate(name,rates)

        for g in groups:
            make_pdf(g,name,rate)
            logs.append(f"[PDF] {g['ship']} {g['start']} → {g['end']}")

    return "\n".join(logs)

# -----------------------------------------
# FLASK UI
# -----------------------------------------
app=Flask(__name__, template_folder="web/frontend")

@app.route("/", methods=["GET","POST"])
def index():
    logs=""

    if request.method=="POST":
        for f in request.files.getlist("files"):
            if f.filename:
                f.save(os.path.join(DATA_DIR,f.filename))

        if request.files.get("template_file"):
            request.files["template_file"].save(TEMPLATE_PDF)

        if request.files.get("rate_file"):
            request.files["rate_file"].save(RATES_FILE)

        logs = process_all()

    return render_template("index.html",
        data_files=os.listdir(DATA_DIR),
        outputs=os.listdir(OUTPUT_DIR),
        logs=logs,
        template_path=TEMPLATE_PDF,
        rate_path=RATES_FILE
    )

@app.route("/download/<name>")
def download(name):
    return send_from_directory(OUTPUT_DIR,name,as_attachment=True)

@app.route("/delete/<folder>/<name>")
def delete(folder,name):
    base = DATA_DIR if folder=="data" else OUTPUT_DIR
    path=os.path.join(base,name)
    if os.path.exists(path): os.remove(path)
    return redirect("/")

if __name__=="__main__":
    app.run(host="0.0.0.0",port=8080)
