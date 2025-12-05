import os
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
# HELPER: WRITE LINES TO PDF (CLEAN WRAPPED FORMAT)
# ------------------------------------------------
def _write_pdf_from_lines(lines, pdf_path):
    c = canvas.Canvas(pdf_path, pagesize=letter)
    width, height = letter

    text = c.beginText(40, height - 40)
    text.setFont("Courier", 9)
    max_chars = 95

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
# PROFESSIONAL VALIDATION REPORTS
# ------------------------------------------------
def write_validation_reports(summary_data):
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

        total_days = sum(p["days"] for p in periods)

        lines = []
        lines.append("=" * 69)
        lines.append(f"SAILOR: {display_name}")
        lines.append("=" * 69)
        lines.append("")

        lines.append("SUMMARY")
        lines.append("-" * 69)
        lines.append(f"  Total Valid Sea Pay Days : {total_days}")
        lines.append(f"  Valid Period Count       : {len(periods)}")
        lines.append(f"  Invalid / Excluded Events: {len(skipped_unknown)}")
        lines.append(f"  Duplicate Date Conflicts : {len(skipped_dupe)}")
        lines.append("")

        lines.append("VALID SEA PAY PERIODS")
        lines.append("-" * 69)
        if periods:
            lines.append("  SHIP                START        END          DAYS")
            lines.append("  ------------------- ------------ ------------ ----")
            for p in periods:
                ship = (p["ship"] or "").upper()
                start = p["start"].strftime("%d %b %Y")
                end = p["end"].strftime("%d %b %Y")
                days = p["days"]
                lines.append(f"  {ship[:19]:19} {start:12} {end:12} {days:4}")
        else:
            lines.append("  NONE")
        lines.append("")

        lines.append("INVALID / EXCLUDED EVENTS")
        lines.append("-" * 69)
        if skipped_unknown:
            for entry in skipped_unknown:
                date = entry.get("date", "UNKNOWN")
                ship = entry.get("ship", "")
                reason = entry.get("reason", "Excluded event")
                detail = f"{date}"
                if ship:
                    detail += f" | {ship}"
                lines.append(f"  - {detail} — {reason}")
        else:
            lines.append("  NONE")
        lines.append("")

        lines.append("DUPLICATE DATE CONFLICTS")
        lines.append("-" * 69)
        if skipped_dupe:
            for entry in skipped_dupe:
                date = entry.get("date", "UNKNOWN")
                ship = entry.get("ship", "")
                occ = entry.get("occ_idx", "")
                detail = f"{date}"
                if ship:
                    detail += f" | {ship}"
                if occ:
                    detail += f" | occurrence #{occ}"
                lines.append(f"  - {detail}")
        else:
            lines.append("  NONE")
        lines.append("")

        lines.append("RECOMMENDATIONS")
        lines.append("-" * 69)
        if skipped_unknown or skipped_dupe:
            lines.append("  - Review TORIS export for discrepancies.")
            lines.append("  - Verify ship names and event types.")
        else:
            lines.append("  - No discrepancies detected.")
        lines.append("")
        lines.append("")

        safe_name = f"{rate}_{last}_{first}".replace(" ", "_").replace(",", "")
        if not safe_name:
            safe_name = key.replace(" ", "_").replace(",", "")

        sailor_txt_path = os.path.join(validation_dir, f"VALIDATION_{safe_name}.txt")
        sailor_pdf_path = os.path.join(validation_dir, f"VALIDATION_{safe_name}.pdf")

        with open(sailor_txt_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

        _write_pdf_from_lines(lines, sailor_pdf_path)

        master_lines.extend(lines)
        master_lines.append("=" * 69)
        master_lines.append("")

    with open(master_txt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(master_lines))

    _write_pdf_from_lines(master_lines, master_pdf_path)


# ------------------------------------------------
# TRACKING EXPORTS (JSON + CSV)
# ------------------------------------------------
def write_json_tracker(summary_data):
    import json

    tracking_dir = os.path.join(OUTPUT_DIR, "tracking")
    os.makedirs(tracking_dir, exist_ok=True)

    payload = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "tool_version": "1.0.0",
        "sailors": []
    }

    for key, sd in summary_data.items():
        periods = sd.get("periods", [])
        skipped_unknown = sd.get("skipped_unknown", [])
        skipped_dupe = sd.get("skipped_dupe", [])

        total_days = sum(p["days"] for p in periods)
        status = "VALID" if not (skipped_unknown or skipped_dupe) else "WITH_DISCREPANCIES"

        def fmt(d):
            return d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)

        payload["sailors"].append({
            "rate": sd.get("rate", ""),
            "last": sd.get("last", ""),
            "first": sd.get("first", ""),
            "total_days": total_days,
            "status": status,
            "periods": [
                {"ship": p["ship"], "start": fmt(p["start"]), "end": fmt(p["end"]), "days": p["days"]}
                for p in periods
            ],
            "invalid_events": skipped_unknown,
            "duplicate_events": skipped_dupe,
        })

    out_path = os.path.join(tracking_dir, f"SeaPay_Tracking_{datetime.now().strftime('%Y-%m-%d')}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def write_csv_tracker(summary_data):
    import csv

    tracking_dir = os.path.join(OUTPUT_DIR, "tracking")
    os.makedirs(tracking_dir, exist_ok=True)

    csv_path = os.path.join(tracking_dir, f"SeaPay_Tracking_{datetime.now().strftime('%Y-%m-%d')}.csv")

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "Rate", "Last", "First",
            "Ship", "Start", "End", "Days",
            "InvalidCount", "DuplicateCount",
            "Status", "GeneratedAt"
        ])

        generated_at = datetime.utcnow().isoformat() + "Z"

        def fmt(d):
            return d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)

        for key, sd in summary_data.items():
            periods = sd.get("periods", [])
            skipped_unknown = sd.get("skipped_unknown", [])
            skipped_dupe = sd.get("skipped_dupe", [])

            invalid_count = len(skipped_unknown)
            dupe_count = len(skipped_dupe)

            status = "VALID" if not (invalid_count or dupe_count) else "WITH_DISCREPANCIES"

            if periods:
                for p in periods:
                    writer.writerow([
                        sd.get("rate", ""),
                        sd.get("last", ""),
                        sd.get("first", ""),
                        p["ship"],
                        fmt(p["start"]),
                        fmt(p["end"]),
                        p["days"],
                        invalid_count,
                        dupe_count,
                        status,
                        generated_at
                    ])
            else:
                writer.writerow([
                    sd.get("rate", ""),
                    sd.get("last", ""),
                    sd.get("first", ""),
                    "",
                    "",
                    "",
                    0,
                    invalid_count,
                    dupe_count,
                    status,
                    generated_at
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

    for file in files:
        path = os.path.join(DATA_DIR, file)
        log(f"OCR → {file}")

        raw = strip_times(ocr_pdf(path))

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

        marked_dir = os.path.join(OUTPUT_DIR, "marked_sheets")
        os.makedirs(marked_dir, exist_ok=True)
        marked_path = os.path.join(marked_dir, f"MARKED_{os.path.splitext(file)[0]}.pdf")

        mark_sheet_with_strikeouts(
            path, skipped_dupe, skipped_unknown, marked_path,
            total_days, strike_color=strike_color
        )

        ship_periods = {}
        for g in groups:
            ship_periods.setdefault(g["ship"], []).append(g)

        for ship, periods in ship_periods.items():
            make_pdf_for_ship(ship, periods, name)

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
    write_summary_files(summary_data)

    write_validation_reports(summary_data)
    log("VALIDATION REPORTS UPDATED")

    write_json_tracker(summary_data)
    write_csv_tracker(summary_data)
    log("TRACKING FILES UPDATED")

    log("✅ ALL OPERATIONS COMPLETE")
