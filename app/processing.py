import os
import re
from datetime import datetime

from app.core.logger import log, reset_progress, set_progress, add_progress_detail
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


def extract_reporting_period(text, filename=""):
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


def clear_pg13_folder():
    try:
        if not os.path.isdir(SEA_PAY_PG13_FOLDER):
            os.makedirs(SEA_PAY_PG13_FOLDER, exist_ok=True)
        for f in os.listdir(SEA_PAY_PG13_FOLDER):
            fp = os.path.join(SEA_PAY_PG13_FOLDER, f)
            if os.path.isfile(fp):
                os.remove(fp)
    except Exception as e:
        log(f"PG13 CLEAR ERROR → {e}")


def process_all(strike_color="black"):
    os.makedirs(SEA_PAY_PG13_FOLDER, exist_ok=True)
    os.makedirs(TORIS_CERT_FOLDER, exist_ok=True)

    clear_pg13_folder()
    reset_progress()

    files = [f for f in os.listdir(DATA_DIR) if f.lower().endswith(".pdf")]
    if not files:
        log("NO INPUT FILES FOUND")
        set_progress(
            status="complete",
            total_files=0,
            current_file=0,
            current_step="No input files",
            percentage=100,
        )
        return

    total_files = len(files)
    set_progress(
        status="processing",
        total_files=total_files,
        current_file=0,
        current_step="Initializing",
        percentage=0,
        details={
            "files_processed": 0,
            "valid_days": 0,
            "invalid_events": 0,
            "pg13_created": 0,
            "toris_marked": 0,
        },
    )

    log("=== PROCESS STARTED ===")
    summary_data = {}

    # --------------------------
    # NEW TOTAL COUNTERS
    # --------------------------
    files_processed_total = 0
    valid_days_total = 0
    invalid_events_total = 0
    pg13_total = 0
    toris_total = 0
    # --------------------------

    for idx, file in enumerate(sorted(files), start=1):
        path = os.path.join(DATA_DIR, file)
        set_progress(
            current_file=idx,
            current_step=f"OCR and parse: {file}",
            percentage=int(((idx - 1) / max(total_files, 1)) * 100),
        )
        log(f"OCR → {file}")

        raw = strip_times(ocr_pdf(path))

        sheet_start, sheet_end, _ = extract_reporting_period(raw, file)

        try:
            name = extract_member_name(raw)
            log(f"NAME → {name}")
        except Exception as e:
            log(f"NAME ERROR → {e}")
            continue

        year = extract_year_from_filename(file)

        rows, skipped_dupe, skipped_unknown = parse_rows(raw, year)

        groups = group_by_ship(rows)
        total_days = sum((g["end"] - g["start"]).days + 1 for g in groups)

        # --------------------------
        # UPDATE TOTALS
        # --------------------------
        valid_days_total += total_days
        invalid_events_total += len(skipped_dupe) + len(skipped_unknown)
        # --------------------------

        add_progress_detail("valid_days", total_days)
        add_progress_detail("invalid_events", len(skipped_dupe) + len(skipped_unknown))

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

        sd["reporting_periods"].append(
            {"start": sheet_start, "end": sheet_end, "file": file}
        )

        for g in groups:
            sd["periods"].append(
                {
                    "ship": g["ship"],
                    "start": g["start"],
                    "end": g["end"],
                    "days": (g["end"] - g["start"]).days + 1,
                    "sheet_file": file,
                }
            )

        sd["skipped_unknown"].extend(skipped_unknown)
        sd["skipped_dupe"].extend(skipped_dupe)

        hf = sheet_start.strftime("%m-%d-%Y") if sheet_start else "UNKNOWN"
        ht = sheet_end.strftime("%m-%d-%Y") if sheet_end else "UNKNOWN"
        toris_filename = (
            f"{rate}_{last}_{first}__TORIS_SEA_DUTY_CERT_SHEETS__{hf}_TO_{ht}.pdf"
        ).replace(" ", "_")
        toris_path = os.path.join(TORIS_CERT_FOLDER, toris_filename)

        extracted_total_days = None
        computed_total_days = total_days

        set_progress(current_step=f"Marking TORIS sheet: {file}")
        mark_sheet_with_strikeouts(
            path,
            skipped_dupe,
            skipped_unknown,
            toris_path,
            extracted_total_days,
            computed_total_days,
            strike_color=strike_color,
        )

        add_progress_detail("toris_marked", 1)
        toris_total += 1

        ship_map = {}
        for g in groups:
            ship_map.setdefault(g["ship"], []).append(g)

        for ship, ship_periods in ship_map.items():
            set_progress(current_step=f"Generating PG13 for {ship}")
            make_pdf_for_ship(ship, ship_periods, name)
            add_progress_detail("pg13_created", 1)
            pg13_total += 1

        add_progress_detail("files_processed", 1)
        files_processed_total += 1

        set_progress(
            current_step=f"Completed file: {file}",
            percentage=int((idx / max(total_files, 1)) * 100),
        )

    # -----------------------------------
    # WRITE FINAL TOTALS INTO PROGRESS
    # -----------------------------------
    final_details = {
        "files_processed": files_processed_total,
        "valid_days": valid_days_total,
        "invalid_events": invalid_events_total,
        "pg13_created": pg13_total,
        "toris_marked": toris_total,
    }
    set_progress(details=final_details)
    # -----------------------------------

    set_progress(current_step="Writing summary files")
    write_summary_files(summary_data)

    set_progress(current_step="Merging output package", percentage=100)
    merge_all_pdfs()

    log("PROCESS COMPLETE")
    set_progress(status="complete", current_step="Processing complete", percentage=100)
