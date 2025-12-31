import os
import re
import json
import shutil  # 🔹 PATCH: Add shutil import
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


# 🔹 =====================================================
# 🔹 PATCH: GRANULAR PROGRESS HELPER
# 🔹 =====================================================
class ProgressTracker:
    """
    Helper class to manage smooth, granular progress updates.
    Divides 100% progress into phases and sub-steps.
    """
    def __init__(self, total_files):
        self.total_files = max(total_files, 1)
        self.current_file = 0
        
        # Phase allocation (must sum to 100%)
        self.PHASE_FILE_PROCESSING = 85  # 85% for all file processing
        self.PHASE_SUMMARY = 5           # 5% for summary generation
        self.PHASE_MERGE = 10            # 10% for merging outputs
        
        # Sub-steps within each file (must sum to 100%)
        self.STEP_OCR = 20               # 20% OCR
        self.STEP_PARSE = 15             # 15% Parsing
        self.STEP_VALIDATION = 15        # 15% Validation
        self.STEP_REVIEW_STATE = 10      # 10% Building review state
        self.STEP_TORIS = 20             # 20% TORIS marking
        self.STEP_PG13 = 20              # 20% PG-13 generation
        
    def get_file_base_progress(self, file_index):
        """Get the starting progress % for a given file (0-indexed)"""
        return int((file_index / self.total_files) * self.PHASE_FILE_PROCESSING)
    
    def get_file_progress_range(self):
        """Get how much % each file is worth"""
        return self.PHASE_FILE_PROCESSING / self.total_files
    
    def update(self, file_index, sub_step_percent, step_name):
        """
        Update progress with granular sub-step tracking.
        
        Args:
            file_index: Current file index (0-indexed)
            sub_step_percent: Progress within current file (0-100)
            step_name: Description of current step
        """
        base = self.get_file_base_progress(file_index)
        file_range = self.get_file_progress_range()
        within_file = (sub_step_percent / 100.0) * file_range
        total = int(base + within_file)
        
        # Clamp to valid range
        total = max(0, min(total, 100))
        
        set_progress(
            status="PROCESSING",
            percent=total,
            current_step=step_name
        )
    
    def phase_summary(self):
        """Update progress for summary phase"""
        percent = self.PHASE_FILE_PROCESSING + int(self.PHASE_SUMMARY * 0.5)
        set_progress(
            status="PROCESSING",
            percent=percent,
            current_step="Writing summary files"
        )
    
    def phase_merge(self):
        """Update progress for merge phase"""
        percent = self.PHASE_FILE_PROCESSING + self.PHASE_SUMMARY
        set_progress(
            status="PROCESSING",
            percent=percent,
            current_step="Merging output package"
        )
    
    def complete(self):
        """Mark as 100% complete"""
        set_progress(
            status="COMPLETE",
            percent=100,
            current_step="Complete"
        )


