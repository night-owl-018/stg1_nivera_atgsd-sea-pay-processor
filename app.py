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
    """Clear all logs from the buffer"""
    LIVE_LOGS.clear()
    print("Logs cleared", flush=True)

# ------------------------------------------------
# CLEANUP FUNCTIONS
# ------------------------------------------------

def cleanup_folder(folder_path, folder_name):
    """Delete all files in a folder"""
    try:
        files_deleted = 0
        for filename in os.listdir(folder_path):
            file_path = os.path.join(folder_path, filename)
            if os.path.isfile(file_path):
                os.remove(file_path)
                files_deleted += 1
        
        if files_deleted > 0:
            log(f"🗑️ CLEANED {folder_name}: {files_deleted} files deleted")
        return files_deleted
    except Exception as e:
        log(f"❌ CLEANUP ERROR in {folder_name}: {e}")
        return 0

def cleanup_all_folders():
    """Clean up all working folders and clear logs"""
    log("=== STARTING RESET/CLEANUP ===")
    
    total = 0
    total += cleanup_folder(DATA_DIR, "INPUT/DATA")
    total += cleanup_folder(OUTPUT_DIR, "OUTPUT")
    
    log(f"✅ RESET COMPLETE: {total} total files deleted")
    log(f"🗑️ CLEARING ALL LOGS...")
    log("=" * 50)
    
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
# OCR FUNCTIONS
# ------------------------------------------------

def strip_times(text):
    return re.sub(r"\b[0-2]?\d[0-5]\d\b", "", text)

def ocr_pdf(path):
    images = convert_from_path(path)
    output = ""
    for img in images:
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
    """
    Parse OCR text into unique (date, ship) rows.

    NEW BEHAVIOR:
    - Only one ship is allowed per date (first one wins).
    - Duplicate dates for later ships are skipped and recorded.
    - Rows with no recognizable ship (MITE, SBTT, etc.) are skipped and recorded.

    Returns:
        rows: list[{"date": "MM/DD/YYYY", "ship": "CHAFEE"}]
        skipped_duplicates: list[{"date": "MM/DD/YYYY", "ship": "PAUL HAMILTON"}]
        skipped_unknown: list[{"date": "MM/DD/YYYY", "raw": "<full event text>"}]
    """
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

        if not ship:
            # No recognizable ship for this row (e.g., MITE, SBTT, admin events)
            skipped_unknown.append({"date": date, "raw": cleaned_raw})
            log(f"⚠️ UNKNOWN SHIP/EVENT, SKIPPING → {date} [{cleaned_raw}]")
            continue

        if date in seen_dates:
            # Another ship already claimed this date; this one is discarded
            skipped_duplicates.append({"date": date, "ship": ship})
            log(f"⚠️ DUPLICATE DATE FOUND, DISCARDING → {date} ({ship})")
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
        dt = datetime.strptime(r["date"], "%m/%d/%Y")
        grouped.setdefault(r["ship"], []).append(dt)

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
# CSV AUTHORITY RESOLUTION
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
        rate, last, first = best
        log(f"CSV MATCH ({best_score:.2f}) → {rate} {last},{first}")
        return best

    log(f"CSV NO GOOD MATCH (best={best_score:.2f}) for [{name}]")
    return None

def get_rate(name):
    parts = normalize(name).split()
    if len(parts) < 2:
        return ""
    key = f"{parts[-1]},{parts[0]}"
    return RATES.get(key, "")

# ------------------------------------------------
# PDF CREATION
# ------------------------------------------------

def make_pdf(group, name):

    csv_id = lookup_csv_identity(name)
    if csv_id:
        rate, last, first = csv_id
    else:
        parts = name.split()
        last = parts[-1]
        first = " ".join(parts[:-1])
        rate = get_rate(name)

    start = group["start"].strftime("%m/%d/%Y")
    end = group["end"].strftime("%m/%d/%Y")
    ship = group["ship"]

    prefix = f"{rate}_" if rate else ""
    filename = f"{prefix}{last}_{first}_{ship}_{start.replace('/','-')}_TO_{end.replace('/','-')}.pdf"
    filename = filename.replace(" ", "_")

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
# MERGE ALL PDFs WITH BOOKMARKS
# ------------------------------------------------

