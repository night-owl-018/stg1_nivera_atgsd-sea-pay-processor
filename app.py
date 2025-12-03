import os
import re
import io
import csv
import zipfile
import tempfile
import shutil
from collections import deque
from datetime import datetime, timedelta
from difflib import get_close_matches, SequenceMatcher

from flask import Flask, render_template, request, send_from_directory, jsonify
from PyPDF2 import PdfReader, PdfWriter, PdfMerger
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.lib.colors import black

import pytesseract
from pdf2image import convert_from_path
from pytesseract import Output

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
    print("Logs cleared", flush=True)

# ------------------------------------------------
# CLEANUP FUNCTIONS
# ------------------------------------------------

def cleanup_folder(folder_path, folder_name):
    try:
        files_deleted = 0
        for filename in os.listdir(folder_path):
            file_path = os.path.join(folder_path, filename)
            if os.path.isfile(file_path):
                os.remove(file_path)
                files_deleted += 1

        if files_deleted > 0:
            log(f"🗑 CLEANED {folder_name}: {files_deleted} files deleted")
        return files_deleted
    except Exception as e:
        log(f"❌ CLEANUP ERROR in {folder_name}: {e}")
        return 0

def cleanup_all_folders():
    log("=== STARTING RESET/CLEANUP ===")
    total = 0
    total += cleanup_folder(DATA_DIR, "INPUT/DATA")
    total += cleanup_folder(OUTPUT_DIR, "OUTPUT")
    log(f"✅ RESET COMPLETE: {total} total files deleted")
    log("🗑 CLEARING ALL LOGS...")
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
    out = ""
    for img in images:
        out += pytesseract.image_to_string(img)
    return out.upper()

# ------------------------------------------------
# NAME EXTRACTION
# ------------------------------------------------

def extract_member_name(text):
    m = re.search(r"NAME:\s*([A-Z\s]+?)\s+SSN", text)
    if not m:
        raise RuntimeError("NAME NOT FOUND")
    return " ".join(m.group(1).split())

# ------------------------------------------------
# SHIP MATCHING
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
# DATE HANDLING
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

        cleaned_raw = raw.strip()
        upper_raw = cleaned_raw.upper()

        if "SBTT" in upper_raw:
            skipped_unknown.append({"date": date, "raw": "SBTT"})
            log(f"⚠️ SBTT EVENT, SKIPPING → {date}")
            continue

        ship = match_ship(raw)

        if not ship:
            skipped_unknown.append({"date": date, "raw": cleaned_raw})
            log(f"⚠️ UNKNOWN SHIP/EVENT, SKIPPING → {date} [{cleaned_raw}]")
            continue

        if date in seen_dates:
            skipped_duplicates.append({"date": date, "ship": ship})
            log(f"⚠️ DUPLICATE DATE FOUND, DISCARDING → {date} ({ship})")
            continue

        rows.append({"date": date, "ship": ship})
        seen_dates.add(date)

    return rows, skipped_duplicates, skipped_unknown

# ------------------------------------------------
# GROUPING BY SHIP
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
# CSV MATCHING / IDENTITY
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

def resolve_identity(name):
    csv_id = lookup_csv_identity(name)
    if csv_id:
        rate, last, first = csv_id
    else:
        parts = name.split()
        last = parts[-1]
        first = " ".join(parts[:-1])
        rate = get_rate(name)
    return rate, last, first

# ------------------------------------------------
# FLATTEN PDF
# ------------------------------------------------

def flatten_pdf(path):
    try:
        reader = PdfReader(path)
        writer = PdfWriter()

        for page in reader.pages:
            if "/Annots" in page:
                del page["/Annots"]

            contents = page.get("/Contents")
            if isinstance(contents, list):
                merged = b""
                for obj in contents:
                    merged += obj.get_data()
                page["/Contents"] = writer._add_object(merged)

            if "/Rotate" in page:
                del page["/Rotate"]

            writer.add_page(page)

        if "/AcroForm" in writer._root_object:
            del writer._root_object["/AcroForm"]

        tmp = path + ".flat"
        with open(tmp, "wb") as f:
            writer.write(f)

        os.replace(tmp, path)
        log(f"FLATTENED → {os.path.basename(path)}")

    except Exception as e:
        log(f"⚠️ FLATTEN FAILED → {e}")

