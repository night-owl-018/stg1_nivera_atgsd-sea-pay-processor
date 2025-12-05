import os
import re
from datetime import datetime, timedelta

from app.core.logger import log
from app.core.config import DATA_DIR, OUTPUT_DIR
from app.core.ocr import ocr_pdf, strip_times, extract_member_name
from app.core.parser import parse_rows, extract_year_from_filename, group_by_ship
from app.core.pdf_writer import make_pdf_for_ship
from app.core.strikeout import mark_sheet_with_strikeouts
from app.core.merge import merge_all_pdfs
from app.core.summary import write_summary_files
from app.core.rates import resolve_identity

from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter


# ------------------------------------------------
# SMALL HELPERS
# ------------------------------------------------

def _write_pdf_from_lines(lines, pdf_path):
    """
    Render a list of text lines into a clean, readable PDF.
    """
    c = canvas.Canvas(pdf_path, pagesize=letter)
    width, height = letter

    text = c.beginText(40, height - 40)
    text.setFont("Courier", 9)
    max_chars = 95

    if not lines:
        lines = ["No validation data available."]

    for raw in lines:
        clean = raw.encode("ascii", "ignore").decode()

        # Soft wrap long lines
        while len(clean) > max_chars:
            text.textLine(clean[:max_chars])
            clean = clean[max_chars:]

            if text.getY() < 40:
                c.drawText(text)
                c.showPage()
                text = c.beginText(40, height - 40)
                text.setFont("Courier", 9)

        text.textLine(clean)

        if text.getY() < 40:
            c.drawText(text)
            c.showPage()
            text = c.beginText(40, height - 40)
            text.setFont("Courier", 9)

    c.drawText(text)
    c.save()


def _fmt_dmy(d, default="UNKNOWN"):
    """
    Format a datetime as DD MON YYYY in UPPERCASE (04 AUG 2025).
    """
    if not d or not hasattr(d, "strftime"):
        return default
    return d.strftime("%d %b %Y").upper()


def _fmt_iso(d, default=""):
    """
    Format a datetime as YYYY-MM-DD for JSON/CSV.
    """
    if not d or not hasattr(d, "strftime"):
        return default
    return d.strftime("%Y-%m-%d")


def extract_reporting_period(raw_text, filename):
    """
    Extract reporting period From/To dates from:
      1) The OCR text: 'From: 8/4/2025 To: 11/24/2025'
      2) Fallback: the filename: '... 8-04-2025 to 11-24-2025.pdf'
    Returns: (start_dt, end_dt, range_text_str)
    """
    # Try inside the sheet text first
    m = re.search(
        r"From:\s*(\d{1,2}/\d{1,2}/\d{4})\s*To:\s*(\d{1,2}/\d{1,2}/\d{4})",
        raw_text,
        re.IGNORECASE,
    )
    if m:
        start_str, end_str = m.group(1), m.group(2)
        try:
            start_dt = datetime.strptime(start_str, "%m/%d/%Y")
            end_dt = datetime.strptime(end_str, "%m/%d/%Y")
            return start_dt, end_dt, f"{start_str} to {end_str}"
        except Exception:
            pass

    # Fallback to filename pattern: 8-04-2025 to 11-24-2025
    m2 = re.search(
        r"(\d{1,2}-\d{1,2}-\d{4})\s*to\s*(\d{1,2}-\d{1,2}-\d{4})",
        filename,
        re.IGNORECASE,
    )
    if m2:
        start_str, end_str = m2.group(1), m2.group(2)
        try:
            start_dt = datetime.strptime(start_str, "%m-%d-%Y")
            end_dt = datetime.strptime(end_str, "%m-%d-%Y")
            return start_dt, end_dt, f"{start_str} to {end_str}"
        except Exception:
            pass

    # Nothing found
    return None, None, "UNKNOWN"


# ------------------------------------------------
# PROFESSIONAL VALIDATION REPORTS
# ------------------------------------------------

