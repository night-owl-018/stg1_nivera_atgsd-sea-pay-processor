import os
import re
from datetime import datetime

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
# TEXT → PDF
# ------------------------------------------------

def _write_pdf_from_lines(lines, pdf_path):
    """
    Render a list of text lines into a PDF with a monospaced font.
    """
    c = canvas.Canvas(pdf_path, pagesize=letter)
    width, height = letter

    text = c.beginText(40, height - 40)
    text.setFont("Courier", 9)
    max_chars = 95  # safe for 8.5in width at 9pt Courier

    if not lines:
        lines = ["No validation data available."]

    for raw in lines:
        clean = raw.encode("ascii", "ignore").decode()

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


# ------------------------------------------------
# DATE HELPERS
# ------------------------------------------------

def _fmt_dmy(d, default="UNKNOWN"):
    if not d or not hasattr(d, "strftime"):
        return default
    return d.strftime("%d %b %Y").upper()


def _fmt_iso(d, default=""):
    if not d or not hasattr(d, "strftime"):
        return default
    return d.strftime("%Y-%m-%d")


def _parse_flex_date(date_str):
    """
    Parse date in formats like:
      8/4/2025
      8-4-2025
      8_4_2025
    """
    if not date_str:
        return None
    cleaned = re.sub(r"[-_]", "/", date_str.strip())
    try:
        return datetime.strptime(cleaned, "%m/%d/%Y")
    except Exception:
        return None


# ------------------------------------------------
# REPORTING PERIOD EXTRACTION
# ------------------------------------------------

def extract_reporting_period(raw_text, filename):
    """
    Extract reporting window from:
      1) Text in the sheet: 'From: 8/4/2025 To: 11/24/2025'
      2) Filename: '... 8_4_2025 - 11_24_2025.pdf' or '... 8-4-2025 to 11-24-2025.pdf'
    Returns (start_dt, end_dt, 'raw_start to raw_end') or (None, None, 'UNKNOWN')
    """
    # 1) Best: the sheet's text itself
    m1 = re.search(
        r"From:\s*(\d{1,2}/\d{1,2}/\d{4})\s*To:\s*(\d{1,2}/\d{1,2}/\d{4})",
        raw_text,
        re.IGNORECASE,
    )
    if m1:
        start_str, end_str = m1.group(1), m1.group(2)
        start_dt = datetime.strptime(start_str, "%m/%d/%Y")
        end_dt = datetime.strptime(end_str, "%m/%d/%Y")
        return start_dt, end_dt, f"{start_str} to {end_str}"

    # 2) Filename as fallback
    m2 = re.search(
        r"(\d{1,2}[-_/]\d{1,2}[-_/]\d{4})\s*(?:to|-)\s*(\d{1,2}[-_/]\d{1,2}[-_/]\d{4})",
        filename,
        re.IGNORECASE,
    )
    if m2:
        raw_start, raw_end = m2.group(1), m2.group(2)
        start_dt = _parse_flex_date(raw_start)
        end_dt = _parse_flex_date(raw_end)

        start_str = re.sub(r"[-_]", "/", raw_start)
        end_str = re.sub(r"[-_]", "/", raw_end)

        if start_dt and end_dt:
            return start_dt, end_dt, f"{start_str} to {end_str}"

    # 3) Nothing found
    return None, None, "UNKNOWN"


# ------------------------------------------------
# VALIDATION REPORTS (MASTER + PER MEMBER, MINIMAL-BORDER)
# ------------------------------------------------