# ------------------------------------------------
# CREATE 1070/613 PDF
# ------------------------------------------------

def make_pdf(group, name):
    rate, last, first = resolve_identity(name)

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

    flatten_pdf(outpath)
    log(f"CREATED → {filename}")

# ------------------------------------------------
# MERGE ALL PDFs
# ------------------------------------------------

def merge_all_pdfs():
    pdf_files = sorted([
        f for f in os.listdir(OUTPUT_DIR)
        if f.lower().endswith(".pdf")
        and not f.startswith("MERGED_SeaPay_Forms_")
        and not f.startswith("MARKED_")
    ])

    if not pdf_files:
        log("NO PDFs TO MERGE")
        return None

    log(f"MERGING {len(pdf_files)} PDFs...")
    merger = PdfMerger()

    for pdf_file in pdf_files:
        pdf_path = os.path.join(OUTPUT_DIR, pdf_file)
        bookmark = os.path.splitext(pdf_file)[0]
        merger.append(pdf_path, outline_item=bookmark)
        log(f"ADDED BOOKMARK → {bookmark}")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    merged_filename = f"MERGED_SeaPay_Forms_{ts}.pdf"
    merged_path = os.path.join(OUTPUT_DIR, merged_filename)

    try:
        merger.write(merged_path)
        merger.close()
        flatten_pdf(merged_path)
        log(f"MERGED PDF CREATED → {merged_filename}")
        return merged_filename
    except Exception as e:
        log(f"❌ MERGE FAILED → {e}")
        return None

# ------------------------------------------------
# STRIKEOUT ENGINE HELPERS
# ------------------------------------------------

def _build_date_variants(date_str):
    variants = set()
    try:
        dt = datetime.strptime(date_str, "%m/%d/%Y")
    except Exception:
        return {date_str}
    variants.add(date_str)  # 08/06/2025
    variants.add(f"{dt.month}/{dt.day}/{dt.year}")                 # 8/6/2025
    variants.add(f"{dt.month}/{dt.day}/{dt.year % 100:02d}")       # 8/6/25
    variants.add(f"{dt.month:02d}/{dt.day:02d}/{dt.year % 100:02d}")  # 08/06/25
    return variants

def _extract_strong_tokens(raw):
    if not raw:
        return []
    tokens = []
    for tok in re.split(r"\s+", raw.upper()):
        tok = re.sub(r"[^A-Z0-9*/\-]", "", tok)
        if len(tok) >= 3:
            tokens.append(tok)
    return tokens

# ------------------------------------------------
# STRIKEOUT ENGINE (DATE + ANY STRONG TOKEN)
# ------------------------------------------------

