import os
import re
import io
import csv
import zipfile
import tempfile
from collections import deque
from datetime import datetime, timedelta
from difflib import get_close_matches, SequenceMatcher

from flask import Flask, render_template, request, send_from_directory, jsonify
from PyPDF2 import PdfReader, PdfWriter, PdfMerger
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter

import pytesseract
from pdf2image import convert_from_path

# ------------------------------------------------
# PATHS
# ------------------------------------------------
DATA_DIR = "/data"
OUTPUT_DIR = "/output"
TEMPLATE_DIR = "/templates"
CONFIG_DIR = "/config"
SHIP_FILE = "/app/ships.txt"

TEMPLATE = os.path.join(TEMPLATE_DIR, "NAVPERS_1070_613_TEMPLATE.pdf")
RATE_FILE = os.path.join(CONFIG_DIR, "atgsd_n811.csv")

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(TEMPLATE_DIR, exist_ok=True)
os.makedirs(CONFIG_DIR, exist_ok=True)

pytesseract.pytesseract.tesseract_cmd = "tesseract"
FONT_NAME = "Times-Roman"
FONT_SIZE = 10

# ------------------------------------------------
# LIVE LOG MEMORY
# ------------------------------------------------
LIVE_LOGS = deque(maxlen=500)

def log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    LIVE_LOGS.append(line)

# ------------------------------------------------
# CLEANUP FUNCTIONS
# ------------------------------------------------

def cleanup_folder(folder_path, folder_name):
    try:
        count = 0
        for f in os.listdir(folder_path):
            fp = os.path.join(folder_path, f)
            if os.path.isfile(fp):
                os.remove(fp)
                count += 1
        if count:
            log(f"🗑️ CLEANED {folder_name}: {count} files")
        return count
    except Exception as e:
        log(f"❌ ERROR CLEANING {folder_name}: {e}")
        return 0

def cleanup_all_folders():
    log("=== CLEANUP STARTED ===")
    total = 0
    total += cleanup_folder(DATA_DIR, "DATA")
    total += cleanup_folder(OUTPUT_DIR, "OUTPUT")
    log(f"✅ CLEANUP COMPLETE: {total} files deleted")
    return total

# ------------------------------------------------
# LOAD RATES
# ------------------------------------------------

def _clean_header(h):
    return h.lstrip("\ufeff").strip().strip('"').lower() if h else ""