def write_validation_reports(summary_data):
    """
    Per-sailor and master validation reports.
    Now includes REPORTING PERIODS section for each sailor.
    """
    validation_dir = os.path.join(OUTPUT_DIR, "validation")
    os.makedirs(validation_dir, exist_ok=True)

    master_txt_path = os.path.join(validation_dir, "VALIDATION_REPORTS_MASTER.txt")
    master_pdf_path = os.path.join(validation_dir, "VALIDATION_REPORTS_MASTER.pdf")

    master_lines = []

    if not summary_data:
        with open(master_txt_path, "w", encoding="utf-8") as f:
            f.write("No validation data available.\n")
        _write_pdf_from_lines(["No validation data available."], master_pdf_path)
        return

    def sort_key(item):
        _key, sd = item
        return ((sd.get("last") or "").upper(), (sd.get("first") or "").upper())

    for key, sd in sorted(summary_data.items(), key=sort_key):
        rate = sd.get("rate") or ""
        last = sd.get("last") or ""
        first = sd.get("first") or ""

        display_name = f"{rate} {last}, {first}".strip()

        periods = sd.get("periods", [])
        skipped_unknown = sd.get("skipped_unknown", [])
        skipped_dupe = sd.get("skipped_dupe", [])
        reporting_periods = sd.get("reporting_periods", [])

        total_days = sum(p["days"] for p in periods)

        lines = []
        lines.append("=" * 69)
        lines.append(f"SAILOR: {display_name}")
        lines.append("=" * 69)
        lines.append("")

        # REPORTING PERIODS
        lines.append("REPORTING PERIODS (SEA DUTY CERTIFICATION SHEETS)")
        lines.append("-" * 69)
        if reporting_periods:
            lines.append("  REPORTING PERIOD START   REPORTING PERIOD END     SOURCE FILE")
            lines.append("  -----------------------  -----------------------  ------------------------------")
            for rp in reporting_periods:
                rs = _fmt_dmy(rp.get("start"))
                re_ = _fmt_dmy(rp.get("end"))
                src = os.path.basename(rp.get("file", ""))[:30]
                lines.append(f"  {rs:23}  {re_:23}  {src}")
        else:
            lines.append("  NONE RECORDED")
        lines.append("")

        # SUMMARY
        lines.append("SUMMARY")
        lines.append("-" * 69)
        lines.append(f"  Total Valid Sea Pay Days : {total_days}")
        lines.append(f"  Valid Period Count       : {len(periods)}")
        lines.append(f"  Invalid / Excluded Events: {len(skipped_unknown)}")
        lines.append(f"  Duplicate Date Conflicts : {len(skipped_dupe)}")
        lines.append("")

        # VALID PERIODS
        lines.append("VALID SEA PAY PERIODS")
        lines.append("-" * 69)
        if periods:
            lines.append("  SHIP                START        END          DAYS")
            lines.append("  ------------------- ------------ ------------ ----")
            for p in periods:
                ship = (p["ship"] or "").upper()
                start = _fmt_dmy(p["start"])
                end = _fmt_dmy(p["end"])
                days = p["days"]
                lines.append(f"  {ship[:19]:19} {start:12} {end:12} {days:4}")
        else:
            lines.append("  NONE")
        lines.append("")

        # INVALID / EXCLUDED
        lines.append("INVALID / EXCLUDED EVENTS")
        lines.append("-" * 69)
        if skipped_unknown:
            for entry in skipped_unknown:
                date = entry.get("date", "UNKNOWN")
                ship = entry.get("ship") or entry.get("ship_name") or ""
                reason = entry.get("reason") or "Excluded / unrecognized / non-qualifying"
                detail = f"{date}"
                if ship:
                    detail += f" | {ship}"
                lines.append(f"  - {detail} — {reason}")
        else:
            lines.append("  NONE")
        lines.append("")

        # DUPLICATES
        lines.append("DUPLICATE DATE CONFLICTS")
        lines.append("-" * 69)
        if skipped_dupe:
            for entry in skipped_dupe:
                date = entry.get("date", "UNKNOWN")
                ship = entry.get("ship") or entry.get("ship_name") or ""
                occ = entry.get("occ_idx") or entry.get("occurrence") or ""
                detail = f"{date}"
                if ship:
                    detail += f" | {ship}"
                if occ:
                    detail += f" | occurrence #{occ}"
                lines.append(f"  - {detail}")
        else:
            lines.append("  NONE")
        lines.append("")

        # RECOMMENDATIONS
        lines.append("RECOMMENDATIONS")
        lines.append("-" * 69)
        if skipped_unknown or skipped_dupe:
            lines.append("  - Review TORIS export for the dates listed above.")
            lines.append("  - Confirm ship names and event types against current guidance.")
            lines.append("  - Provide corrected certification sheet to ATG/PSD if required.")
        else:
            lines.append("  - No discrepancies detected based on current input.")
        lines.append("")
        lines.append("")

        # Per-sailor outputs
        safe_name = f"{rate}_{last}_{first}".strip().replace(" ", "_").replace(",", "")
        if not safe_name:
            safe_name = key.replace(" ", "_").replace(",", "")

        sailor_txt_path = os.path.join(validation_dir, f"VALIDATION_{safe_name}.txt")
        sailor_pdf_path = os.path.join(validation_dir, f"VALIDATION_{safe_name}.pdf")

        with open(sailor_txt_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

        _write_pdf_from_lines(lines, sailor_pdf_path)

        # Add to master
        master_lines.extend(lines)
        master_lines.append("=" * 69)
        master_lines.append("")

    with open(master_txt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(master_lines))

    _write_pdf_from_lines(master_lines, master_pdf_path)


# ------------------------------------------------
# VALIDATION LEDGER (MASTER SHEET)
# ------------------------------------------------

def write_validation_ledger(summary_data, generated_at):
    """
    Build a master ledger of all sailors and reporting periods.
    One row per sailor per reporting window.
    """
    validation_dir = os.path.join(OUTPUT_DIR, "validation")
    os.makedirs(validation_dir, exist_ok=True)

    ledger_txt_path = os.path.join(validation_dir, "VALIDATION_LEDGER.txt")
    ledger_pdf_path = os.path.join(validation_dir, "VALIDATION_LEDGER.pdf")

    lines = []
    lines.append("=" * 69)
    lines.append("SEA PAY CERTIFICATION LEDGER")
    lines.append(f"Generated: {generated_at.strftime('%d %b %Y %H:%M')} LOCAL")
    lines.append("=" * 69)
    lines.append("")

    header = (
        "RATE  NAME                     REPORTING PERIOD START   "
        "REPORTING PERIOD END     GENERATED"
    )
    lines.append(header)
    lines.append(
        "----- ------------------------- -----------------------  "
        "-----------------------  ------------------------"
    )

    def sort_key(item):
        _key, sd = item
        return ((sd.get("last") or "").upper(), (sd.get("first") or "").upper())

    for key, sd in sorted(summary_data.items(), key=sort_key):
        rate = sd.get("rate") or ""
        last = sd.get("last") or ""
        first = sd.get("first") or ""
        name = f"{last}, {first}"

        reporting_periods = sd.get("reporting_periods", [])
        if reporting_periods:
            for rp in reporting_periods:
                rs = _fmt_dmy(rp.get("start"))
                re_ = _fmt_dmy(rp.get("end"))
                gen_str = generated_at.strftime("%d %b %Y %H:%M")
                lines.append(
                    f"{rate:5} {name[:25]:25} {rs:23}  {re_:23}  {gen_str}"
                )
        else:
            gen_str = generated_at.strftime("%d %b %Y %H:%M")
            lines.append(
                f"{rate:5} {name[:25]:25} {'UNKNOWN':23}  {'UNKNOWN':23}  {gen_str}"
            )

    with open(ledger_txt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    _write_pdf_from_lines(lines, ledger_pdf_path)


# ------------------------------------------------
# TRACKING EXPORTS (JSON + CSV)
# ------------------------------------------------

def write_json_tracker(summary_data, generated_at):
    """
    Build a local JSON tracking file for the user to download and store.
    Includes reporting periods and per-ship periods.
    """
    import json

    tracking_dir = os.path.join(OUTPUT_DIR, "tracking")
    os.makedirs(tracking_dir, exist_ok=True)

    payload = {
        "generated_at": generated_at.isoformat(),
        "tool_version": "1.0.0",
        "sailors": []
    }

    for key, sd in summary_data.items():
        periods = sd.get("periods", [])
        skipped_unknown = sd.get("skipped_unknown", [])
        skipped_dupe = sd.get("skipped_dupe", [])
        reporting_periods = sd.get("reporting_periods", [])

        total_days = sum(p["days"] for p in periods)
        status = "VALID" if not (skipped_unknown or skipped_dupe) else "WITH_DISCREPANCIES"

        payload["sailors"].append({
            "rate": sd.get("rate", ""),
            "last": sd.get("last", ""),
            "first": sd.get("first", ""),
            "total_days": total_days,
            "status": status,
            "reporting_periods": [
                {
                    "start": _fmt_iso(rp.get("start")),
                    "end": _fmt_iso(rp.get("end")),
                    "file": rp.get("file", ""),
                    "range_text": rp.get("range_text", ""),
                }
                for rp in reporting_periods
            ],
            "periods": [
                {
                    "ship": p["ship"],
                    "start": _fmt_iso(p["start"]),
                    "end": _fmt_iso(p["end"]),
                    "days": p["days"],
                    "reporting_period_start": _fmt_iso(p.get("sheet_start")),
                    "reporting_period_end": _fmt_iso(p.get("sheet_end")),
                    "source_file": p.get("sheet_file", ""),
                }
                for p in periods
            ],
            "invalid_events": skipped_unknown,
            "duplicate_events": skipped_dupe,
        })

    json_name = f"SeaPay_Tracking_{generated_at.strftime('%Y-%m-%d')}.json"
    out_path = os.path.join(tracking_dir, json_name)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def write_csv_tracker(summary_data, generated_at):
    """
    Build a CSV tracking file that PSD / ATG can open in Excel.
    One row per valid sea pay period, including reporting window.
    """
    import csv

    tracking_dir = os.path.join(OUTPUT_DIR, "tracking")
    os.makedirs(tracking_dir, exist_ok=True)

    csv_name = f"SeaPay_Tracking_{generated_at.strftime('%Y-%m-%d')}.csv"
    csv_path = os.path.join(tracking_dir, csv_name)

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "Rate",
            "Last",
            "First",
            "ReportingPeriodStart",
            "ReportingPeriodEnd",
            "Ship",
            "Start",
            "End",
            "Days",
            "InvalidCount",
            "DuplicateCount",
            "Status",
            "GeneratedAt",
            "SourceFile",
        ])

        generated_at_str = generated_at.isoformat()

        for key, sd in summary_data.items():
            periods = sd.get("periods", [])
            skipped_unknown = sd.get("skipped_unknown", [])
            skipped_dupe = sd.get("skipped_dupe", [])

            invalid_count = len(skipped_unknown)
            dupe_count = len(skipped_dupe)
            status = "VALID" if not (invalid_count or dupe_count) else "WITH_DISCREPANCIES"

            rate = sd.get("rate", "")
            last = sd.get("last", "")
            first = sd.get("first", "")

            if periods:
                for p in periods:
                    writer.writerow([
                        rate,
                        last,
                        first,
                        _fmt_iso(p.get("sheet_start")),
                        _fmt_iso(p.get("sheet_end")),
                        p["ship"],
                        _fmt_iso(p["start"]),
                        _fmt_iso(p["end"]),
                        p["days"],
                        invalid_count,
                        dupe_count,
                        status,
                        generated_at_str,
                        p.get("sheet_file", ""),
                    ])
            else:
                # No valid periods, still record the sailor with reporting period if any
                reporting_periods = sd.get("reporting_periods", [])
                if reporting_periods:
                    rp0 = reporting_periods[0]
                    rp_start = _fmt_iso(rp0.get("start"))
                    rp_end = _fmt_iso(rp0.get("end"))
                else:
                    rp_start = ""
                    rp_end = ""

                writer.writerow([
                    rate,
                    last,
                    first,
                    rp_start,
                    rp_end,
                    "",
                    "",
                    "",
                    0,
                    invalid_count,
                    dupe_count,
                    status,
                    generated_at_str,
                    "",
                ])