# ---------------------------------------------------------
# HELPERS
# ---------------------------------------------------------
def extract_reporting_period(text, filename: str = ""):
    """
    Try to pull the "From: ... To: ..." reporting period from the OCR text.
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


# PATCH: Extract event details from raw text
def extract_event_details(raw_text):
    """
    Extract event details (everything in parentheses) from raw text.
    
    Examples:
      "CHAFEE (ASW M-1*1)" -> "(ASW M-1*1)"
      "PAUL HAMILTON (ASW T-2)" -> "(ASW T-2)"
      "ATGSD (ASW MITE AUG 2025)" -> "(ASW MITE AUG 2025)"
    
    Returns event string or empty string if no parentheses found.
    """
    match = re.search(r'\(([^)]+)\)', raw_text)
    return f"({match.group(1)})" if match else ""


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
    Top-level processor with granular progress updates.

    - OCR each SEA DUTY CERTIFICATION SHEET
    - Parse rows (SBTT/MITE suppression, mission priority, duplicates)
    - Group into sea pay periods and compute totals
    - Generate PG-13 PDFs (unchanged)
    - Mark TORIS sheets with strikeouts (unchanged)
    - Write summary TXT/PDF + tracker (unchanged)
    - Merge package (unchanged)
    - Build rich JSON review_state (Phase 3)
    - Apply per-member overrides (Phase 4 Option A)
    
    🔹 PATCH: Now includes granular progress reporting at 1-2% increments
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
    
    # 🔹 PATCH: Initialize granular progress tracker
    progress = ProgressTracker(total_files)
    
    set_progress(
        status="PROCESSING",
        percent=0,
        current_step="Initializing",
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
    for idx, file in enumerate(sorted(files)):
        path = os.path.join(DATA_DIR, file)

        # 🔹 PATCH: OCR step (0% of this file)
        progress.update(idx, 0, f"[{idx+1}/{total_files}] OCR: {file}")
        log(f"OCR → {file}")

        # 1. OCR and basic text cleanup
        raw = strip_times(ocr_pdf(path))
        
        # 🔹 PATCH: OCR complete (20% of this file)
        progress.update(idx, progress.STEP_OCR, f"[{idx+1}/{total_files}] OCR complete: {file}")
        
        sheet_start, sheet_end, _ = extract_reporting_period(raw, file)

        # 2. Member name detection
        try:
            name = extract_member_name(raw)
            log(f"NAME → {name}")
        except Exception as e:
            log(f"NAME ERROR → {e}")
            continue

        # 🔹 PATCH: Parse step (35% of this file)
        progress.update(idx, progress.STEP_OCR + progress.STEP_PARSE, 
                       f"[{idx+1}/{total_files}] Parsing: {file}")

        # 3. Parse rows (TORIS logic, including SBTT/MITE suppression)
        year = extract_year_from_filename(file)
        rows, skipped_dupe, skipped_unknown = parse_rows(raw, year)

        # 🔹 PATCH: Validation step (50% of this file)
        progress.update(idx, progress.STEP_OCR + progress.STEP_PARSE + progress.STEP_VALIDATION,
                       f"[{idx+1}/{total_files}] Validating: {file}")

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

        # 🔹 PATCH: Building review state (60% of this file)
        progress.update(idx, progress.STEP_OCR + progress.STEP_PARSE + 
                       progress.STEP_VALIDATION + progress.STEP_REVIEW_STATE,
                       f"[{idx+1}/{total_files}] Building review: {file}")

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
        }

        # Build rows
        for r in rows:
            row_obj = {
                "date": r["date"].strftime("%m/%d/%Y"),
                "ship": r["ship"],
                "event": r.get("event", ""),
                "raw": r.get("raw", ""),
                "occ_idx": r.get("occ_idx"),
                "event_index": r.get("event_index"),
                "reason": r.get("reason", ""),
                "final_classification": {
                    "is_valid": True,
                    "source": "parser",
                    "reason": "Valid row from parser",
                },
            }
            sheet_block["rows"].append(row_obj)

        # Build invalid_events
        for sk in skipped_dupe:
            inv = {
                "date": sk.get("date"),
                "ship": sk.get("ship"),
                "event": sk.get("event", ""),
                "raw": sk.get("raw", ""),
                "occ_idx": sk.get("occ_idx"),
                "event_index": sk.get("event_index"),
                "reason": sk.get("reason", "Duplicate event"),
                "category": "duplicate",
                "final_classification": {
                    "is_valid": False,
                    "source": "parser",
                    "reason": sk.get("reason", "Duplicate event"),
                },
            }
            sheet_block["invalid_events"].append(inv)

        for sk in skipped_unknown:
            inv = {
                "date": sk.get("date"),
                "ship": sk.get("ship"),
                "event": sk.get("event", ""),
                "raw": sk.get("raw", ""),
                "occ_idx": sk.get("occ_idx"),
                "event_index": sk.get("event_index"),
                "reason": sk.get("reason", "Unknown/filtered event"),
                "category": "unknown",
                "final_classification": {
                    "is_valid": False,
                    "source": "parser",
                    "reason": sk.get("reason", "Unknown/filtered event"),
                },
            }
            sheet_block["invalid_events"].append(inv)

        review_state[member_key]["sheets"].append(sheet_block)

        # ------------------------------------------
        # SUMMARY DATA
        # ------------------------------------------
        if member_key not in summary_data:
            summary_data[member_key] = {
                "rate": rate,
                "last": last,
                "first": first,
                "periods": [],
                "skipped_dupe": [],
                "skipped_unknown": [],
                "reporting_periods": [],
            }

        for g in groups:
            summary_data[member_key]["periods"].append({
                "ship": g["ship"],
                "start": g["start"],
                "end": g["end"],
                "days": (g["end"] - g["start"]).days + 1,
                "sheet_file": file,
            })

        for d in skipped_dupe:
            summary_data[member_key]["skipped_dupe"].append({
                "date": d.get("date"),
                "ship": d.get("ship"),
                "occ_idx": d.get("occ_idx"),
                "raw": d.get("raw", ""),
                "reason": d.get("reason", ""),
                "category": "duplicate",
            })

        for u in skipped_unknown:
            summary_data[member_key]["skipped_unknown"].append({
                "date": u.get("date"),
                "ship": u.get("ship"),
                "occ_idx": u.get("occ_idx"),
                "raw": u.get("raw", ""),
                "reason": u.get("reason", ""),
                "category": "unknown",
            })

        if sheet_start and sheet_end:
            summary_data[member_key]["reporting_periods"].append({
                "from": sheet_start.strftime("%m/%d/%Y"),
                "to": sheet_end.strftime("%m/%d/%Y"),
                "sheet": file,
            })

        # 🔹 PATCH: TORIS marking (80% of this file)
        progress.update(idx, progress.STEP_OCR + progress.STEP_PARSE + 
                       progress.STEP_VALIDATION + progress.STEP_REVIEW_STATE + progress.STEP_TORIS,
                       f"[{idx+1}/{total_files}] Marking TORIS: {file}")

        # TORIS with strikeouts
        toris_name = f"{rate}_{last}_{first}__TORIS_SEA_DUTY_CERT_SHEETS.pdf".replace(" ", "_")
        toris_path = os.path.join(TORIS_CERT_FOLDER, toris_name)

        if os.path.exists(toris_path):
            os.remove(toris_path)

        extracted_total_days = None
        computed_total_days = total_days

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

        # 🔹 PATCH: PG-13 generation (90-100% of this file)
        pg13_base_progress = (progress.STEP_OCR + progress.STEP_PARSE + 
                             progress.STEP_VALIDATION + progress.STEP_REVIEW_STATE + 
                             progress.STEP_TORIS)
        
        # PG-13 per ship
        ship_map = {}
        for g in groups:
            ship_map.setdefault(g["ship"], []).append(g)

        ship_count = len(ship_map)
        for ship_idx, (ship, ship_periods) in enumerate(ship_map.items(), start=1):
            # Update progress within PG-13 step
            pg13_progress = pg13_base_progress + (progress.STEP_PG13 * (ship_idx / max(ship_count, 1)))
            progress.update(idx, pg13_progress, 
                          f"[{idx+1}/{total_files}] PG-13 {ship_idx}/{ship_count}: {ship}")
            
            make_pdf_for_ship(ship, ship_periods, name)
            add_progress_detail("pg13_created", 1)
            pg13_total += 1

        add_progress_detail("files_processed", 1)
        files_processed_total += 1
        
        # 🔹 PATCH: File complete (100% of this file)
        progress.update(idx, 100, f"[{idx+1}/{total_files}] Complete: {file}")

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

    # 🔹 PATCH: Summary phase
    progress.phase_summary()
    log("Writing summary files...")
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
        
        # 🔹 PATCH: Create original backup immediately after writing
        original_path = REVIEW_JSON_PATH.replace('.json', '_ORIGINAL.json')
        shutil.copy(REVIEW_JSON_PATH, original_path)
        log(f"ORIGINAL REVIEW BACKUP CREATED → {original_path}")
        
    except Exception as e:
        log(f"REVIEW JSON ERROR → {e}")

    # ----------------------------------------------------
    # MERGE OUTPUT PACKAGE (unchanged)
    # ----------------------------------------------------
    # 🔹 PATCH: Merge phase
    progress.phase_merge()
    log("Merging output package...")
    merge_all_pdfs()

    log("PROCESS COMPLETE")
    # 🔹 PATCH: Complete with granular tracker
    progress.complete()


# =========================================================
# REBUILD OUTPUTS FROM REVIEW JSON (NO OCR / NO PARSING)
# =========================================================
def rebuild_outputs_from_review():
    """
    Rebuild PG-13, TORIS, summaries, and merged package
    strictly from REVIEW_JSON_PATH.
    
    PATCH: Properly handles force-valid overrides
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
    total_members = len(review_state)
    for member_idx, (member_key, member_data) in enumerate(review_state.items(), start=1):
        # 🔹 PATCH: Update progress per member
        member_progress = int((member_idx / max(total_members, 1)) * 85)  # 85% for processing
        set_progress(
            percent=member_progress,
            current_step=f"Rebuilding [{member_idx}/{total_members}]: {member_key}"
        )
        
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
            
            # PATCH: Collect ALL valid rows (includes force-valid from overrides)
            # The rows array already contains moved force-valid events from overrides.py
            for r in sheet.get("rows", []):
                if r.get("final_classification", {}).get("is_valid"):
                    final_valid_rows.append(r)
            
            # Only keep invalid events (force-valid ones already moved to rows)
            for e in sheet.get("invalid_events", []):
                if not e.get("final_classification", {}).get("is_valid"):
                    invalid_entry = {
                        "date": e.get("date"),
                        "ship": e.get("ship"),
                        "occ_idx": e.get("occ_idx"),
                        "raw": e.get("raw", ""),
                        "reason": e.get("reason", ""),
                        "category": e.get("category", ""),
                    }
                    final_invalid_events.append(invalid_entry)
                    
                    # PATCH: Populate summary data invalid lists for summary txt/pdf
                    category = e.get("category", "")
                    if category == "duplicate" or "duplicate" in e.get("reason", "").lower():
                        summary_data[member_key]["skipped_dupe"].append(invalid_entry)
                    else:
                        summary_data[member_key]["skipped_unknown"].append(invalid_entry)

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
                override_valid_rows=final_valid_rows,
            )

            toris_total += 1

    # =============================
    # FINALIZE (ONCE)
    # =============================
    # 🔹 PATCH: Update progress for final steps
    set_progress(percent=90, current_step="Writing summary files")
    write_summary_files(summary_data)
    
    set_progress(percent=95, current_step="Merging PDFs")
    merge_all_pdfs()

    set_progress(
        status="COMPLETE",
        percent=100,
        current_step="Rebuild complete",
        details={
            "pg13_created": pg13_total,
            "toris_marked": toris_total,
        },
    )

    log("REBUILD OUTPUTS COMPLETE")
