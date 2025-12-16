import os
import re
import json
from datetime import datetime

from app.core.logger import (
    log,
    reset_progress,
    set_progress,
    add_progress_detail,
)
from app.core.config import (
    DATA_DIR,
    SEA_PAY_PG13_FOLDER,
    TORIS_CERT_FOLDER,
    REVIEW_JSON_PATH,  # JSON review output path
)
from app.core.ocr import (
    ocr_pdf,
    strip_times,
    extract_member_name,
)
from app.core.parser import (
    parse_rows,
    extract_year_from_filename,
    group_by_ship,
)
from app.core.pdf_writer import make_pdf_for_ship
from app.core.strikeout import mark_sheet_with_strikeouts
from app.core.summary import write_summary_files
from app.core.merge import merge_all_pdfs
from app.core.rates import resolve_identity

# Phase 4 – manual overrides (Option A)
from app.core.overrides import apply_overrides


# ---------------------------------------------------------
# HELPERS
# ---------------------------------------------------------
def extract_reporting_period(text, filename: str = ""):
    """
    Try to pull the “From: ... To: ...” reporting period from the OCR text.
    Fall back to a date range in the filename if needed.
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
    """Clear existing PG-13 outputs at the start of a run."""
    try:
        if not os.path.isdir(SEA_PAY_PG13_FOLDER):
            os.makedirs(SEA_PAY_PG13_FOLDER, exist_ok=True)
        for f in os.listdir(SEA_PAY_PG13_FOLDER):
            fp = os.path.join(SEA_PAY_PG13_FOLDER, f)
            if os.path.isfile(fp):
                os.remove(fp)
    except Exception as e:
        log(f"PG13 CLEAR ERROR → {e}")


# ---------------------------------------------------------
# MAIN PROCESSOR
# ---------------------------------------------------------
def process_all(strike_color: str = "black"):
    """
    Top-level processor:

    - OCR each SEA DUTY CERTIFICATION SHEET
    - Parse rows (SBTT/MITE suppression, mission priority, duplicates)
    - Group into sea pay periods and compute totals
    - Generate PG-13 PDFs (unchanged)
    - Mark TORIS sheets with strikeouts (unchanged)
    - Write summary TXT/PDF + tracker (unchanged)
    - Merge package (unchanged)
    - Build rich JSON review_state (Phase 3)
    - Apply per-member overrides (Phase 4 Option A)
    """

    # Ensure key output dirs exist
    os.makedirs(SEA_PAY_PG13_FOLDER, exist_ok=True)
    os.makedirs(TORIS_CERT_FOLDER, exist_ok=True)

    clear_pg13_folder()
    reset_progress()

    files = [f for f in os.listdir(DATA_DIR) if f.lower().endswith(".pdf")]
    if not files:
        log("NO INPUT FILES FOUND")
        set_progress(
            status="COMPLETE",
            percent=100,
        )
        return

    total_files = len(files)
    set_progress(
        status="PROCESSING",
        percent=0,
        details={
            "files_processed": 0,
            "valid_days": 0,
            "invalid_events": 0,
            "pg13_created": 0,
            "toris_marked": 0,
        },
    )

    log("=== PROCESS STARTED ===")

    # For summary / tracker / merged PDFs
    summary_data = {}

    # Phase 3: review JSON state (per member → sheets → rows)
    review_state = {}

    # Totals for dashboard / progress
    files_processed_total = 0
    valid_days_total = 0
    invalid_events_total = 0
    pg13_total = 0
    toris_total = 0

    # --------------------------------------------------
    # PROCESS EACH INPUT PDF
    # --------------------------------------------------
    for idx, file in enumerate(sorted(files), start=1):
        path = os.path.join(DATA_DIR, file)

        set_progress(
            current_step=f"OCR and parse: {file}",
            percent=int(((idx - 1) / max(total_files, 1)) * 100),
        )
        log(f"OCR → {file}")

        # 1. OCR and basic text cleanup
        raw = strip_times(ocr_pdf(path))
        sheet_start, sheet_end, _ = extract_reporting_period(raw, file)

        # 2. Member name detection
        try:
            name = extract_member_name(raw)
            log(f"NAME → {name}")
        except Exception as e:
            log(f"NAME ERROR → {e}")
            continue

        # 3. Parse rows (TORIS logic, including SBTT/MITE suppression)
        year = extract_year_from_filename(file)
        rows, skipped_dupe, skipped_unknown = parse_rows(raw, year)

        # 4. Group by ship & compute total sea pay days (unchanged behavior)
        groups = group_by_ship(rows)
        total_days = sum((g["end"] - g["start"]).days + 1 for g in groups)

        # Totals
        valid_days_total += total_days
        invalid_events_total += len(skipped_dupe) + len(skipped_unknown)
        add_progress_detail("valid_days", total_days)
        add_progress_detail("invalid_events", len(skipped_dupe) + len(skipped_unknown))

        # 5. Resolve identity as before
        rate, last, first = resolve_identity(name)
        member_key = f"{rate} {last},{first}"

        # ------------------------------------------
        # BUILD / UPDATE REVIEW STATE (Phase 3)
        # ------------------------------------------
        if member_key not in review_state:
            review_state[member_key] = {
                "rate": rate,
                "last": last,
                "first": first,
                "sheets": [],
            }

        sheet_block = {
            "source_file": file,
            "reporting_period": {
                "from": sheet_start.strftime("%m/%d/%Y") if sheet_start else None,
                "to": sheet_end.strftime("%m/%d/%Y") if sheet_end else None,
            },
            "member_name_raw": name,
            "total_valid_days": total_days,
            "stats": {
                "total_rows": len(rows),
                "skipped_dupe_count": len(skipped_dupe),
                "skipped_unknown_count": len(skipped_unknown),
            },
            "rows": [],
            "invalid_events": [],
            "parsing_warnings": [],
            "parse_confidence": 1.0,
        }

        # -------------------------
        # CLASSIFY VALID ROWS
        # -------------------------
        for r in rows:
            system_classification = {
                "is_valid": True,
                "reason": None,
                "explanation": (
                    "Valid sea pay day after TORIS parser filtering "
                    "(non-training, non-duplicate, known ship)."
                ),
                "confidence": 1.0,
            }

            override = {
                "status": None,   # "valid" | "invalid" | None
                "reason": None,
                "source": None,   # "manual" | "admin" | None
                "history": [],
            }

            final_classification = {
                "is_valid": system_classification["is_valid"],
                "reason": system_classification["reason"],
                "source": "system",
            }

            sheet_block["rows"].append(
                {
                    "date": r.get("date"),
                    "ship": r.get("ship"),
                    "occ_idx": r.get("occ_idx"),
                    "raw": r.get("raw", ""),
                    "is_inport": bool(r.get("is_inport", False)),
                    "inport_label": r.get("inport_label"),
                    "is_mission": r.get("is_mission"),
                    "label": r.get("label"),
                    # Backwards-compatible simple flags
                    "status": "valid",
                    "status_reason": None,
                    "confidence": 1.0,
                    # Structured classifications
                    "system_classification": system_classification,
                    "override": override,
                    "final_classification": final_classification,
                }
            )

        # -------------------------
        # CLASSIFY INVALID EVENTS
        # -------------------------
        invalid_events = []

        # Duplicates
        for e in skipped_dupe:
            system_classification = {
                "is_valid": False,
                "reason": "duplicate",
                "explanation": (
                    "Duplicate event for this date; another entry kept "
                    "as primary sea pay event."
                ),
                "confidence": 1.0,
            }
            override = {
                "status": None,
                "reason": None,
                "source": None,
                "history": [],
            }
            final_classification = {
                "is_valid": False,
                "reason": "duplicate",
                "source": "system",
            }

            invalid_events.append(
                {
                    "date": e.get("date"),
                    "ship": e.get("ship"),
                    "raw": e.get("raw", ""),
                    "reason": e.get("reason", "Duplicate"),
                    "category": "duplicate",
                    "source": "parser",
                    "system_classification": system_classification,
                    "override": override,
                    "final_classification": final_classification,
                }
            )

        # Unknown / shore-side / suppressed
        for e in skipped_unknown:
            raw_reason = (e.get("reason") or "").lower()
            if "in-port" in raw_reason or "shore" in raw_reason:
                category = "shore_side_event"
                explanation = "In-port shore-side training or non-sea-pay event."
            else:
                category = "unknown"
                explanation = (
                    "Unknown or non-platform event; no valid ship identified "
                    "for sea pay."
                )

            system_classification = {
                "is_valid": False,
                "reason": category,
                "explanation": explanation,
                "confidence": 1.0,
            }
            override = {
                "status": None,
                "reason": None,
                "source": None,
                "history": [],
            }
            final_classification = {
                "is_valid": False,
                "reason": category,
                "source": "system",
            }

            invalid_events.append(
                {
                    "date": e.get("date"),
                    "ship": e.get("ship"),
                    "raw": e.get("raw", ""),
                    "reason": e.get("reason", "Unknown"),
                    "category": category,
                    "source": "parser",
                    "system_classification": system_classification,
                    "override": override,
                    "final_classification": final_classification,
                }
            )

        sheet_block["invalid_events"] = invalid_events

        # -------------------------
        # PARSE CONFIDENCE HEURISTICS
        # -------------------------
        if len(skipped_unknown) > 0:
            sheet_block["parse_confidence"] = 0.7
            sheet_block["parsing_warnings"].append(
                f"{len(skipped_unknown)} unknown/suppressed entries detected."
            )
        if len(rows) == 0 and invalid_events:
            sheet_block["parse_confidence"] = 0.4
            sheet_block["parsing_warnings"].append(
                "Sheet had no valid rows after parser filtering."
            )

        review_state[member_key]["sheets"].append(sheet_block)

        # ----------------------------------
        # SUMMARY / PG-13 / TORIS (unchanged behavior)
        # ----------------------------------
        if member_key not in summary_data:
            summary_data[member_key] = {
                "rate": rate,
                "last": last,
                "first": first,
                "periods": [],
                "skipped_unknown": [],
                "skipped_dupe": [],
                "reporting_periods": [],
            }

        sd = summary_data[member_key]
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

        # TORIS file naming
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

        # PG-13 per ship
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
            percent=int((idx / max(total_files, 1)) * 100),
        )

# -------------------------------
# FINAL TOTALS AND SUMMARY FILES
# -------------------------------
    final_details = {
        "files_processed": files_processed_total,
        "valid_days": valid_days_total,
        "invalid_events": invalid_events_total,
        "pg13_created": pg13_total,
        "toris_marked": toris_total,
    }
    set_progress(details=final_details)

    set_progress(current_step="Writing summary files")
    write_summary_files(summary_data)

# ----------------------------------------------------
# APPLY OVERRIDES (Phase 4 – Option A)
# ----------------------------------------------------
    final_review_state = {}
    for member_key, member_data in review_state.items():
        final_review_state[member_key] = apply_overrides(member_key, member_data)

# ----------------------------------------------------
# WRITE JSON REVIEW STATE (MUST HAPPEN BEFORE MERGE)
# ----------------------------------------------------
    try:
        os.makedirs(os.path.dirname(REVIEW_JSON_PATH), exist_ok=True)
        with open(REVIEW_JSON_PATH, "w", encoding="utf-8") as f:
            json.dump(final_review_state, f, indent=2, default=str)
        log(f"REVIEW JSON WRITTEN → {REVIEW_JSON_PATH}")
    except Exception as e:
        log(f"REVIEW JSON ERROR → {e}")

# ----------------------------------------------------
# MERGE OUTPUT PACKAGE (unchanged)
# ----------------------------------------------------
    set_progress(current_step="Merging output package", percent=100)
    merge_all_pdfs()

    log("PROCESS COMPLETE")
    set_progress(status="COMPLETE", percent=100)
# =========================================================
# REBUILD OUTPUTS FROM REVIEW JSON (NO OCR / NO PARSING)
# =========================================================
# ===================== PATCHED REBUILD ONLY =====================
def rebuild_outputs_from_review():
    """
    Rebuild PG-13, TORIS, summaries, and merged package
    strictly from REVIEW_JSON_PATH.
    """

    if not os.path.exists(REVIEW_JSON_PATH):
        log("REBUILD ERROR → REVIEW JSON NOT FOUND")
        return

    with open(REVIEW_JSON_PATH, "r", encoding="utf-8") as f:
        review_state = json.load(f)

    set_progress(status="PROCESSING", percent=0, current_step="Rebuilding outputs")

    os.makedirs(SEA_PAY_PG13_FOLDER, exist_ok=True)
    os.makedirs(TORIS_CERT_FOLDER, exist_ok=True)

    summary_data = {}
    pg13_total = 0
    toris_total = 0

    # =============================
    # LOOP MEMBERS
    # =============================
    for member_key, member_data in review_state.items():
        rate = member_data["rate"]
        last = member_data["last"]
        first = member_data["first"]
        name = f"{first} {last}"

        summary_data[member_key] = {
            "rate": rate,
            "last": last,
            "first": first,
            "periods": [],
            "skipped_unknown": [],
            "skipped_dupe": [],
            "reporting_periods": [],
        }

        # =============================
        # LOOP SHEETS
        # =============================
        for sheet in member_data.get("sheets", []):
            src_file = os.path.join(DATA_DIR, sheet["source_file"])

            final_valid_rows = []
            final_invalid_events = []
            
            # Build a set of valid (date, occ_idx) after overrides
            valid_keys = set()
            
            for r in sheet.get("rows", []):
                if r.get("final_classification", {}).get("is_valid"):
                    final_valid_rows.append(r)
                    if r.get("date") and r.get("occ_idx"):
                        valid_keys.add((r["date"], r["occ_idx"]))
            
            # Only keep invalid events that were NOT overridden to valid
            for e in sheet.get("invalid_events", []):
                key = (e.get("date"), e.get("occ_idx"))
                if key in valid_keys:
                    log(
                        f"REBUILD SKIP INVALID (OVERRIDDEN VALID) → "
                        f"{e.get('date')} OCC#{e.get('occ_idx')}"
                    )
                    continue
            
                final_invalid_events.append({
                    "date": e.get("date"),
                    "ship": e.get("ship"),
                    "occ_idx": e.get("occ_idx"),
                    "reason": e.get("final_classification", {}).get("reason"),
                })


            # =============================
            # REBUILD PERIODS (FINAL VALID)
            # =============================
            ship_map = {}
            for r in final_valid_rows:
                ship_map.setdefault(r["ship"], []).append(r)

            for ship, ship_rows in ship_map.items():
                periods = group_by_ship(ship_rows)

                for g in periods:
                    summary_data[member_key]["periods"].append({
                        "ship": ship,
                        "start": g["start"],
                        "end": g["end"],
                        "days": (g["end"] - g["start"]).days + 1,
                        "sheet_file": src_file,
                    })

                make_pdf_for_ship(ship, periods, name)
                pg13_total += 1

            # =============================
            # TORIS REBUILD (FINAL INVALID)
            # =============================
            toris_name = f"{rate}_{last}_{first}__TORIS_SEA_DUTY_CERT_SHEETS.pdf".replace(" ", "_")
            toris_path = os.path.join(TORIS_CERT_FOLDER, toris_name)

            if os.path.exists(toris_path):
                os.remove(toris_path)

            computed_days = sum(p["days"] for p in summary_data[member_key]["periods"])

            mark_sheet_with_strikeouts(
                src_file,
                [],
                final_invalid_events,
                toris_path,
                None,
                computed_days,
                override_valid_rows=final_valid_rows,  # ← this is the fix
            )

            toris_total += 1

    # =============================
    # FINALIZE (ONCE)
    # =============================
    write_summary_files(summary_data)
    merge_all_pdfs()

    set_progress(
        status="COMPLETE",
        percent=100,
        details={
            "pg13_created": pg13_total,
            "toris_marked": toris_total,
        },
    )

    log("REBUILD OUTPUTS COMPLETE")




