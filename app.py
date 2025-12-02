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
# PATH CONFIG
# ------------------------------------------------

DATA_DIR = "/data"
OUTPUT_DIR = "/output"
TEMPLATE_DIR = "/templates"
CONFIG_DIR = "/config"

TEMPLATE = os.path.join(TEMPLATE_DIR, "NAVPERS_1070_613_TEMPLATE.pdf")
RATE_FILE = os.path.join(CONFIG_DIR, "atgsd_n811.csv")
SHIP_FILE = "/app/ships.txt"

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(TEMPLATE_DIR, exist_ok=True)
os.makedirs(CONFIG_DIR, exist_ok=True)

pytesseract.pytesseract.tesseract_cmd = "tesseract"
FONT_NAME = "Times-Roman"
FONT_SIZE = 10

# ------------------------------------------------
# LIVE LOG BUFFER
# ------------------------------------------------

LIVE_LOGS = deque(maxlen=500)

def log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    LIVE_LOGS.append(line)

def clear_logs():
    LIVE_LOGS.clear()

# ------------------------------------------------
# CLEANUP FUNCTIONS
# ------------------------------------------------

def cleanup_folder(folder_path, folder_name):
    count = 0
    for filename in os.listdir(folder_path):
        file_path = os.path.join(folder_path, filename)
        if os.path.isfile(file_path):
            os.remove(file_path)
            count += 1
    log(f"🗑️ CLEANED {folder_name}: {count} files deleted")
    return count

def cleanup_all_folders():
    total = cleanup_folder(DATA_DIR, "INPUT") + cleanup_folder(OUTPUT_DIR, "OUTPUT")
    log(f"✅ RESET COMPLETE: {total} files deleted")
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

    with open(RATE_FILE, "r", encoding="utf-8-sig", newline="") as f:
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
for key, rate in RATES.items():
    last, first = key.split(",", 1)
    def normalize_for_id(text):
        t = re.sub(r"\(.*?\)", "", text.upper())
        t = re.sub(r"[^A-Z ]", "", t)
        return " ".join(t.split())
    full_norm = normalize_for_id(f"{first} {last}")
    CSV_IDENTITIES.append((full_norm, rate, last, first))

# ------------------------------------------------
# LOAD SHIP LIST
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
# OCR
# ------------------------------------------------

def strip_times(text):
    return re.sub(r"\b[0-2]?\d[0-5]\d\b", "", text)

def ocr_pdf(path):
    output = ""
    for img in convert_from_path(path):
        output += pytesseract.image_to_string(img)
    return output.upper()

# ------------------------------------------------
# NAME EXTRACTION
# ------------------------------------------------

def extract_member_name(text):
    m = re.search(r"NAME:\s*([A-Z\s]+?)\s+SSN", text)
    if not m:
        raise RuntimeError("NAME NOT FOUND")
    return " ".join(m.group(1).split())

# ------------------------------------------------
# SHIP MATCH
# ------------------------------------------------

def match_ship(raw_text):
    candidate = normalize(raw_text)
    words = candidate.split()
    for size in range(len(words), 0, -1):
        for i in range(len(words) - size + 1):
            chunk = " ".join(words[i:i+size])
            match = get_close_matches(chunk, NORMAL_KEYS, n=1, cutoff=0.75)
            if match:
                return NORMALIZED_SHIPS[match[0]]
    return None

# ------------------------------------------------
# DATES
# ------------------------------------------------

def extract_year_from_filename(fn):
    m = re.search(r"(20\d{2})", fn)
    return m.group(1) if m else str(datetime.now().year)