def merge_all_pdfs():
    pdf_files = sorted([
        f for f in os.listdir(OUTPUT_DIR) 
        if f.lower().endswith(".pdf") and not f.startswith("MERGED_SeaPay_Forms_")
    ])
    
    if not pdf_files:
        log("NO PDFs TO MERGE")
        return None
    
    log(f"MERGING {len(pdf_files)} PDFs...")
    
    merger = PdfMerger()
    
    for pdf_file in pdf_files:
        pdf_path = os.path.join(OUTPUT_DIR, pdf_file)
        try:
            bookmark_name = os.path.splitext(pdf_file)[0]
            merger.append(pdf_path, outline_item=bookmark_name)
            log(f"ADDED WITH BOOKMARK → {bookmark_name}")
        except Exception as e:
            log(f"ERROR ADDING {pdf_file}: {e}")
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    merged_filename = f"MERGED_SeaPay_Forms_{timestamp}.pdf"
    merged_path = os.path.join(OUTPUT_DIR, merged_filename)
    
    try:
        merger.write(merged_path)
        merger.close()
        log(f"✅ MERGED PDF CREATED → {merged_filename}")
        log(f"📑 BOOKMARKS ADDED: {len(pdf_files)}")
        return merged_filename
    except Exception as e:
        log(f"❌ MERGE FAILED: {e}")
        return None

# ------------------------------------------------
# PROCESS
# ------------------------------------------------

def process_all():
    files = [f for f in os.listdir(DATA_DIR) if f.lower().endswith(".pdf")]

    if not files:
        log("NO INPUT FILES")
        return

    log("=== PROCESS STARTED ===")

    # This will hold a human-readable summary for all sailors / ships / dates
    summary_lines = []

    for file in files:
        log(f"OCR → {file}")
        path = os.path.join(DATA_DIR, file)

        raw = strip_times(ocr_pdf(path))

        try:
            name = extract_member_name(raw)
            log(f"NAME → {name}")
        except Exception as e:
            log(f"NAME ERROR → {e}")
            continue

        year = extract_year_from_filename(file)
        rows, skipped_dupe, skipped_unknown = parse_rows(raw, year)

        used_fallback = False

        if not rows:
            ship = match_ship(raw)
            if ship:
                log(f"FALLBACK SHIP → {ship}")
                rows = [{"date": datetime.today().strftime("%m/%d/%Y"), "ship": ship}]
                used_fallback = True
            else:
                log("NO SHIP MATCH")
                continue

        groups = group_by_ship(rows)

        # Generate PDFs for each group
        for g in groups:
            make_pdf(g, name)

        # ===============================
        # FORMATTED SUMMARY OUTPUT 
        # ===============================
        csv_id = lookup_csv_identity(name)
        if csv_id:
            rate, last, first = csv_id
            display_name = f"{rate} {first} {last}"
        else:
            display_name = name

        width = 69
        summary_lines.append("=" * width)
        summary_lines.append(display_name.upper())
        summary_lines.append("=" * width)
        summary_lines.append("")

        # VALID PERIODS
        summary_lines.append("VALID SEA PAY PERIODS")
        summary_lines.append("-" * width)

        if groups:
            for g in groups:
                start_str = g["start"].strftime("%m/%d/%Y")
                end_str = g["end"].strftime("%m/%d/%Y")
                ship = g["ship"]
                summary_lines.append(f"{ship} : FROM {start_str} TO {end_str}")
        else:
            summary_lines.append("  NONE")

        summary_lines.append("")
        summary_lines.append("-" * width)
        summary_lines.append("INVALID / EXCLUDED EVENTS / UNRECOGNIZED / NON-SHIP ENTRIES")

        if skipped_unknown:
            for s in skipped_unknown:
                summary_lines.append(f"  {s['date']}  {s['raw']}")
        else:
            summary_lines.append("  NONE")

        summary_lines.append("")
        summary_lines.append("-" * width)
        summary_lines.append("DUPLICATE DATE CONFLICTS")

        if skipped_dupe:
            for s in skipped_dupe:
                summary_lines.append(f"  {s['date']}  {s['ship']}")
        else:
            summary_lines.append("  NONE")

        summary_lines.append("")
        summary_lines.append("")


    log("======================================")
    log("✅ GENERATION COMPLETE")
    log("======================================")
    
    log("=== STARTING AUTO-MERGE ===")
    merge_all_pdfs()
    log("======================================")
    log("✅ ALL OPERATIONS COMPLETE — READY TO DOWNLOAD")
    log("======================================")

    # ---------------------------------
    # WRITE SUMMARY TEXT FILE TO OUTPUT
    # ---------------------------------
    if summary_lines:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        summary_filename = f"SeaPay_Summary_{timestamp}.txt"
        summary_path = os.path.join(OUTPUT_DIR, summary_filename)
        try:
            with open(summary_path, "w", encoding="utf-8") as f:
                f.write("\n".join(summary_lines))
            log(f"📝 SUMMARY FILE CREATED → {summary_filename}")
        except Exception as e:
            log(f"❌ SUMMARY FILE ERROR: {e}")