def load_rates():
    rates = {}
    if not os.path.exists(RATE_FILE):
        log("RATE FILE MISSING")
        return rates

    with open(RATE_FILE, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        reader.fieldnames = [_clean_header(h) for h in reader.fieldnames]

        for row in reader:
            last = (row.get("last") or "").upper().strip()
            first = (row.get("first") or "").upper().strip()
            rate = (row.get("rate") or "").upper().strip()
            if last and rate:
                rates[f"{last},{first}"] = rate

    log(f"RATES LOADED: {len(rates)}")
    return rates

RATES = load_rates()

CSV_IDENTITIES = []
def normalize_for_id(text):
    t = re.sub(r"\(.*?\)", "", text.upper())
    t = re.sub(r"[^A-Z ]", "", t)
    return " ".join(t.split())

for key, rate in RATES.items():
    last, first = key.split(",", 1)
    CSV_IDENTITIES.append((normalize_for_id(f"{first} {last}"), rate, last, first))

# ------------------------------------------------
# LOAD SHIPS
# ------------------------------------------------

with open(SHIP_FILE, "r", encoding="utf-8") as f:
    SHIP_LIST = [line.strip() for line in f if line.strip()]

def normalize(text):
    text = re.sub(r"\(.*?\)", "", text.upper())
    text = re.sub(r"[^A-Z ]", "", text)
    return " ".join(text.split())

NORMALIZED_SHIPS = {normalize(s): s.upper() for s in SHIP_LIST}
NORMAL_KEYS = list(NORMALIZED_SHIPS.keys())

# ------------------------------------------------
# OCR + PARSING
# ------------------------------------------------

def strip_times(text):
    return re.sub(r"\b[0-2]?\d[0-5]\d\b", "", text)

def ocr_pdf(path):
    images = convert_from_path(path)
    out = ""
    for img in images:
        out += pytesseract.image_to_string(img)
    return out.upper()

def extract_member_name(text):
    m = re.search(r"NAME:\s*([A-Z\s]+?)\s+SSN", text)
    if not m:
        raise RuntimeError("NAME NOT FOUND")
    return " ".join(m.group(1).split())

def match_ship(raw):
    cand = normalize(raw)
    words = cand.split()
    for size in range(len(words), 0, -1):
        for i in range(len(words)-size+1):
            chunk = " ".join(words[i:i+size])
            match = get_close_matches(chunk, NORMAL_KEYS, n=1, cutoff=0.75)
            if match:
                return NORMALIZED_SHIPS[match[0]]
    return None

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
        y = ("20" + yy) if yy and len(yy) == 2 else yy or year
        date = f"{mm.zfill(2)}/{dd.zfill(2)}/{y}"

        raw = line[m.end():]
        if i+1 < len(lines):
            raw += " " + lines[i+1]

        ship = match_ship(raw)
        if ship and (date, ship) not in seen:
            rows.append({"date": date, "ship": ship})
            seen.add((date, ship))

    return rows

def group_by_ship(rows):
    grouped = {}
    for r in rows:
        dt = datetime.strptime(r["date"], "%m/%d/%Y")
        grouped.setdefault(r["ship"], []).append(dt)

    out = []
    for ship, dates in grouped.items():
        dates = sorted(set(dates))
        start = prev = dates[0]

        for d in dates[1:]:
            if d == prev + timedelta(days=1):
                prev = d
            else:
                out.append({"ship": ship, "start": start, "end": prev})
                start = prev = d

        out.append({"ship": ship, "start": start, "end": prev})

    return out

# ------------------------------------------------
# MATCH RATE
# ------------------------------------------------

def lookup_csv_identity(name):
    ocr_norm = normalize_for_id(name)
    best = None
    score = 0.0

    for csv_norm, rate, last, first in CSV_IDENTITIES:
        s = SequenceMatcher(None, ocr_norm, csv_norm).ratio()
        if s > score:
            score = s
            best = (rate, last, first)

    if best and score >= 0.6:
        log(f"CSV MATCH ({score:.2f}) → {best}")
        return best

    log(f"CSV MATCH FAIL ({score:.2f}) for {name}")
    return None

# ------------------------------------------------
# PDF CREATION
# ------------------------------------------------

def make_pdf(group, name):
    csv = lookup_csv_identity(name)
    if csv:
        rate, last, first = csv
    else:
        parts = name.split()
        last = parts[-1]
        first = " ".join(parts[:-1])
        rate = RATES.get(f"{last},{first}", "")

    start = group["start"].strftime("%m/%d/%Y")
    end = group["end"].strftime("%m/%d/%Y")
    ship = group["ship"]

    prefix = f"{rate}_" if rate else ""
    filename = f"{prefix}{last}_{first}_{ship}_{start.replace('/','-')}_TO_{end.replace('/','-')}.pdf".replace(" ","_")
    outpath = os.path.join(OUTPUT_DIR, filename)

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
    c.drawString(38.8, 595, f"____. REPORT CAREER SEA PAY FROM {start} TO {end}.")
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

    with open(outpath, "wb") as f:
        writer.write(f)

    log(f"CREATED → {filename}")

# ------------------------------------------------
# MERGE ALL PDFs
# ------------------------------------------------

def merge_all_pdfs():
    pdfs = sorted(f for f in os.listdir(OUTPUT_DIR) if f.endswith(".pdf") and not f.startswith("MERGED_"))

    if not pdfs:
        log("NO PDFs TO MERGE")
        return None

    merger = PdfMerger()
    for f in pdfs:
        merger.append(os.path.join(OUTPUT_DIR, f), outline_item=f.replace(".pdf",""))

    outname = f"MERGED_SeaPay_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
    outpath = os.path.join(OUTPUT_DIR, outname)
    merger.write(outpath)
    merger.close()

    log(f"MERGED → {outname}")
    return outname

# ------------------------------------------------
# PROCESSING ENGINE
# ------------------------------------------------

def process_all():

    files = [f for f in os.listdir(DATA_DIR) if f.lower().endswith(".pdf")]

    if not files:
        log("NO INPUT FILES")
        return

    log("=== PROCESSING STARTED ===")

    for fname in files:
        path = os.path.join(DATA_DIR, fname)

        log(f"OCR → {fname}")
        raw = strip_times(ocr_pdf(path))

        try:
            name = extract_member_name(raw)
            log(f"NAME → {name}")
        except:
            log("NAME NOT FOUND")
            continue

        year = extract_year_from_filename(fname)
        rows = parse_rows(raw, year)

        if not rows:
            log(f"NO SHIP MATCHES in {fname}")
            continue

        groups = group_by_ship(rows)

        for g in groups:
            make_pdf(g, name)

    merge_all_pdfs()
    log("=== PROCESS COMPLETE ===")

# ------------------------------------------------
# TEXT REPORT FEATURE
# ------------------------------------------------

EXCLUDED = ["MITE", "SBTT"]

def generate_text_report():

    files = [f for f in os.listdir(DATA_DIR) if f.lower().endswith(".pdf")]
    if not files:
        return None, "NO INPUT FILES"

    lines = []

    for fname in files:
        path = os.path.join(DATA_DIR, fname)
        raw = strip_times(ocr_pdf(path))

        try:
            name = extract_member_name(raw)
        except:
            continue

        last_first = ", ".join(name.split()[::-1])
        lines.append("="*30)
        lines.append(last_first)
        lines.append("="*30)

        skipped = []
        rows = []
        year = extract_year_from_filename(fname)

        for line in raw.splitlines():

            if any(key in line for key in EXCLUDED):
                skipped.append(f"- Original: '{line.strip()}' (Excluded keyword)")
                continue

            m = re.match(r"\s*(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?", line)
            if not m:
                continue

            mm, dd, yy = m.groups()
            y = ("20" + yy) if yy and len(yy)==2 else yy or year
            date = f"{mm.zfill(2)}/{dd.zfill(2)}/{y}"

            ship = match_ship(line)
            if not ship:
                skipped.append(f"- Original: '{line.strip()}' (No ship match)")
                continue

            rows.append({"date": date, "ship": ship})

        groups = group_by_ship(rows)

        total_days = 0

        for g in groups:
            s = g["start"].strftime("%m/%d/%Y")
            e = g["end"].strftime("%m/%d/%Y")
            d = (g["end"] - g["start"]).days + 1
            total_days += d

            lines.append(f"{g['ship']} – FROM {s} TO {e} ({d} days)")

        lines.append(f"TOTAL DAYS: {total_days}")

        if skipped:
            lines.append("Skipped:")
            lines.extend(skipped)
        else:
            lines.append("Skipped: NONE")

        lines.append("")

    out_path = os.path.join(OUTPUT_DIR, "SEA_PAY_TEXT_REPORT.txt")
    with open(out_path, "w") as f:
        f.write("\n".join(lines))

    return out_path, None

# ------------------------------------------------
# WEB UI
# ------------------------------------------------

app = Flask(__name__, template_folder="web/frontend")

@app.route("/", methods=["GET","POST"])
def index():
    if request.method == "POST":

        for f in request.files.getlist("files"):
            if f.filename:
                f.save(os.path.join(DATA_DIR, f.filename))

        tpl = request.files.get("template_file")
        if tpl and tpl.filename:
            tpl.save(TEMPLATE)

        csvf = request.files.get("rate_file")
        if csvf and csvf.filename:
            csvf.save(RATE_FILE)

        global RATES, CSV_IDENTITIES
        RATES = load_rates()
        CSV_IDENTITIES.clear()
        for key, rate in RATES.items():
            last, first = key.split(",", 1)
            CSV_IDENTITIES.append((normalize_for_id(f"{first} {last}"), rate, last, first))

        process_all()

    return render_template("index.html",
        logs="\n".join(LIVE_LOGS),
        template_path=TEMPLATE,
        rate_path=RATE_FILE
    )

@app.route("/logs")
def get_logs():
    return "\n".join(LIVE_LOGS)

@app.route("/download_all")
def download_all():
    zip_path = os.path.join(tempfile.gettempdir(), "SeaPay_Output.zip")

    if os.path.exists(zip_path):
        os.remove(zip_path)

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for f in os.listdir(OUTPUT_DIR):
            full = os.path.join(OUTPUT_DIR, f)
            if os.path.isfile(full):
                z.write(full, arcname=f)

    return send_from_directory(os.path.dirname(zip_path),
        os.path.basename(zip_path),
        as_attachment=True,
        download_name="SeaPay_Output.zip")

@app.route("/generate_text_report")
def download_text_report():
    path, err = generate_text_report()
    if err:
        return err, 400
    return send_from_directory(
        OUTPUT_DIR,
        "SEA_PAY_TEXT_REPORT.txt",
        as_attachment=True
    )

@app.route("/reset", methods=["POST"])
def reset():
    total = cleanup_all_folders()
    return jsonify({"status":"success","files_deleted":total})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
