import os
import re
import json
import shutil
from datetime import datetime
import sys

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
    REVIEW_JSON_PATH,
    PACKAGE_FOLDER,
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
from app.core.overrides import apply_overrides


# 🔹 PATCH: Cancel check helper - uses sys.modules to avoid circular import
def is_cancelled():
    """Check if processing has been cancelled"""
    try:
        # Access routes module from sys.modules after it's already imported
        routes = sys.modules.get('app.routes')
        if routes:
            return getattr(routes, 'processing_cancelled', False)
        return False
    except:
        return False


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
def process_all(strike_color: str = "black", consolidate_pg13: bool = False):
    """
    Top-level processor with granular progress updates.

    Args:
        strike_color: Color for invalid entry strikeouts ("black" or "red")
        consolidate_pg13: If True, creates one PG-13 per ship with all periods.
                         If False, creates separate PG-13 for each period (default)

    - OCR each SEA DUTY CERTIFICATION SHEET
    - Parse rows (SBTT/MITE suppression, mission priority, duplicates)
    - Group into sea pay periods and compute totals
    - Generate PG-13 PDFs (with optional consolidation)
    - Mark TORIS sheets with strikeouts
    - Write summary TXT/PDF + tracker
    - Merge package
    - Build rich JSON review_state (Phase 3)
    - Apply per-member overrides (Phase 4 Option A)
    
    🔹 PATCH: Now includes granular progress reporting at 1-2% increments
    🔹 NEW: Optional PG-13 consolidation to save paper
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
        # 🔹 PATCH: Check for cancellation at start of each file
        if is_cancelled():
            log("❌ PROCESSING CANCELLED BY USER")
            set_progress(status="CANCELLED", percent=0, current_step="Cancelled by user")
            return
            
        path = os.path.join(DATA_DIR, file)

        # 🔹 PATCH: OCR step (0% of this file)
        progress.update(idx, 0, f"[{idx+1}/{total_files}] OCR: {file}")
        log(f"OCR → {file}")

        # 1. OCR and basic text cleanup
        raw = strip_times(ocr_pdf(path))
        
        # 🔹 PATCH: OCR complete (20% of this file)
        progress.update(idx, progress.STEP_OCR, f"[{idx+1}/{total_files}] OCR complete: {file}")
        
        # 🔹 PATCH: Check for cancellation after OCR
        if is_cancelled():
            log("❌ PROCESSING CANCELLED BY USER")
            set_progress(status="CANCELLED", percent=0, current_step="Cancelled by user")
            return
        
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

        # 🔹 PATCH: Check for cancellation after parsing
        if is_cancelled():
            log("❌ PROCESSING CANCELLED BY USER")
            set_progress(status="CANCELLED", percent=0, current_step="Cancelled by user")
            return

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
            "parse_confidence": 1.0,
        }

        # 🔹 --- START OF PATCH --- 🔹

        # CLASSIFY VALID ROWS: Add a permanent, positive event_index to every valid row.
        for valid_idx, r in enumerate(rows):
            system_classification = {
                "is_valid": True,
                "reason": None,
                "explanation": "Valid sea pay day after TORIS parser filtering (non-training, non-duplicate, known ship).",
                "confidence": 1.0,
            }
            override = { "status": None, "reason": None, "source": None, "history": [] }
            final_classification = { "is_valid": True, "reason": None, "source": "system" }
            
            sheet_block["rows"].append({
                "event_index": valid_idx,  # Stamp permanent positive index
                "date": r.get("date"),
                "ship": r.get("ship"),
                "event": extract_event_details(r.get("raw", "")),
                "occ_idx": r.get("occ_idx"),
                "raw": r.get("raw", ""),
                "is_inport": bool(r.get("is_inport", False)),
                "inport_label": r.get("inport_label"),
                "is_mission": r.get("is_mission"),
                "label": r.get("label"),
                "status": "valid",
                "status_reason": None,
                "confidence": 1.0,
                "system_classification": system_classification,
                "override": override,
                "final_classification": final_classification,
            })

        # CLASSIFY INVALID EVENTS: Add a permanent, negative event_index to every invalid event.
        invalid_events = []
        all_invalid_source = skipped_dupe + skipped_unknown
        
        for invalid_idx, e in enumerate(all_invalid_source):
            event_index = -(invalid_idx + 1)  # Stamp permanent negative index
            
            # This logic is a consolidation of your original two separate loops
            is_dupe = e in skipped_dupe
            
            if is_dupe:
                category = "duplicate"
                explanation = "Duplicate event for this date; another entry kept as primary sea pay event."
            else:
                raw_reason = (e.get("reason") or "").lower()
                if "in-port" in raw_reason or "shore" in raw_reason:
                    category = "shore_side_event"
                    explanation = "In-port shore-side training or non-sea-pay event."
                else:
                    category = "unknown"
                    explanation = "Unknown or non-platform event; no valid ship identified for sea pay."

            system_classification = { "is_valid": False, "reason": category, "explanation": explanation, "confidence": 1.0 }
            override = { "status": None, "reason": None, "source": None, "history": [] }
            final_classification = { "is_valid": False, "reason": category, "source": "system" }
            
            # 🔹 --- START OF CORRECTION --- 🔹
            invalid_events.append({
                "event_index": event_index,
                "status": "invalid",
                "date": e.get("date"),
                "ship": e.get("ship"),  # Corrected from e.g.get
                "event": extract_event_details(e.get("raw", "")),
                "occ_idx": e.get("occ_idx"),
                "raw": e.get("raw", ""),
                "reason": e.get("reason", "Unknown"),
                "category": category,
                "source": "parser",
                "system_classification": system_classification,
                "override": override,
                "final_classification": final_classification,
            })
            # 🔹 --- END OF CORRECTION --- 🔹
            
        sheet_block["invalid_events"] = invalid_events

        # 🔹 --- END OF PATCH --- 🔹


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

        sd = summary_data[member_key]
        sd["reporting_periods"].append(
            {"start": sheet_start, "end": sheet_end, "file": file}
        )

        for g in groups:
            sd["periods"].append({
                "ship": g["ship"],
                "start": g["start"],
                "end": g["end"],
                "days": (g["end"] - g["start"]).days + 1,
                "sheet_file": file,
            })

        sd["skipped_unknown"].extend(skipped_unknown)
        sd["skipped_dupe"].extend(skipped_dupe)

        # 🔹 PATCH: Check for cancellation before TORIS marking
        if is_cancelled():
            log("❌ PROCESSING CANCELLED BY USER")
            set_progress(status="CANCELLED", percent=0, current_step="Cancelled by user")
            return

        # 🔹 PATCH: TORIS marking (80% of this file)
        progress.update(idx, progress.STEP_OCR + progress.STEP_PARSE + 
                       progress.STEP_VALIDATION + progress.STEP_REVIEW_STATE + progress.STEP_TORIS,
                       f"[{idx+1}/{total_files}] Marking TORIS: {file}")

        # TORIS with strikeouts
        hf = sheet_start.strftime("%m-%d-%Y") if sheet_start else "UNKNOWN"
        ht = sheet_end.strftime("%m-%d-%Y") if sheet_end else "UNKNOWN"
        toris_filename = (
            f"{rate}_{last}_{first}__TORIS_SEA_DUTY_CERT_SHEETS__{hf}_TO_{ht}.pdf"
        ).replace(" ", "_")
        toris_path = os.path.join(TORIS_CERT_FOLDER, toris_filename)

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
            
            make_pdf_for_ship(ship, ship_periods, name, consolidate=consolidate_pg13)
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
    
    # 🔹 FIX: Delete old PACKAGE folder to force fresh merge
    if os.path.exists(PACKAGE_FOLDER):
        shutil.rmtree(PACKAGE_FOLDER)
        log("Deleted old PACKAGE folder for fresh merge")
    
    merge_all_pdfs()

    log("PROCESS COMPLETE")
    # 🔹 PATCH: Complete with granular tracker
    progress.complete()

# =========================================================
# REBUILD OUTPUTS FROM REVIEW JSON (NO OCR / NO PARSING)
# =========================================================
def rebuild_outputs_from_review(consolidate_pg13: bool = False):
    """
    Rebuild PG-13, TORIS, summaries, and merged package
    strictly from REVIEW_JSON_PATH.
    
    Args:
        consolidate_pg13: If True, creates one PG-13 per ship with all periods
    
    FIXED: Properly handles force-valid overrides and builds correct summary data
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
        # 🔹 PATCH: Check for cancellation during rebuild
        if is_cancelled():
            log("❌ REBUILD CANCELLED BY USER")
            set_progress(status="CANCELLED", percent=0, current_step="Cancelled by user")
            return
            
        member_progress = int((member_idx / max(total_members, 1)) * 85)
        set_progress(
            percent=member_progress,
            current_step=f"Rebuilding [{member_idx}/{total_members}]: {member_key}"
        )
        
        rate = member_data["rate"]
        last = member_data["last"]
        first = member_data["first"]
        mi = member_data.get("mi") or member_data.get("middle_initial") or ""
        name = f"{first} {last}"

        summary_data[member_key] = {
            "rate": rate,
            "last": last,
            "first": first,
            "mi": mi,
            "valid_periods": [],           # ✅ NEW: Direct list for summary
            "invalid_events": [],          # ✅ NEW: Direct list for summary
            "events_followed": [],         # ✅ NEW: Direct list for summary
            "tracker_lines": [],           # ✅ NEW: Direct list for tracker
            "reporting_periods": [],       # ✅ Preserve reporting periods
        }

        # Collect all valid rows across all sheets
        all_valid_rows = []
        all_invalid_events = []
        
        # =============================
        # LOOP SHEETS - COLLECT DATA
        # =============================
        for sheet in member_data.get("sheets", []):
            src_file = os.path.join(DATA_DIR, sheet["source_file"])
            
            # ✅ Extract reporting period if present
            if sheet.get("reporting_period"):
                summary_data[member_key]["reporting_periods"].append({
                    "start": sheet["reporting_period"].get("start"),
                    "end": sheet["reporting_period"].get("end"),
                })
            
            # ✅ VALID ROWS: All rows with is_valid=True in final_classification
            for r in sheet.get("rows", []):
                if r.get("final_classification", {}).get("is_valid"):
                    all_valid_rows.append(r)
            
            # ✅ INVALID EVENTS: All events in invalid_events array
            for e in sheet.get("invalid_events", []):
                # Get the best reason (override reason takes priority)
                override_reason = e.get("status_reason") or e.get("override", {}).get("reason")
                final_reason = override_reason if override_reason else e.get("reason", "Invalid event")
                
                invalid_entry = {
                    "date": e.get("date"),
                    "ship": e.get("ship") or "UNKNOWN",
                    "occ_idx": e.get("occ_idx"),
                    "raw": e.get("raw", ""),
                    "reason": final_reason,
                    "category": e.get("category", ""),
                }
                all_invalid_events.append(invalid_entry)

        # =============================
        # BUILD VALID PERIODS (ship continuity grouping)
        # =============================
        ship_map = {}
        for r in all_valid_rows:
            ship = r.get("ship") or "UNKNOWN"
            ship_map.setdefault(ship, []).append(r)

        valid_periods_list = []
        
        for ship, ship_rows in ship_map.items():
            # Group into continuous date ranges
            periods = group_by_ship(ship_rows)

            for g in periods:
                start_dt = g["start"]
                end_dt = g["end"]
                days = (end_dt - start_dt).days + 1
                
                valid_periods_list.append({
                    "ship": ship,
                    "start": start_dt,
                    "end": end_dt,
                    "days": days,
                })

            # ✅ Create PG-13 for this ship
            make_pdf_for_ship(ship, periods, name, consolidate=consolidate_pg13)
            pg13_total += 1

        # Sort valid periods chronologically
        valid_periods_list.sort(key=lambda p: p["start"])
        
        # =============================
        # BUILD SUMMARY DATA STRUCTURES
        # =============================
        
        # Store as tuples for summary.py to process
        summary_data[member_key]["valid_periods"] = [
            (p["ship"], p["start"], p["end"]) for p in valid_periods_list
        ]
        
        summary_data[member_key]["invalid_events"] = [
            (e["ship"], datetime.strptime(e["date"], "%m/%d/%Y"), e["reason"])
            for e in all_invalid_events if e.get("date")
        ]
        
        # ✅ Build EVENTS FOLLOWED list
        events_followed = []
        
        # Add valid periods first
        for p in valid_periods_list:
            from datetime import datetime as dt
            if isinstance(p["start"], str):
                start_dt = dt.strptime(p["start"], "%m/%d/%Y")
                end_dt = dt.strptime(p["end"], "%m/%d/%Y")
            else:
                start_dt = p["start"]
                end_dt = p["end"]
            
            days = (end_dt - start_dt).days + 1
            events_followed.append(
                f"{start_dt.month}/{start_dt.day}/{start_dt.year} TO "
                f"{end_dt.month}/{end_dt.day}/{end_dt.year} | {p['ship']} | "
                f"PAY AUTHORIZED ({days} day{'s' if days != 1 else ''})"
            )
        
        # Add invalid events
        for e in all_invalid_events:
            if e.get("date"):
                try:
                    dt_obj = datetime.strptime(e["date"], "%m/%d/%Y")
                    date_str = f"{dt_obj.month}/{dt_obj.day}/{dt_obj.year}"
                except:
                    date_str = e["date"]
                
                events_followed.append(
                    f"{date_str} | {e['ship']} | {e['reason']}"
                )
        
        summary_data[member_key]["events_followed"] = events_followed
        
        # ✅ Build TRACKER LINES
        tracker_lines = []
        
        for p in valid_periods_list:
            from datetime import datetime as dt
            if isinstance(p["start"], str):
                start_dt = dt.strptime(p["start"], "%m/%d/%Y")
                end_dt = dt.strptime(p["end"], "%m/%d/%Y")
            else:
                start_dt = p["start"]
                end_dt = p["end"]
            
            days = (end_dt - start_dt).days + 1
            tracker_lines.append(
                f"{rate} {last}, {first} | {p['ship']} | "
                f"{start_dt.month}/{start_dt.day}/{start_dt.year} TO "
                f"{end_dt.month}/{end_dt.day}/{end_dt.year} "
                f"({days} day{'s' if days != 1 else ''}) | VALID"
            )
        
        for e in all_invalid_events:
            if e.get("date"):
                try:
                    dt_obj = datetime.strptime(e["date"], "%m/%d/%Y")
                    date_str = f"{dt_obj.month}/{dt_obj.day}/{dt_obj.year}"
                except:
                    date_str = e["date"]
                
                tracker_lines.append(
                    f"{rate} {last}, {first} | {e['ship']} | "
                    f"{date_str} | {e['reason']}"
                )
        
        summary_data[member_key]["tracker_lines"] = tracker_lines

        # =============================
        # TORIS REBUILD WITH STRIKEOUTS
        # =============================
        
        # Get first sheet for TORIS generation
        first_sheet = member_data.get("sheets", [{}])[0]
        src_file = os.path.join(DATA_DIR, first_sheet.get("source_file", ""))
        
        if not os.path.exists(src_file):
            log(f"⚠️ TORIS REBUILD SKIP → Source file not found: {src_file}")
            continue
        
        toris_name = f"{rate}_{last}_{first}__TORIS_SEA_DUTY_CERT_SHEETS.pdf".replace(" ", "_")
        toris_path = os.path.join(TORIS_CERT_FOLDER, toris_name)

        if os.path.exists(toris_path):
            os.remove(toris_path)

        computed_days = sum(p["days"] for p in valid_periods_list)

        # ✅ Pass correct invalid events to strikeout
        mark_sheet_with_strikeouts(
            src_file,
            [],  # No duplicates in rebuild (already filtered)
            all_invalid_events,  # ✅ Pass complete invalid list
            toris_path,
            None,  # No extracted total in rebuild
            computed_days,
            override_valid_rows=all_valid_rows,  # ✅ Pass valid rows for override detection
        )

        toris_total += 1

    # =============================
    # FINALIZE (ONCE)
    # =============================
    set_progress(percent=90, current_step="Writing summary files")
    write_summary_files(summary_data)
    
    set_progress(percent=95, current_step="Merging PDFs")
    
    # 🔹 FIX: Delete old PACKAGE folder to force fresh merge
    if os.path.exists(PACKAGE_FOLDER):
        shutil.rmtree(PACKAGE_FOLDER)
        log("Deleted old PACKAGE folder for fresh merge")
    
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