def mark_sheet_with_strikeouts(original_pdf, skipped_duplicates, skipped_unknown, output_path):
    try:
        log(f"MARKING SHEET START → {os.path.basename(original_pdf)}")

        # Collect all dates that have any skipped event
        skip_dates = set()
        for d in skipped_duplicates:
            skip_dates.add(d["date"])
        for u in skipped_unknown:
            skip_dates.add(u["date"])

        if not skip_dates:
            shutil.copy2(original_pdf, output_path)
            log(f"NO STRIKEOUTS NEEDED, COPIED → {os.path.basename(output_path)}")
            return

        # Build date -> variants map
        date_variants = {d: _build_date_variants(d) for d in skip_dates}

        # Group duplicates by (date, ship)
        dup_groups = {}
        for d in skipped_duplicates:
            key = (d["date"], d["ship"].upper())
            dup_groups[key] = dup_groups.get(key, 0) + 1

        # Build list of rows from OCR (one per line)
        pages = convert_from_path(original_pdf)
        row_list = []  # each: {"page", "line", "y", "text"}

        for page_index, img in enumerate(pages):
            log(f"  BUILDING ROWS FROM PAGE {page_index+1}/{len(pages)}")
            data = pytesseract.image_to_data(img, output_type=Output.DICT)
            img_w, img_h = img.size
            scale_y = letter[1] / float(img_h)

            rows_by_line = {}
            n = len(data["text"])

            for i in range(n):
                txt = data["text"][i].strip()
                if not txt:
                    continue
                line_num = data["line_num"][i]
                top = data["top"][i]
                h = data["height"][i]

                center_y_img = top + h / 2.0
                center_from_bottom_px = img_h - center_y_img
                y = center_from_bottom_px * scale_y

                if line_num not in rows_by_line:
                    rows_by_line[line_num] = {
                        "y_sum": y,
                        "count": 1,
                        "tokens": [txt],
                    }
                else:
                    r = rows_by_line[line_num]
                    r["y_sum"] += y
                    r["count"] += 1
                    r["tokens"].append(txt)

            for line_num, r in rows_by_line.items():
                text = " ".join(r["tokens"]).upper()
                y_avg = r["y_sum"] / r["count"]
                row_list.append({
                    "page": page_index,
                    "line": line_num,
                    "y": y_avg,
                    "text": text,
                })

        rows_to_strike = set()

        # 1) Handle UNKNOWN / NON-SHIP / SBTT / MITE etc. using date + ANY strong token from raw
        for u in skipped_unknown:
            date = u["date"]
            raw = u.get("raw", "")
            variants = date_variants.get(date, {date})
            tokens = _extract_strong_tokens(raw)

            for idx, row in enumerate(row_list):
                if not any(v in row["text"] for v in variants):
                    continue
                if tokens and not any(tok in row["text"] for tok in tokens):
                    continue
                if idx not in rows_to_strike:
                    rows_to_strike.add(idx)
                    log(f"    STRIKEOUT UNKNOWN {date} MATCH '{raw}' ON PAGE {row['page']+1} Y={row['y']:.1f}")
                    break  # one row per unknown entry

        # 2) Handle DUPLICATES using date + ship, and only mark extra rows
        for (date, ship_upper), dup_count in dup_groups.items():
            variants = date_variants.get(date, {date})
            candidates = []

            for idx, row in enumerate(row_list):
                if not any(v in row["text"] for v in variants):
                    continue
                if ship_upper not in row["text"]:
                    continue
                candidates.append(idx)

            if not candidates or dup_count <= 0:
                continue

            # Sort candidates: by page, then by vertical (top first)
            candidates_sorted = sorted(
                candidates,
                key=lambda idx: (row_list[idx]["page"], -row_list[idx]["y"])
            )

            # Don't touch rows already struck by UNKNOWN logic
            markable = [idx for idx in candidates_sorted if idx not in rows_to_strike]
            if not markable:
                continue

            # Duplicates = last dup_count rows in reading order
            if dup_count < len(markable):
                to_mark = markable[-dup_count:]
            else:
                to_mark = markable  # mark all if somehow dup_count >= candidates

            for idx in to_mark:
                rows_to_strike.add(idx)
                row = row_list[idx]
                log(f"    STRIKEOUT DUP {date} {ship_upper} ON PAGE {row['page']+1} Y={row['y']:.1f}")

        if not rows_to_strike:
            shutil.copy2(original_pdf, output_path)
            log(f"NO STRIKEOUT POSITIONS FOUND, COPIED → {os.path.basename(output_path)}")
            return

        # Build strike targets per page from rows_to_strike
        strike_targets = {}
        for idx in rows_to_strike:
            row = row_list[idx]
            strike_targets.setdefault(row["page"], []).append(row["y"])

        # Build overlays and merge
        overlays = []
        for page_index in range(len(pages)):
            ys = strike_targets.get(page_index)
            if not ys:
                overlays.append(None)
                continue

            buf = io.BytesIO()
            c = canvas.Canvas(buf, pagesize=letter)
            c.setStrokeColor(black)
            c.setLineWidth(0.8)  # thinner line as requested

            for y in ys:
                c.line(40, y, 550, y)

            c.save()
            buf.seek(0)
            overlays.append(PdfReader(buf))

        reader = PdfReader(original_pdf)
        writer = PdfWriter()

        for i, page in enumerate(reader.pages):
            if i < len(overlays) and overlays[i] is not None:
                page.merge_page(overlays[i].pages[0])
            writer.add_page(page)

        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "wb") as f:
            writer.write(f)

        log(f"MARKED SHEET CREATED → {os.path.basename(output_path)}")

    except Exception as e:
        log(f"⚠️ MARKING FAILED → {e}")
        try:
            shutil.copy2(original_pdf, output_path)
            log(f"FALLBACK COPY CREATED → {os.path.basename(output_path)}")
        except Exception as e2:
            log(f"⚠️ FALLBACK COPY FAILED → {e2}")