def parse_rows(text, year):
    rows = []
    seen_dates = set()
    skipped_duplicates = []
    skipped_unknown = []

    lines = text.splitlines()

    for i, line in enumerate(lines):
        m = re.match(r"\s*(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?", line)
        if not m:
            continue

        mm, dd, yy = m.groups()
        y = ("20" + yy) if yy and len(yy) == 2 else yy or year
        date = f"{mm.zfill(2)}/{dd.zfill(2)}/{y}"

        raw = line[m.end():]
        if i + 1 < len(lines):
            raw += " " + lines[i + 1]

        ship = match_ship(raw)
        cleaned_raw = raw.strip()

        # ✅ SBTT EXCLUSION (ADD ONLY)
        if "SBTT" in cleaned_raw.upper():
            ship_for_label = ship if ship else "UNKNOWN SHIP"
            skipped_unknown.append({"date": date, "raw": f"{ship_for_label} SBTT"})
            log(f"⚠️ SBTT EVENT SKIPPED → {date} [{ship_for_label} SBTT]")
            continue

        if not ship:
            skipped_unknown.append({"date": date, "raw": cleaned_raw})
            log(f"⚠️ UNKNOWN EVENT → {date} [{cleaned_raw}]")
            continue

        if date in seen_dates:
            skipped_duplicates.append({"date": date, "ship": ship})
            log(f"⚠️ DUPLICATE DATE → {date} ({ship})")
            continue

        rows.append({"date": date, "ship": ship})
        seen_dates.add(date)

    return rows, skipped_duplicates, skipped_unknown

# ------------------------------------------------
# GROUPING
# ------------------------------------------------

def group_by_ship(rows):
    grouped = {}
    for r in rows:
        grouped.setdefault(r["ship"], []).append(datetime.strptime(r["date"], "%m/%d/%Y"))

    output = []
    for ship, dates in grouped.items():
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

# ------------------------------------------------
# CSV MATCH
# ------------------------------------------------

def lookup_csv_identity(name):
    ocr_norm = normalize(name)
    best = None
    best_score = 0.0

    for csv_norm, rate, last, first in CSV_IDENTITIES:
        score = SequenceMatcher(None, ocr_norm, csv_norm).ratio()
        if score > best_score:
            best_score = score
            best = (rate, last, first)

    if best and best_score >= 0.60:
        log(f"CSV MATCH ({best_score:.2f}) → {best[0]} {best[1]},{best[2]}")
        return best

    return None

# ------------------------------------------------
# SUMMARY FORMATTER (TOTAL DAYS ADDED)
# ------------------------------------------------

def format_summary_block(name, groups, skipped_dupe, skipped_unknown, rate=""):

    width = 69
    header = f"{rate} {name}".strip()

    lines = []
    lines.append("=" * width)
    lines.append(header)
    lines.append("=" * width)
    lines.append("")

    lines.append("VALID SEA PAY PERIODS")
    lines.append("-" * width)

    ship_totals = {}

    if groups:
        for g in groups:
            ship = g["ship"]
            start = g["start"]
            end = g["end"]
            lines.append(f"{ship} : FROM {start.strftime('%m/%d/%Y')} TO {end.strftime('%m/%d/%Y')}")

            # total days per ship
            days = (end - start).days + 1
            ship_totals[ship] = ship_totals.get(ship, 0) + days
    else:
        lines.append("  NONE")

    # ✅ TOTAL DAYS SECTION
    lines.append("")
    lines.append("-" * width)
    lines.append("TOTAL SEA PAY DAYS BY SHIP")

    if ship_totals:
        total_all = 0
        for ship, days in sorted(ship_totals.items()):
            lines.append(f"{ship} : {days} DAY" + ("S" if days != 1 else ""))
            total_all += days
        lines.append("")
        lines.append(f"TOTAL SEA PAY DAYS (ALL SHIPS): {total_all} DAYS")
    else:
        lines.append("  NONE")

    lines.append("")
    lines.append("-" * width)
    lines.append("INVALID / EXCLUDED EVENTS / UNRECOGNIZED / NON-SHIP ENTRIES")

    if skipped_unknown:
        for s in skipped_unknown:
            lines.append(f"  {s['raw']} : {s['date']}")
    else:
        lines.append("  NONE")

    lines.append("")
    lines.append("-" * width)
    lines.append("DUPLICATE DATE CONFLICTS")

    if skipped_dupe:
        for s in skipped_dupe:
            lines.append(f"  {s['date']}  {s['ship']}")
    else:
        lines.append("  NONE")

    lines.append("\n")
    return lines