# =============================================================================
# REBUILD SINGLE MEMBER FUNCTION
# =============================================================================

def rebuild_single_member(member_key, consolidate_pg13=False):
    """
    Rebuild outputs for a SINGLE member only.
    
    This is much faster than rebuilding everything when you just changed
    one member's overrides.
    
    Args:
        member_key: The member to rebuild (e.g., "STG1 NIVERA,RYAN")
        consolidate_pg13: If True, creates one PG-13 per ship
    
    Returns:
        dict: Status and info about what was rebuilt
    """
    
    if not os.path.exists(REVIEW_JSON_PATH):
        log(f"REBUILD SINGLE MEMBER ERROR → REVIEW JSON NOT FOUND")
        return {"status": "error", "message": "Review JSON not found"}
    
    with open(REVIEW_JSON_PATH, "r", encoding="utf-8") as f:
        review_state = json.load(f)
    
    if member_key not in review_state:
        log(f"REBUILD SINGLE MEMBER ERROR → Member not found: {member_key}")
        return {"status": "error", "message": f"Member not found: {member_key}"}
    
    member_data = review_state[member_key]
    
    log(f"=== REBUILDING SINGLE MEMBER: {member_key} ===")
    
    rate = member_data["rate"]
    last = member_data["last"]
    first = member_data["first"]
    mi = member_data.get("mi") or member_data.get("middle_initial") or ""
    
    # Create safe filename prefix
    safe_prefix = f"{rate}_{last}_{first}".replace(" ", "_").replace(",", "_")
    
    # =============================
    # 1. DELETE OLD FILES FOR THIS MEMBER
    # =============================
    log(f"  → Removing old files for {member_key}")
    
    # Delete old PG-13s
    if os.path.exists(SEA_PAY_PG13_FOLDER):
        for f in os.listdir(SEA_PAY_PG13_FOLDER):
            if f.startswith(safe_prefix):
                os.remove(os.path.join(SEA_PAY_PG13_FOLDER, f))
                log(f"    - Deleted old PG-13: {f}")
    
    # Delete old TORIS
    if os.path.exists(TORIS_CERT_FOLDER):
        for f in os.listdir(TORIS_CERT_FOLDER):
            if f.startswith(safe_prefix):
                os.remove(os.path.join(TORIS_CERT_FOLDER, f))
                log(f"    - Deleted old TORIS: {f}")
    
    # Delete old summary files
    summary_txt = os.path.join(SUMMARY_TXT_FOLDER, f"{safe_prefix}_SUMMARY.txt")
    summary_pdf = os.path.join(SUMMARY_PDF_FOLDER, f"{safe_prefix}_SUMMARY.pdf")
    if os.path.exists(summary_txt):
        os.remove(summary_txt)
        log(f"    - Deleted old summary TXT")
    if os.path.exists(summary_pdf):
        os.remove(summary_pdf)
        log(f"    - Deleted old summary PDF")
    
    # Delete old tracker
    tracker_file = os.path.join(TRACKER_FOLDER, f"{safe_prefix}_TRACKER.txt")
    if os.path.exists(tracker_file):
        os.remove(tracker_file)
        log(f"    - Deleted old tracker")
    
    # =============================
    # 2. COLLECT DATA FROM SHEETS
    # =============================
    log(f"  → Collecting data from sheets")
    
    all_valid_rows = []
    all_invalid_events = []
    
    summary_data = {
        member_key: {
            "rate": rate,
            "last": last,
            "first": first,
            "mi": mi,
            "valid_periods": [],
            "invalid_events": [],
            "events_followed": [],
            "tracker_lines": [],
            "reporting_periods": [],
        }
    }
    
    for sheet in member_data.get("sheets", []):
        # Extract reporting period if present
        if sheet.get("reporting_period"):
            summary_data[member_key]["reporting_periods"].append({
                "start": sheet["reporting_period"].get("start"),
                "end": sheet["reporting_period"].get("end"),
            })
        
        # Collect valid rows
        for row in sheet.get("rows", []):
            all_valid_rows.append(row)
        
        # Collect invalid events
        for ev in sheet.get("invalid_events", []):
            all_invalid_events.append(ev)
    
    log(f"    - Valid rows: {len(all_valid_rows)}")
    log(f"    - Invalid events: {len(all_invalid_events)}")
    
    # =============================
    # 3. REBUILD PG-13 FORMS
    # =============================
    log(f"  → Rebuilding PG-13 forms")
    
    ship_groups = group_by_ship(all_valid_rows)
    pg13_count = 0
    
    if consolidate_pg13:
        # One form per ship
        for ship, periods in ship_groups.items():
            if not periods:
                continue
            
            pg13_name = f"{safe_prefix}__PG13__{ship.replace(' ', '_')}.pdf"
            pg13_path = os.path.join(SEA_PAY_PG13_FOLDER, pg13_name)
            
            make_pdf_for_ship(
                rate=rate,
                name=f"{first} {last}",
                ship=ship,
                periods=periods,
                output_path=pg13_path,
            )
            pg13_count += 1
            log(f"    - Created consolidated PG-13: {ship}")
    else:
        # Multiple forms per ship (one per period)
        pg13_idx = 1
        for ship, periods in ship_groups.items():
            for period in periods:
                pg13_name = f"{safe_prefix}__PG13__{ship.replace(' ', '_')}__{pg13_idx:03d}.pdf"
                pg13_path = os.path.join(SEA_PAY_PG13_FOLDER, pg13_name)
                
                make_pdf_for_ship(
                    rate=rate,
                    name=f"{first} {last}",
                    ship=ship,
                    periods=[period],
                    output_path=pg13_path,
                )
                pg13_idx += 1
                pg13_count += 1
        log(f"    - Created {pg13_count} separate PG-13 forms")
    
    # =============================
    # 4. BUILD SUMMARY DATA
    # =============================
    log(f"  → Building summary data")
    
    # Build valid periods list
    valid_periods_list = []
    for row in all_valid_rows:
        valid_periods_list.append({
            "ship": row.get("ship", ""),
            "start": row.get("start_date", ""),
            "end": row.get("end_date", ""),
            "days": row.get("days", 0),
        })
    
    summary_data[member_key]["valid_periods"] = valid_periods_list
    
    # Build invalid events list
    for e in all_invalid_events:
        summary_data[member_key]["invalid_events"].append({
            "date": e.get("date", ""),
            "ship": e.get("ship", ""),
            "reason": e.get("reason", ""),
        })
    
    # Build events followed list
    events_followed = []
    for p in valid_periods_list:
        try:
            start_dt = datetime.strptime(p["start"], "%m/%d/%Y")
            end_dt = datetime.strptime(p["end"], "%m/%d/%Y")
            date_str = f"{start_dt.month}/{start_dt.day}/{start_dt.year} TO {end_dt.month}/{end_dt.day}/{end_dt.year}"
        except:
            date_str = f"{p['start']} TO {p['end']}"
        
        events_followed.append(f"{date_str} | {p['ship']} | VALID")
    
    for e in all_invalid_events:
        if e.get("date"):
            try:
                dt_obj = datetime.strptime(e["date"], "%m/%d/%Y")
                date_str = f"{dt_obj.month}/{dt_obj.day}/{dt_obj.year}"
            except:
                date_str = e["date"]
            
            events_followed.append(f"{date_str} | {e['ship']} | {e['reason']}")
    
    summary_data[member_key]["events_followed"] = events_followed
    
    # Build tracker lines
    tracker_lines = []
    for p in valid_periods_list:
        try:
            start_dt = datetime.strptime(p["start"], "%m/%d/%Y")
            end_dt = datetime.strptime(p["end"], "%m/%d/%Y")
            days = (end_dt - start_dt).days + 1
            tracker_lines.append(
                f"{rate} {last}, {first} | {p['ship']} | "
                f"{start_dt.month}/{start_dt.day}/{start_dt.year} TO "
                f"{end_dt.month}/{end_dt.day}/{end_dt.year} "
                f"({days} day{'s' if days != 1 else ''}) | VALID"
            )
        except:
            pass
    
    for e in all_invalid_events:
        if e.get("date"):
            try:
                dt_obj = datetime.strptime(e["date"], "%m/%d/%Y")
                date_str = f"{dt_obj.month}/{dt_obj.day}/{dt_obj.year}"
            except:
                date_str = e["date"]
            
            tracker_lines.append(
                f"{rate} {last}, {first} | {e['ship']} | {date_str} | {e['reason']}"
            )
    
    summary_data[member_key]["tracker_lines"] = tracker_lines
    
    # =============================
    # 5. REBUILD TORIS WITH STRIKEOUTS
    # =============================
    log(f"  → Rebuilding TORIS certification")
    
    first_sheet = member_data.get("sheets", [{}])[0]
    src_file = os.path.join(DATA_DIR, first_sheet.get("source_file", ""))
    
    if os.path.exists(src_file):
        toris_name = f"{safe_prefix}__TORIS_SEA_DUTY_CERT_SHEETS.pdf"
        toris_path = os.path.join(TORIS_CERT_FOLDER, toris_name)
        
        computed_days = sum(p["days"] for p in valid_periods_list)
        
        mark_sheet_with_strikeouts(
            src_file,
            [],  # No duplicates in rebuild
            all_invalid_events,
            toris_path,
            None,
            computed_days,
            override_valid_rows=all_valid_rows,
        )
        log(f"    - Created TORIS with {computed_days} total days")
    else:
        log(f"    ⚠️ Source file not found for TORIS: {src_file}")
    
    # =============================
    # 6. WRITE SUMMARY FILES
    # =============================
    log(f"  → Writing summary files")
    write_summary_files(summary_data)
    
    # =============================
    # 7. REBUILD MERGED PACKAGE
    # =============================
    log(f"  → Rebuilding merged package")
    
    # 🔹 FIX: Delete old PACKAGE folder to force fresh merge
    # This ensures the merged PDF uses the updated TORIS, not a cached version
    if os.path.exists(PACKAGE_FOLDER):
        shutil.rmtree(PACKAGE_FOLDER)
        log("    - Deleted old PACKAGE folder for fresh merge")
    
    merge_all_pdfs()
    
    log(f"✅ REBUILD COMPLETE FOR {member_key}")
    log(f"   - {pg13_count} PG-13 forms created")
    log(f"   - TORIS certification updated")
    log(f"   - Summary files regenerated")
    log(f"   - Merged package updated")
    
    return {
        "status": "success",
        "member_key": member_key,
        "pg13_count": pg13_count,
        "valid_rows": len(all_valid_rows),
        "invalid_events": len(all_invalid_events),
    }