# ------------------------------------------------
# MAIN PROCESSOR
# ------------------------------------------------

def process_all(strike_color="black"):
    files = [f for f in os.listdir(DATA_DIR) if f.lower().endswith(".pdf")]

    if not files:
        log("NO INPUT FILES")
        return

    log("=== PROCESS STARTED ===")
    summary_data = {}
    run_generated_at = datetime.now()

    for file in files:
        path = os.path.join(DATA_DIR, file)
        log(f"OCR → {file}")

        # OCR and strip times
        raw = strip_times(ocr_pdf(path))

        # Extract reporting period from sheet / filename
        sheet_start, sheet_end, sheet_range_text = extract_reporting_period(raw, file)

        # Extract member name
        try:
            name = extract_member_name(raw)
            log(f"NAME → {name}")
        except Exception as e:
            log(f"NAME ERROR → {e}")
            continue

        # Parse rows
        year = extract_year_from_filename(file)
        rows, skipped_dupe, skipped_unknown = parse_rows(raw, year)

        # Group valid periods by ship
        groups = group_by_ship(rows)

        # Total valid days for sheet (for total-days correction on struck sheet)
        total_days = sum((g["end"] - g["start"]).days + 1 for g in groups)

        # Strikeout marked sheet
        marked_dir = os.path.join(OUTPUT_DIR, "marked_sheets")
        os.makedirs(marked_dir, exist_ok=True)
        marked_path = os.path.join(marked_dir, f"MARKED_{os.path.splitext(file)[0]}.pdf")

        mark_sheet_with_strikeouts(
            path,
            skipped_dupe,
            skipped_unknown,
            marked_path,
            total_days,
            strike_color=strike_color,
        )

        # Build 1070 PDFs for each ship
        ship_periods = {}
        for g in groups:
            ship_periods.setdefault(g["ship"], []).append(g)

        for ship, periods in ship_periods.items():
            make_pdf_for_ship(ship, periods, name)

        # Resolve sailor identity (rate/last/first)
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
                "reporting_periods": [],
            }

        sd = summary_data[key]

        # Track reporting period per sheet
        sd["reporting_periods"].append({
            "start": sheet_start,
            "end": sheet_end,
            "file": file,
            "range_text": sheet_range_text,
        })

        # Add valid grouped periods with linkage back to reporting period + source file
        for g in groups:
            days = (g["end"] - g["start"]).days + 1
            sd["periods"].append({
                "ship": g["ship"],
                "start": g["start"],
                "end": g["end"],
                "days": days,
                "sheet_start": sheet_start,
                "sheet_end": sheet_end,
                "sheet_file": file,
            })

        # Add invalid/skipped entries
        sd["skipped_unknown"].extend(skipped_unknown)
        sd["skipped_dupe"].extend(skipped_dupe)

    # Merge all NAVPERS PDFs
    merge_all_pdfs()

    # Write summary text files
    write_summary_files(summary_data)

    # Validation reports per sailor + master
    write_validation_reports(summary_data)
    log("VALIDATION REPORTS UPDATED")

    # Validation Ledger (master reporting-period view)
    write_validation_ledger(summary_data, run_generated_at)
    log("VALIDATION LEDGER UPDATED")

    # Tracking (JSON + CSV)
    write_json_tracker(summary_data, run_generated_at)
    write_csv_tracker(summary_data, run_generated_at)
    log("TRACKING FILES UPDATED")

    log("✅ ALL OPERATIONS COMPLETE")