def write_validation_reports(summary_data):
    """
    Build per-member and master validation reports using
    clean minimal-border Excel-style tables.
    """
    validation_dir = os.path.join(OUTPUT_DIR, "validation")
    os.makedirs(validation_dir, exist_ok=True)

    master_txt = os.path.join(validation_dir, "VALIDATION_REPORTS_MASTER.txt")
    master_pdf = os.path.join(validation_dir, "VALIDATION_REPORTS_MASTER.pdf")

    master_lines = []

    if not summary_data:
        with open(master_txt, "w", encoding="utf-8") as f:
            f.write("No validation data.")
        _write_pdf_from_lines(["No validation data."], master_pdf)
        return

    # ---------------------------
    # Helper Functions
    # ---------------------------
    def sort_key(item):
        _k, sd = item
        return ((sd.get("last") or "").upper(), (sd.get("first") or "").upper())

    def fix_width(text, width):
        """Trim or pad text to fixed width, add ellipsis if needed."""
        if text is None:
            text = ""
        text = str(text)
        return text[:width-1] + "…" if len(text) > width else text.ljust(width)

    # Column widths used across all minimal-border tables
    W_DATE = 12
    W_SHIP = 15
    W_REASON = 40
    W_OCC = 16
    W_START = 17
    W_END = 17
    W_SRC = 15
    W_SUM_LABEL = 30
    W_SUM_VAL = 12

    for key, sd in sorted(summary_data.items(), key=sort_key):
        rate = sd.get("rate", "")
        last = sd.get("last", "")
        first = sd.get("first", "")
        display_name = f"{rate} {last}, {first}".strip()

        periods = sd.get("periods", [])
        invalids = sd.get("skipped_unknown", [])
        dupes = sd.get("skipped_dupe", [])
        reporting_periods = sd.get("reporting_periods", [])

        total_days = sum(p["days"] for p in periods)
        status = "VALID" if not (invalids or dupes) else "WITH DISCREPANCIES"

        lines = []
        lines.append("=" * 90)
        lines.append(f"MEMBER: {display_name}")
        lines.append("=" * 90)
        lines.append("")

        # ---------------------------------------------------------
        # REPORTING PERIODS (Minimal Border)
        # ---------------------------------------------------------
        lines.append("REPORTING PERIODS")
        lines.append(
            fix_width("START DATE", W_START) + "  " +
            fix_width("END DATE", W_END) + "  " +
            fix_width("SOURCE", W_SRC)
        )
        lines.append(
            "-" * W_START + "  " +
            "-" * W_END + "  " +
            "-" * W_SRC
        )

        if reporting_periods:
            for rp in reporting_periods:
                rs = _fmt_dmy(rp.get("start"))
                re_ = _fmt_dmy(rp.get("end"))
                src = "SEA DUTY SHEET"
                lines.append(
                    fix_width(rs, W_START) + "  " +
                    fix_width(re_, W_END) + "  " +
                    fix_width(src, W_SRC)
                )
        else:
            lines.append(
                fix_width("UNKNOWN", W_START) + "  " +
                fix_width("UNKNOWN", W_END) + "  " +
                fix_width("NO DATA", W_SRC)
            )
        lines.append("")

        # ---------------------------------------------------------
        # SUMMARY (Minimal Border)
        # ---------------------------------------------------------
        lines.append("SUMMARY")
        lines.append(
            fix_width("METRIC", W_SUM_LABEL) + "  " +
            fix_width("VALUE", W_SUM_VAL)
        )
        lines.append(
            "-" * W_SUM_LABEL + "  " +
            "-" * W_SUM_VAL
        )

        lines.append(fix_width("Total Valid Sea Pay Days", W_SUM_LABEL) + "  " + fix_width(total_days, W_SUM_VAL))
        lines.append(fix_width("Valid Period Count", W_SUM_LABEL) + "  " + fix_width(len(periods), W_SUM_VAL))
        lines.append(fix_width("Invalid Events", W_SUM_LABEL) + "  " + fix_width(len(invalids), W_SUM_VAL))
        lines.append(fix_width("Duplicate Conflicts", W_SUM_LABEL) + "  " + fix_width(len(dupes), W_SUM_VAL))
        lines.append(fix_width("Status", W_SUM_LABEL) + "  " + fix_width(status, W_SUM_VAL))
        lines.append("")

        # ---------------------------------------------------------
        # VALID PERIODS (Minimal Border)
        # ---------------------------------------------------------
        lines.append("VALID SEA PAY PERIODS")
        lines.append(
            fix_width("SHIP", W_SHIP) + "  " +
            fix_width("START DATE", W_START) + "  " +
            fix_width("END DATE", W_END) + "  " +
            fix_width("DAYS", 5)
        )
        lines.append(
            "-" * W_SHIP + "  " +
            "-" * W_START + "  " +
            "-" * W_END + "  " +
            "-" * 5
        )

        if periods:
            for p in periods:
                lines.append(
                    fix_width((p["ship"] or "").upper(), W_SHIP) + "  " +
                    fix_width(_fmt_dmy(p["start"]), W_START) + "  " +
                    fix_width(_fmt_dmy(p["end"]), W_END) + "  " +
                    fix_width(p["days"], 5)
                )
        else:
            lines.append(
                fix_width("NONE", W_SHIP) + "  " +
                fix_width("", W_START) + "  " +
                fix_width("", W_END) + "  " +
                fix_width("", 5)
            )
        lines.append("")

        # ---------------------------------------------------------
        # INVALID EVENTS (Minimal Border)
        # ---------------------------------------------------------
        lines.append("INVALID / EXCLUDED EVENTS")
        lines.append(
            fix_width("DATE", W_DATE) + "  " +
            fix_width("SHIP", W_SHIP) + "  " +
            fix_width("REASON", W_REASON)
        )
        lines.append(
            "-" * W_DATE + "  " +
            "-" * W_SHIP + "  " +
            "-" * W_REASON
        )

        if invalids:
            for e in invalids:
                dt = e.get("date", "UNKNOWN")
                ship = e.get("ship") or e.get("ship_name", "") or "N/A"
                reason = e.get("reason", "Excluded / non-qualifying")
                lines.append(
                    fix_width(dt, W_DATE) + "  " +
                    fix_width(ship, W_SHIP) + "  " +
                    fix_width(reason, W_REASON)
                )
        else:
            lines.append(
                fix_width("NONE", W_DATE) + "  " +
                fix_width("", W_SHIP) + "  " +
                fix_width("", W_REASON)
            )
        lines.append("")

        # ---------------------------------------------------------
        # DUPLICATE EVENTS (Minimal Border)
        # ---------------------------------------------------------
        lines.append("DUPLICATE DATE CONFLICTS")
        lines.append(
            fix_width("DATE", W_DATE) + "  " +
            fix_width("SHIP", W_SHIP) + "  " +
            fix_width("OCCURRENCE", W_OCC)
        )
        lines.append(
            "-" * W_DATE + "  " +
            "-" * W_SHIP + "  " +
            "-" * W_OCC
        )

        if dupes:
            for e in dupes:
                dt = e.get("date", "UNKNOWN")
                ship = e.get("ship") or e.get("ship_name", "") or "N/A"
                occ = e.get("occ_idx") or e.get("occurrence")
                occ_label = f"#{occ} (duplicate)" if occ else "DUP"
                lines.append(
                    fix_width(dt, W_DATE) + "  " +
                    fix_width(ship, W_SHIP) + "  " +
                    fix_width(occ_label, W_OCC)
                )
        else:
            lines.append(
                fix_width("NONE", W_DATE) + "  " +
                fix_width("", W_SHIP) + "  " +
                fix_width("", W_OCC)
            )

        lines.append("")
        lines.append("=" * 90)
        lines.append("")

        # Write per-member files
        safe = f"{rate}_{last}_{first}".replace(" ", "_").replace(",", "")
        txt_path = os.path.join(validation_dir, f"VALIDATION_{safe}.txt")
        pdf_path = os.path.join(validation_dir, f"VALIDATION_{safe}.pdf")

        with open(txt_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

        _write_pdf_from_lines(lines, pdf_path)

        # Append to master
        master_lines.extend(lines)

    with open(master_txt, "w", encoding="utf-8") as f:
        f.write("\n".join(master_lines))

    _write_pdf_from_lines(master_lines, master_pdf)


# ------------------------------------------------
# VALIDATION LEDGER (MINIMAL-BORDER)
# ------------------------------------------------

def write_validation_ledger(summary_data, generated_at):
    """
    Produce a minimal-border Excel-style ledger of all members and their
    reporting periods. No box tables, aligned columns, overflow-safe.
    """
    validation_dir = os.path.join(OUTPUT_DIR, "validation")
    os.makedirs(validation_dir, exist_ok=True)

    txt_path = os.path.join(validation_dir, "VALIDATION_LEDGER.txt")
    pdf_path = os.path.join(validation_dir, "VALIDATION_LEDGER.pdf")

    W_RATE = 4
    W_NAME = 24
    W_START = 17
    W_END = 17
    W_GEN = 20

    def fix_width(text, width):
        if text is None:
            text = ""
        text = str(text)
        return text[:width-1] + "…" if len(text) > width else text.ljust(width)

    def sort_key(item):
        _k, sd = item
        return ((sd.get("last") or "").upper(), (sd.get("first") or "").upper())

    gen_str = generated_at.strftime("%d %b %Y %H:%M")

    lines = []

    # Header
    lines.append(
        fix_width("RATE", W_RATE) + "  " +
        fix_width("NAME", W_NAME) + "  " +
        fix_width("START DATE", W_START) + "  " +
        fix_width("END DATE", W_END) + "  " +
        fix_width("GENERATED", W_GEN)
    )
    lines.append(
        "-" * W_RATE + "  " +
        "-" * W_NAME + "  " +
        "-" * W_START + "  " +
        "-" * W_END + "  " +
        "-" * W_GEN
    )

    # Rows
    for key, sd in sorted(summary_data.items(), key=sort_key):
        rate = sd.get("rate", "") or ""
        last = sd.get("last", "") or ""
        first = sd.get("first", "") or ""
        name = f"{last}, {first}"

        reporting_periods = sd.get("reporting_periods", [])
        if not reporting_periods:
            lines.append(
                fix_width(rate, W_RATE) + "  " +
                fix_width(name, W_NAME) + "  " +
                fix_width("UNKNOWN", W_START) + "  " +
                fix_width("UNKNOWN", W_END) + "  " +
                fix_width(gen_str, W_GEN)
            )
            continue

        for rp in reporting_periods:
            rs = _fmt_dmy(rp.get("start"))
            re_ = _fmt_dmy(rp.get("end"))
            lines.append(
                fix_width(rate, W_RATE) + "  " +
                fix_width(name, W_NAME) + "  " +
                fix_width(rs, W_START) + "  " +
                fix_width(re_, W_END) + "  " +
                fix_width(gen_str, W_GEN)
            )

    with open(txt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    _write_pdf_from_lines(lines, pdf_path)


# ------------------------------------------------
# TRACKING EXPORTS (JSON + CSV)
# ------------------------------------------------

def write_json_tracker(summary_data, generated_at):
    import json

    tracking_dir = os.path.join(OUTPUT_DIR, "tracking")
    os.makedirs(tracking_dir, exist_ok=True)

    payload = {
        "generated_at": generated_at.isoformat(),
        "tool_version": "1.1.0",
        "sailors": []
    }

    for key, sd in summary_data.items():
        periods = sd.get("periods", [])
        reporting_periods = sd.get("reporting_periods", [])
        skipped_unknown = sd.get("skipped_unknown", [])
        skipped_dupe = sd.get("skipped_dupe", [])

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
                    "range_text": rp.get("range_text", "")
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
                    "source_file": p.get("sheet_file", "")
                }
                for p in periods
            ],
            "invalid_events": skipped_unknown,
            "duplicate_events": skipped_dupe
        })

    out_path = os.path.join(tracking_dir, f"SeaPay_Tracking_{generated_at.strftime('%Y-%m-%d')}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def write_csv_tracker(summary_data, generated_at):
    import csv

    tracking_dir = os.path.join(OUTPUT_DIR, "tracking")
    os.makedirs(tracking_dir, exist_ok=True)

    csv_path = os.path.join(tracking_dir, f"SeaPay_Tracking_{generated_at.strftime('%Y-%m-%d')}.csv")

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
            "SourceFile"
        ])

        generated_at_str = generated_at.isoformat()

        for key, sd in summary_data.items():
            periods = sd.get("periods", [])
            reporting_periods = sd.get("reporting_periods", [])
            skipped_unknown = sd.get("skipped_unknown", [])
            skipped_dupe = sd.get("skipped_dupe", [])

            invalid_count = len(skipped_unknown)
            dupe_count = len(skipped_dupe)
            status = "VALID" if not (invalid_count or dupe_count) else "WITH_DISCREPANCIES"

            rate = sd.get("rate", "")
            last = sd.get("last", "")
            first = sd.get("first", "")

            if reporting_periods:
                rp_start = _fmt_iso(reporting_periods[0].get("start"))
                rp_end = _fmt_iso(reporting_periods[0].get("end"))
            else:
                rp_start = ""
                rp_end = ""

            if periods:
                for p in periods:
                    writer.writerow([
                        rate,
                        last,
                        first,
                        rp_start,
                        rp_end,
                        _fmt_iso(p["start"]),
                        _fmt_iso(p["end"]),
                        p["days"],
                        invalid_count,
                        dupe_count,
                        status,
                        generated_at_str,
                        p.get("sheet_file", "")
                    ])
            else:
                writer.writerow([
                    rate,
                    last,
                    first,
                    rp_start,
                    rp_end,
                    "",
                    "",
                    0,
                    invalid_count,
                    dupe_count,
                    status,
                    generated_at_str,
                    ""
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

        raw = strip_times(ocr_pdf(path))

        # Reporting period
        sheet_start, sheet_end, sheet_range_text = extract_reporting_period(raw, file)

        # Member name
        try:
            name = extract_member_name(raw)
            log(f"NAME → {name}")
        except Exception as e:
            log(f"NAME ERROR → {e}")
            continue

        # Parse TORIS rows
        year = extract_year_from_filename(file)
        rows, skipped_dupe, skipped_unknown = parse_rows(raw, year)

        # Group valid rows by ship
        groups = group_by_ship(rows)

        # Total days for this sheet
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
            strike_color=strike_color
        )

        # Build NAVPERS PDFs by ship
        ship_periods = {}
        for g in groups:
            ship_periods.setdefault(g["ship"], []).append(g)

        for ship, periods in ship_periods.items():
            make_pdf_for_ship(ship, periods, name)

        # Identity
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
                "reporting_periods": []
            }

        sd = summary_data[key]

        # Reporting window for this sheet
        sd["reporting_periods"].append({
            "start": sheet_start,
            "end": sheet_end,
            "file": file,
            "range_text": sheet_range_text
        })

        # Valid periods
        for g in groups:
            days = (g["end"] - g["start"]).days + 1
            sd["periods"].append({
                "ship": g["ship"],
                "start": g["start"],
                "end": g["end"],
                "days": days,
                "sheet_start": sheet_start,
                "sheet_end": sheet_end,
                "sheet_file": file
            })

        # Skipped
        sd["skipped_unknown"].extend(skipped_unknown)
        sd["skipped_dupe"].extend(skipped_dupe)

    # Merge NAVPERS PDFs
    merge_all_pdfs()

    # Summary TXT
    write_summary_files(summary_data)

    # Validation (per member + master)
    write_validation_reports(summary_data)
    log("VALIDATION REPORTS DONE")

    # Ledger (now minimal-border too)
    write_validation_ledger(summary_data, run_generated_at)
    log("LEDGER DONE")

    # Tracking
    write_json_tracker(summary_data, run_generated_at)
    write_csv_tracker(summary_data, run_generated_at)
    log("TRACKING DONE")

    log("✅ ALL OPERATIONS COMPLETE")