# ------------------------------------------------
# PROCESS
# ------------------------------------------------

def process_all():
    files = [f for f in os.listdir(DATA_DIR) if f.lower().endswith(".pdf")]
    if not files:
        log("NO INPUT FILES")
        return

    summary_lines = []

    for file in files:
        raw = strip_times(ocr_pdf(os.path.join(DATA_DIR, file)))
        name = extract_member_name(raw)

        rows, skipped_dupe, skipped_unknown = parse_rows(raw, extract_year_from_filename(file))
        groups = group_by_ship(rows)

        for g in groups:
            make_pdf(g, name)

        csv_id = lookup_csv_identity(name)
        rate = csv_id[0] if csv_id else ""

        summary_lines.extend(format_summary_block(name, groups, skipped_dupe, skipped_unknown, rate))

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(OUTPUT_DIR, f"SeaPay_Summary_{ts}.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(summary_lines))

    log(f"📝 SUMMARY FILE CREATED → {os.path.basename(path)}")

# ------------------------------------------------
# PDF (UNCHANGED)
# ------------------------------------------------

def make_pdf(group, name):
    csv_id = lookup_csv_identity(name)
    rate = csv_id[0] if csv_id else ""
    parts = name.split()
    last = parts[-1]
    first = " ".join(parts[:-1])

    start = group["start"].strftime("%m/%d/%Y")
    end   = group["end"].strftime("%m/%d/%Y")
    ship  = group["ship"]

    prefix = f"{rate}_" if rate else ""
    filename = f"{prefix}{last}_{first}_{ship}_{start.replace('/','-')}_TO_{end.replace('/','-')}.pdf"
    filename = filename.replace(" ", "_")

    outpath = os.path.join(OUTPUT_DIR, filename)

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.setFont(FONT_NAME, FONT_SIZE)

    c.drawString(39, 595, f"REPORT CAREER SEA PAY FROM {start} TO {end}.")
    c.drawString(39, 570, f"Member performed eight continuous hours per day on-board: {ship}")
    c.drawString(39, 45, f"{rate} {last}, {first}".strip())

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
# FLASK
# ------------------------------------------------

app = Flask(__name__, template_folder="web/frontend")

@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        for f in request.files.getlist("files"):
            f.save(os.path.join(DATA_DIR, f.filename))

        if request.files.get("template_file"):
            request.files["template_file"].save(TEMPLATE)

        if request.files.get("rate_file"):
            request.files["rate_file"].save(RATE_FILE)

        process_all()

    return render_template("index.html", logs="\n".join(LIVE_LOGS),
                           template_path=TEMPLATE, rate_path=RATE_FILE)

@app.route("/logs")
def get_logs():
    return "\n".join(LIVE_LOGS)

@app.route("/download_all")
def download_all():
    zip_path = os.path.join(tempfile.gettempdir(), "SeaPay_Output.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for f in os.listdir(OUTPUT_DIR):
            z.write(os.path.join(OUTPUT_DIR, f), f)
    return send_from_directory(os.path.dirname(zip_path), os.path.basename(zip_path), as_attachment=True)

@app.route("/download_merged")
def download_merged():
    return "MERGED disabled in this build", 404

@app.route("/download_summary")
def download_summary():
    files = sorted([f for f in os.listdir(OUTPUT_DIR)
                    if f.startswith("SeaPay_Summary_") and f.endswith(".txt")])
    if not files:
        return "No summary created yet", 404
    return send_from_directory(OUTPUT_DIR, files[-1], as_attachment=True)

@app.route("/reset", methods=["POST"])
def reset():
    deleted = cleanup_all_folders()
    clear_logs()
    return jsonify(message=f"Reset complete ({deleted} files)")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
