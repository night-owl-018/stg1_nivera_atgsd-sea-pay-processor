import os
import re
from datetime import datetime

from app.core.logger import log
from app.core.config import (
    DATA_DIR,
    SEA_PAY_PG13_FOLDER,
    TORIS_CERT_FOLDER,
)
from app.core.ocr import ocr_pdf, strip_times, extract_member_name
from app.core.parser import parse_rows, extract_year_from_filename, group_by_ship
from app.core.pdf_writer import make_pdf_for_ship
from app.core.strikeout import mark_sheet_with_strikeouts
from app.core.summary import write_summary_files
from app.core.merge import merge_all_pdfs
from app.core.rates import resolve_identity


# -------------------------------------------------------------------------
# Extract reporting period from TORIS header / filename
# -------------------------------------------------------------------------
def extract_reporting_period(text, filename=""):
    """
    Extracts the official sheet header date range:
    Example line in PDF:
        'From: 8/4/2025 To: 11/24/2025'

    Returns:
        (start_date, end_date, "8/4/2025 - 11/24/2025")
        or (None, None, "") on failure.
    """

    pattern = r"From:\s*([0-9]{1,2}/[0-9]{1,2}/[0-9]{4})\s*To:\s*([0-9]{1,2}/[0-9]{1,2}/[0-9]{4})"
    match = re.search(pattern, text, re.IGNORECASE)

    if match:
        from_raw = match.group(1)
        to_raw = match.group(2)

        try:
            start = datetime.strptime(from_raw, "%m/%d/%Y")
            end = datetime.strptime(to_raw, "%m/%d/%Y")
        except Exception:
            return None, None, ""

        return start, end, f"{from_raw} - {to_raw}"

    # Fallback to filename like: "8_4_2025 - 11_24_2025"
    alt_pattern = r"(\d{1,2}_\d{1,2}_\d{4})\s*-\s*(\d{1,2}_\d{1,2}_\d{4})"
    m2 = re.search(alt_pattern, filename)

    if m2:
        try:
            s = datetime.strptime(m2.group(1).replace("_", "/"), "%m/%d/%Y")
            e = datetime.strptime(m2.group(2).replace("_", "/"), "%m/%d/%Y")
            return s, e, f"{m2.group(1)} - {m2.group(2)}"
        except Exception:
            return None, None, ""

    return None, None, ""


# -------------------------------------------------------------------------
# Clear PG13 output folder before each run
# -------------------------------------------------------------------------
def clear_pg13_folder():
    """Delete all existing PG13 PDFs before generating new ones."""
    try:
        if not os.path.isdir(SEA_PAY_PG13_FOLDER):
            os.makedirs(SEA_PAY_PG13_FOLDER, exist_ok=True)
        for f in os.listdir(SEA_PAY_PG13_FOLDER):
            fp = os.path.join(SEA_PAY_PG13_FOLDER, f)
            if os.path.isfile(fp):
                os.remove(fp)
    except Exception as e:
        log(f"PG13 CLEAR ERROR → {e}")


# -------------------------------------------------------------------------
# Main processing pipeline
# -------------------------------------------------------------------------
def process_all(strike_color="black"):
    """
    Main engine. Keeps your original logic:
        • OCR each TORIS sheet
        • Extract name / rate
        • Parse rows → valid, unknown, dupes
        • Generate TORIS strikeout PDF
        • Generate SEA PAY PG13 PDFs per ship-period
        • Build per-member summary payload
        • Write summary TXT/PDF files
        • Merge PG13, TORIS, and SUMMARY into PACKAGE

    Only behavior change from your last working state:
        → write_summary_files(summary_data) runs BEFORE merge_all_pdfs()
    """

    clear_pg13_folder()

    files = [f for f in os.listdir(DATA_DIR) if f.lower().endswith(".pdf")]
    if not files:
        log("NO INPUT FILES FOUND")
        return

    log("=== PROCESS STARTED ===")
    summary_data = {}

    for file in sorted(files):
        path = os.path.join(DATA_DIR, file)
        log(f"OCR → {file}")

        # 1. OCR + basic cleanup
        raw = strip_times(ocr_pdf(path))

        # 2. Reporting period (TORIS header)
        sheet_start, sheet_end, _ = extract_reporting_period(raw, file)

        # 3. Name extraction
        try:
            name = extract_member_name(raw)
            log(f"NAME → {name}")
        except Exception as e:
            log(f"NAME ERROR → {e}")
            continue

        year = extract_year_from_filename(file)

        # 4. Parse rows into valid / dupes / unknown
        rows, skipped_dupe, skipped_unknown = parse_rows(raw, year)

        # 5. Group valid periods by ship
        groups = group_by_ship(rows)
        total_days = sum((g["end"] - g["start"]).days + 1 for g in groups)

        # 6. Identity resolution from CSV
        rate, last, first = resolve_identity(name)
        key = f"{rate} {last},{first}"

        if key not in summary_data:
            summary_data[key] = {
                "rate": rate,
                "last": last,
                "first": first,
                "periods": [],
                "skipped_unknown": [],
                "skipped_dupe": [],
                "reporting_periods": [],
            }

        sd = summary_data[key]

        # 7. Store reporting range from TORIS header
        sd["reporting_periods"].append({
            "start": sheet_start,
            "end": sheet_end,
            "file": file,
        })

        # 8. Store valid sea-pay periods for summary
        for g in groups:
            sd["periods"].append({
                "ship": g["ship"],
                "start": g["start"],
                "end": g["end"],
                "days": (g["end"] - g["start"]).days + 1,
                "sheet_file": file,
            })

        # 9. Store skipped rows for summary (unknown + dupes)
        sd["skipped_unknown"].extend(skipped_unknown)
        sd["skipped_dupe"].extend(skipped_dupe)

        # 10. Build TORIS SEA PAY CERT SHEET filename with date range
        hf = sheet_start.strftime("%m-%d-%Y") if sheet_start else "UNKNOWN"
        ht = sheet_end.strftime("%m-%d-%Y") if sheet_end else "UNKNOWN"

        toris_filename = (
            f"{rate}_{last}_{first}"
            f"__TORIS_SEA_DUTY_CERT_SHEETS__{hf}_TO_{ht}.pdf"
        ).replace(" ", "_")

        toris_path = os.path.join(TORIS_CERT_FOLDER, toris_filename)

        # 11. Create strikeout TORIS sheet
        mark_sheet_with_strikeouts(
            path,
            skipped_dupe,
            skipped_unknown,
            toris_path,
            total_days,
            strike_color=strike_color,
        )

        # 12. Create NAVPERS 1070/613 PG13 PDFs (1 sheet per ship/period)
        ship_map = {}
        for g in groups:
            ship_map.setdefault(g["ship"], []).append(g)

        for ship, ship_periods in ship_map.items():
            make_pdf_for_ship(ship, ship_periods, name)

    # -------------------------------------------------
    # WRITE SUMMARY FILES FIRST (TXT + PDF)
    # -------------------------------------------------
    write_summary_files(summary_data)

    # -------------------------------------------------
    # THEN MERGE INTO PACKAGE (PG13 / TORIS / SUMMARY)
    # -------------------------------------------------
    merge_all_pdfs()

    log("PROCESS COMPLETE")