# ------------------------------------------------
# FLASK APP
# ------------------------------------------------

app = Flask(__name__, template_folder="web/frontend")

@app.route("/", methods=["GET", "POST"])
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
            def normalize_for_id(text):
                t = re.sub(r"\(.*?\)", "", text.upper())
                t = re.sub(r"[^A-Z ]", "", t)
                return " ".join(t.split())
            full_norm = normalize_for_id(f"{first} {last}")
            CSV_IDENTITIES.append((full_norm, rate, last, first))

        process_all()

    return render_template(
        "index.html",
        logs="\n".join(LIVE_LOGS),
        template_path=TEMPLATE,
        rate_path=RATE_FILE
    )

@app.route("/logs")
def get_logs():
    return "\n".join(LIVE_LOGS)

@app.route("/download_all")
def download_all():
    try:
        zip_path = os.path.join(tempfile.gettempdir(), "SeaPay_Output.zip")
        
        if os.path.exists(zip_path):
            os.remove(zip_path)
        
        log("=== CREATING ZIP FILE ===")
        
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
            files_added = 0
            for f in os.listdir(OUTPUT_DIR):
                full = os.path.join(OUTPUT_DIR, f)
                if os.path.isfile(full):
                    z.write(full, arcname=f)
                    files_added += 1
                    log(f"ZIPPED → {f}")
        
        if files_added == 0:
            log("❌ NO FILES TO ZIP")
            return "No files found in output directory", 404
        
        log(f"✅ ZIP CREATED: {files_added} files")
        
        return send_from_directory(os.path.dirname(zip_path), os.path.basename(zip_path), as_attachment=True, download_name="SeaPay_Output.zip")
    except Exception as e:
        log(f"❌ ZIP ERROR: {e}")
        return f"Error creating zip: {str(e)}", 500

@app.route("/download_merged")
def download_merged():
    try:
        merged_files = sorted([f for f in os.listdir(OUTPUT_DIR) 
                              if f.startswith("MERGED_SeaPay_Forms_") and f.endswith(".pdf")])
        
        if not merged_files:
            log("❌ NO MERGED PDF FOUND")
            return "No merged PDF found", 404
        
        latest_merged = merged_files[-1]
        log(f"📄 DOWNLOADING MERGED PDF → {latest_merged}")
        
        return send_from_directory(OUTPUT_DIR, latest_merged, as_attachment=True)
    except Exception as e:
        log(f"❌ MERGED PDF DOWNLOAD ERROR: {e}")
        return f"Error downloading merged PDF: {str(e)}", 500

@app.route("/download_summary")
def download_summary():
    try:
        summary_files = sorted([
            f for f in os.listdir(OUTPUT_DIR)
            if f.startswith("SeaPay_Summary_") and f.endswith(".txt")
        ])

        if not summary_files:
            log("❌ NO SUMMARY FILE FOUND")
            return "No summary file found", 404

        latest_summary = summary_files[-1]
        log(f"📄 DOWNLOADING SUMMARY FILE → {latest_summary}")

        return send_from_directory(OUTPUT_DIR, latest_summary, as_attachment=True)
    except Exception as e:
        log(f"❌ SUMMARY DOWNLOAD ERROR: {e}")
        return f"Error downloading summary file: {str(e)}", 500

@app.route("/reset", methods=["POST"])
def reset():
    total_deleted = cleanup_all_folders()
    clear_logs()
    
    return jsonify({
        "status": "success", 
        "message": f"Reset complete! {total_deleted} files deleted. Logs cleared.",
        "files_deleted": total_deleted
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