# ------------------------------------------------
# PROCESS ALL + SUMMARY GENERATION
# ------------------------------------------------

def process_all():
    files = [f for f in os.listdir(DATA_DIR) if f.lower().endswith(".pdf")]

    if not files:
        log("NO INPUT FILES")
        return

    log("=== PROCESS STARTED ===")

    # Per-sailor summary accumulator
    summary_data = {}  # key -> {"rate","last","first","periods","skipped_unknown","skipped_dupe"}

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

        # Mark source sheet with strikeouts
        marked_dir = os.path.join(OUTPUT_DIR, "marked_sheets")
        os.makedirs(marked_dir, exist_ok=True)

        marked_path = os.path.join(
            marked_dir,
            f"MARKED_{os.path.splitext(file)[0]}.pdf"
        )

        mark_sheet_with_strikeouts(path, skipped_dupe, skipped_unknown, marked_path)

        # Group valid periods and create PDFs
        groups = group_by_ship(rows)

        for g in groups:
            make_pdf(g, name)

        # Accumulate summary per sailor
        rate, last, first = resolve_identity(name)
        key = f"{rate} {last},{first}" if rate else f"{last},{first}"

        if key not in summary_data:
            summary_data[key] = {
                "rate": rate,
                "last": last,
                "first": first,
                "periods": [],
                "skipped_unknown": [],
                "skipped_dupe": [],
            }

        sd = summary_data[key]

        for g in groups:
            days = (g["end"] - g["start"]).days + 1
            sd["periods"].append({
                "ship": g["ship"],
                "start": g["start"],
                "end": g["end"],
                "days": days,
            })

        sd["skipped_unknown"].extend(skipped_unknown)
        sd["skipped_dupe"].extend(skipped_dupe)

    merge_all_pdfs()

    # Build summary files in /output/summary
    summary_dir = os.path.join(OUTPUT_DIR, "summary")
    os.makedirs(summary_dir, exist_ok=True)

    compiled_lines = []

    for key in sorted(summary_data.keys()):
        sd = summary_data[key]
        rate = sd["rate"]
        last = sd["last"]
        first = sd["first"]
        periods = sd["periods"]
        skipped_unknown = sd["skipped_unknown"]
        skipped_dupe = sd["skipped_dupe"]

        # Sort periods by ship then start date
        periods_sorted = sorted(periods, key=lambda p: (p["ship"], p["start"]))

        total_days = sum(p["days"] for p in periods_sorted)

        # Build block lines in requested format
        lines = []
        lines.append("=====================================================================")
        title = f"{rate} {last}, {first}".strip()
        lines.append(title)
        lines.append("=====================================================================")
        lines.append("")
        lines.append("VALID SEA PAY PERIODS")
        lines.append("---------------------------------------------------------------------")

        if periods_sorted:
            for p in periods_sorted:
                s = p["start"].strftime("%m/%d/%Y")
                e = p["end"].strftime("%m/%d/%Y")
                lines.append(f"{p['ship']} : FROM {s} TO {e} ({p['days']} DAYS)")
            lines.append(f"TOTAL VALID DAYS: {total_days}")
        else:
            lines.append("  NONE")
            lines.append("TOTAL VALID DAYS: 0")

        lines.append("")
        lines.append("---------------------------------------------------------------------")
        lines.append("INVALID / EXCLUDED EVENTS / UNRECOGNIZED / NON-SHIP ENTRIES")

        if skipped_unknown:
            for u in skipped_unknown:
                raw = u.get("raw", "")
                lines.append(f"  {u['date']} : {raw}")
        else:
            lines.append("  NONE")

        lines.append("")
        lines.append("---------------------------------------------------------------------")
        lines.append("DUPLICATE DATE CONFLICTS")

        if skipped_dupe:
            for d in skipped_dupe:
                lines.append(f"  {d['date']} : {d['ship']}")
        else:
            lines.append("  NONE")

        lines.append("")

        # Write individual summary file
        safe_rate = rate.replace(" ", "") if rate else ""
        base_name = f"{safe_rate}_{last}_{first}_summary".strip("_").replace(" ", "_")
        summary_path = os.path.join(summary_dir, f"{base_name}.txt")
        with open(summary_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

        # Append to compiled
        compiled_lines.extend(lines)
        compiled_lines.append("")

    # Write compiled summary file
    compiled_path = os.path.join(summary_dir, "ALL_SUMMARIES_COMPILED.txt")
    if compiled_lines:
        with open(compiled_path, "w", encoding="utf-8") as f:
            f.write("\n".join(compiled_lines))
        log("SUMMARY FILES UPDATED")
    else:
        with open(compiled_path, "w", encoding="utf-8") as f:
            f.write("NO DATA\n")
        log("SUMMARY FILES CREATED BUT EMPTY")

    log("✅ ALL OPERATIONS COMPLETE")

# ------------------------------------------------
# FLASK APP / ROUTES
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
            CSV_IDENTITIES.append((normalize_for_id(f"{first} {last}"), rate, last, first))

        process_all()

    return render_template(
        "index.html",
        logs="\n".join(LIVE_LOGS),
        template_path=TEMPLATE,
        rate_path=RATE_FILE,
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

    return send_from_directory(
        os.path.dirname(zip_path),
        os.path.basename(zip_path),
        as_attachment=True,
        download_name="SeaPay_Output.zip",
    )

@app.route("/download_merged")
def download_merged():
    merged_files = sorted([f for f in os.listdir(OUTPUT_DIR) if f.startswith("MERGED_SeaPay_Forms_")])
    latest = merged_files[-1]
    return send_from_directory(OUTPUT_DIR, latest, as_attachment=True)

@app.route("/download_summary")
def download_summary():
    summary_dir = os.path.join(OUTPUT_DIR, "summary")
    zip_path = os.path.join(tempfile.gettempdir(), "SeaPay_Summaries.zip")

    if os.path.exists(zip_path):
        os.remove(zip_path)

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        if os.path.exists(summary_dir):
            for f in os.listdir(summary_dir):
                full = os.path.join(summary_dir, f)
                if os.path.isfile(full):
                    z.write(full, arcname=f)

    return send_from_directory(
        os.path.dirname(zip_path),
        os.path.basename(zip_path),
        as_attachment=True,
        download_name="SeaPay_Summaries.zip",
    )

@app.route("/download_marked_sheets")
def download_marked_sheets():
    marked_dir = os.path.join(OUTPUT_DIR, "marked_sheets")
    zip_path = os.path.join(tempfile.gettempdir(), "Marked_Sheets.zip")

    if os.path.exists(zip_path):
        os.remove(zip_path)

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        if os.path.exists(marked_dir):
            for f in os.listdir(marked_dir):
                full = os.path.join(marked_dir, f)
                if os.path.isfile(full):
                    z.write(full, arcname=f)

    return send_from_directory(
        os.path.dirname(zip_path),
        os.path.basename(zip_path),
        as_attachment=True,
        download_name="Marked_Sheets.zip",
    )

@app.route("/reset", methods=["POST"])
def reset():
    deleted = cleanup_all_folders()
    clear_logs()
    return jsonify({"status": "success", "message": f"Reset complete! {deleted} files deleted."})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
