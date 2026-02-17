import os
import io
import json
import zipfile
import shutil
import threading
import re
import csv
from flask import Blueprint, request, jsonify, send_file, send_from_directory

from app.core.logger import (
    log,
    clear_logs,
    get_logs,
    get_progress,
    reset_progress,
    set_progress,
)

from app.core.config import (
    DATA_DIR,
    OUTPUT_DIR,
    TEMPLATE,
    RATE_FILE,
    REVIEW_JSON_PATH,
    PACKAGE_FOLDER,
    OVERRIDES_DIR,
    CONFIG_DIR,
    load_certifying_officer,
    save_certifying_officer,
)

from app.processing import process_all
import app.core.rates as rates
from app.core.overrides import (
    save_override,
    clear_overrides,
    apply_overrides,
    load_overrides,
)

from app.processing import rebuild_outputs_from_review, rebuild_single_member
from app.core.merge import merge_all_pdfs

bp = Blueprint("routes", __name__)
FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "web", "frontend")

# 🔹 PATCH: Global flag for cancelling processing with lock for thread safety
processing_cancelled = False
processing_lock = threading.Lock()
processing_thread = None


def _get_override_path(member_key):
    """
    Local copy of private function from overrides.py to ensure stable path generation.
    Convert 'STG1 NIVERA,RYAN' → 'STG1_NIVERA_RYAN.json'
    """
    safe = member_key.replace(" ", "_").replace(",", "_")
    return os.path.join(OVERRIDES_DIR, f"{safe}.json")


def _delete_single_override(member_key, sheet_file, event_index):
    """
    Deletes a single override entry for a specific event.
    """
    path = _get_override_path(member_key)
    if not os.path.exists(path):
        return

    data = load_overrides(member_key)
    overrides = data.get("overrides", [])
    original_count = len(overrides)

    data["overrides"] = [
        ov for ov in overrides
        if not (ov.get("sheet_file") == sheet_file and ov.get("event_index") == event_index)
    ]

    if not data["overrides"]:
        clear_overrides(member_key)
    elif len(data["overrides"]) < original_count:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)


def _norm_status(v):
    """
    Only allow UI dropdown values: "" | "valid" | "invalid"
    """
    if v is None:
        return ""
    v = str(v).strip().lower()
    return v if v in ("", "valid", "invalid") else ""


def _to_int(v, default=None):
    try:
        return int(v)
    except Exception:
        return default


@bp.route("/")
def home():
    return send_from_directory(FRONTEND_DIR, "index.html")


@bp.route("/signatures.html")
def signatures_page():
    """Serve the signature management page"""
    return send_from_directory(FRONTEND_DIR, "signatures.html")


@bp.route("/signature-manager.js")
def signature_manager_js():
    """Serve the signature manager JavaScript file"""
    return send_from_directory(FRONTEND_DIR, "signature-manager.js")


@bp.route("/process", methods=["POST"])
def process_route():
    global processing_cancelled, processing_thread
    
    # 🔹 PATCH: Thread-safe cancellation reset
    with processing_lock:
        processing_cancelled = False
    
    clear_logs()
    reset_progress()
    log("=== PROCESS STARTED ===")
    set_progress(status="PROCESSING", percent=1, current_step="Saving input files")

    files = request.files.getlist("files") or request.files.getlist("pdfs") or []
    for f in files:
        if f and getattr(f, "filename", ""):
            dst = os.path.join(DATA_DIR, f.filename)
            f.save(dst)
            log(f"SAVED INPUT FILE → {dst}")

    if "template_pdf" in request.files:
        request.files["template_pdf"].save(TEMPLATE)
        log("UPDATED TEMPLATE PDF")

    if "rates_csv" in request.files:
        request.files["rates_csv"].save(RATE_FILE)
        try:
            rates.load_rates()
        except Exception as e:
            log(f"RATES CSV RELOAD ERROR → {e}")
        else:
            log("RATES CSV RELOADED")

    strike_color = request.form.get("strikeout_color", "Black")
    consolidate_pg13 = request.form.get("consolidate_pg13", "false").lower() == "true"
    consolidate_all_missions = request.form.get("consolidate_all_missions", "false").lower() == "true"
    
    if consolidate_pg13:
        log("PG-13 CONSOLIDATION ENABLED → Will create one form per ship")
    if consolidate_all_missions:
        log("ALL MISSIONS CONSOLIDATION ENABLED → Will create one form per member with all ships")

    def _run():
        global processing_cancelled
        try:
            # 🔹 PATCH: Check cancellation at start
            with processing_lock:
                if processing_cancelled:
                    log("PROCESSING CANCELLED BEFORE START")
                    set_progress(status="CANCELLED", percent=0, current_step="Cancelled")
                    return
                
            set_progress(status="PROCESSING", percent=5, current_step="Processing")
            process_all(strike_color=strike_color, consolidate_pg13=consolidate_pg13, consolidate_all_missions=consolidate_all_missions)

            # 🔹 PATCH: Check cancellation after processing
            with processing_lock:
                if processing_cancelled:
                    log("PROCESSING CANCELLED AFTER COMPLETION")
                    set_progress(status="CANCELLED", percent=0, current_step="Cancelled")
                    return

            original_path = REVIEW_JSON_PATH.replace('.json', '_ORIGINAL.json')
            if os.path.exists(REVIEW_JSON_PATH):
                shutil.copy(REVIEW_JSON_PATH, original_path)
                log(f"CREATED ORIGINAL REVIEW BACKUP → {original_path}")

            set_progress(status="COMPLETE", percent=100, current_step="Complete")
            log("PROCESS COMPLETE")
        except Exception as e:
            log(f"PROCESS ERROR → {e}")
            set_progress(status="ERROR", percent=0, current_step=f"Error: {str(e)}")

    # 🔹 PATCH: Store thread reference
    processing_thread = threading.Thread(target=_run, daemon=True)
    processing_thread.start()
    
    return jsonify({"status": "STARTED"})


@bp.route("/cancel_process", methods=["POST"])
def cancel_process():
    """🔹 PATCH: Enhanced cancel with thread-safe flag management"""
    global processing_cancelled
    
    with processing_lock:
        processing_cancelled = True
    
    log("=== CANCEL REQUEST RECEIVED ===")
    set_progress(status="CANCELLING", percent=0, current_step="Cancelling operation...")
    
    # Give the process a moment to detect cancellation
    import time
    time.sleep(0.5)
    
    # Force set to cancelled state
    set_progress(status="CANCELLED", percent=0, current_step="Processing cancelled by user")
    
    return jsonify({"status": "cancelled", "message": "Cancellation signal sent"})


@bp.route("/rebuild_outputs", methods=["POST"])
def rebuild_outputs():
    try:
        consolidate_pg13 = request.json.get("consolidate_pg13", False) if request.json else False
        consolidate_all_missions = request.json.get("consolidate_all_missions", False) if request.json else False
        
        log("=== REBUILD OUTPUTS STARTED ===")
        if consolidate_pg13:
            log("PG-13 CONSOLIDATION ENABLED → Will create one form per ship")
        if consolidate_all_missions:
            log("ALL MISSIONS CONSOLIDATION ENABLED → Will create one form per member with all ships")
        
        rebuild_outputs_from_review(consolidate_pg13=consolidate_pg13, consolidate_all_missions=consolidate_all_missions)
        merge_all_pdfs()
        log("=== REBUILD OUTPUTS COMPLETE ===")
        return jsonify({"status": "ok"})
    except Exception as e:
        log(f"REBUILD OUTPUTS ERROR → {e}")
        return jsonify({"status": "error", "error": str(e)}), 500


@bp.route("/progress")
def progress():
    p = get_progress()
    return jsonify({
        "status": p.get("status", "IDLE"),
        "percent": p.get("percent", 0),
        "log": "\n".join(p.get("log", []) or []),
        "current_step": p.get("current_step", ""),
        "details": p.get("details", {}) or {},
    })


@bp.route("/logs")
def logs():
    return jsonify({"log": "\n".join(get_logs())})


def _load_review():
    """Load the ORIGINAL review state (before any overrides)."""
    original_path = REVIEW_JSON_PATH.replace('.json', '_ORIGINAL.json')

    if os.path.exists(original_path):
        try:
            with open(original_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            log(f"Error loading original: {e}")

    if not os.path.exists(REVIEW_JSON_PATH):
        return {}
    try:
        with open(REVIEW_JSON_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log(f"REVIEW JSON READ ERROR → {e}")
        return {}


def _write_review(state: dict) -> None:
    """Write the review state with overrides applied."""
    os.makedirs(os.path.dirname(REVIEW_JSON_PATH), exist_ok=True)
    with open(REVIEW_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


@bp.route("/api/members")
def api_members():
    """
    Return members for Signature Manager.

    Default response:
      { "status": "success", "members": [ ... ] }

    Backward compatibility:
      /api/members?format=list  -> returns legacy JSON list [ ... ]
    """
    state = _load_review()
    members = set(state.keys())

    # Also include roster members (config/atgsd_n811.csv) so signatures can be assigned
    # even before any PDFs are processed.
    try:
        if os.path.exists(RATE_FILE):
            with open(RATE_FILE, "r", encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                # Normalize headers to lower-case
                if reader.fieldnames:
                    reader.fieldnames = [h.lstrip("\ufeff").strip().strip('"').lower() for h in reader.fieldnames]

                for row in reader:
                    rate = (row.get("rate") or "").strip().upper()
                    last = (row.get("last") or "").strip().upper()
                    first = (row.get("first") or "").strip().upper()
                    if not last or not first:
                        continue
                    # Member key format used throughout processing.py
                    member_key = f"{rate} {last},{first}".strip()
                    members.add(member_key)
    except Exception as e:
        log(f"/api/members roster load error → {e}")

    members_sorted = sorted(members)

    # Legacy list mode for any older callers
    if (request.args.get("format") or "").lower() == "list":
        return jsonify(members_sorted)

    return jsonify({"status": "success", "members": members_sorted})
@bp.route("/api/member/<path:member_key>/sheets")
def api_member_sheets(member_key):
    state = _load_review()
    member = state.get(member_key, {})
    return jsonify([s.get("source_file") for s in member.get("sheets", []) if s.get("source_file")])


@bp.route("/api/member/<path:member_key>/sheet/<path:sheet_file>")
def api_single_sheet(member_key, sheet_file):
    """Load a single sheet with overrides applied."""
    state = _load_review()
    member = state.get(member_key)
    if not member:
        return jsonify({}), 404

    member = apply_overrides(member_key, member)

    for sheet in member.get("sheets", []):
        if sheet.get("source_file") == sheet_file:
            return jsonify({
                **sheet,
                "valid_rows": sheet.get("rows", []),
                "invalid_events": sheet.get("invalid_events", []),
            })

    return jsonify({}), 404


@bp.route("/api/overrides/batch", methods=["POST"])
def api_override_batch():
    """Batch save overrides."""
    payload_list = request.get_json(silent=True) or []
    if not isinstance(payload_list, list):
        return jsonify({"error": "Request payload must be a list"}), 400

    affected_members = set()

    for payload in payload_list:
        member_key = (payload.get("member_key") or "").strip()
        sheet_file = (payload.get("sheet_file") or "").strip()
        event_index = _to_int(payload.get("event_index"), default=None)

        if not member_key or not sheet_file or event_index is None:
            continue

        affected_members.add(member_key)

        status = _norm_status(payload.get("status"))
        reason = (payload.get("reason") or "").strip()
        source = payload.get("source", "manual")

        # 🔹 PATCH FIX: Always save the override, even if status and reason are empty
        # This allows users to explicitly clear reasons while maintaining override record
        save_override(
            member_key=member_key,
            sheet_file=sheet_file,
            event_index=event_index,
            status=status or None,
            reason=reason,
            source=source,
        )

    if affected_members:
        state = _load_review()
        for mk in affected_members:
            if mk in state:
                state[mk] = apply_overrides(mk, state[mk])
        _write_review(state)

    return jsonify({"status": "batch processed"})


@bp.route("/api/override", methods=["POST"])
def api_override():
    """Save a single override."""
    payload = request.get_json(silent=True) or {}

    member_key = (payload.get("member_key") or "").strip()
    sheet_file = (payload.get("sheet_file") or "").strip()
    event_index = _to_int(payload.get("event_index"), default=None)

    if not member_key or not sheet_file or event_index is None:
        return jsonify({"error": "member_key, sheet_file, event_index required"}), 400

    status = _norm_status(payload.get("status"))
    reason = (payload.get("reason") or "").strip()
    source = payload.get("source", "manual")

    # 🔹 PATCH FIX: Always save the override, even if status and reason are empty
    # This allows users to explicitly clear reasons while maintaining override record
    save_override(
        member_key=member_key,
        sheet_file=sheet_file,
        event_index=event_index,
        status=status or None,
        reason=reason,
        source=source,
    )

    state = _load_review()
    if member_key in state:
        state[member_key] = apply_overrides(member_key, state[member_key])
        _write_review(state)

    return jsonify({"status": "saved"})


@bp.route("/api/override", methods=["DELETE"])
def api_override_clear():
    """Clear all overrides for a member."""
    payload = request.get_json(silent=True) or {}
    mk = (payload.get("member_key") or "").strip()
    if not mk:
        return jsonify({"error": "member_key required"}), 400

    clear_overrides(mk)

    state = _load_review()
    if mk in state:
        state[mk] = apply_overrides(mk, state[mk])
        _write_review(state)

    return jsonify({"status": "cleared"})


@bp.route("/download_all")
def download_all():
    mem = io.BytesIO()
    with zipfile.ZipFile(mem, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _, files in os.walk(OUTPUT_DIR):
            for f in files:
                full = os.path.join(root, f)
                z.write(full, os.path.relpath(full, OUTPUT_DIR))
    mem.seek(0)
    return send_file(mem, as_attachment=True, download_name="ALL_OUTPUT.zip")


@bp.route("/download_merged")
def download_merged():
    """
    Download the merged package.
    
    🔹 NUCLEAR FIX: Regenerate the package EVERY TIME to ensure fresh data.
    This prevents any caching issues and ensures the latest TORIS is included.
    """
    import shutil
    import time
    from app.core.merge import merge_all_pdfs
    
    # 🔹 FIX: Force complete rebuild of package before download
    if os.path.exists(PACKAGE_FOLDER):
        shutil.rmtree(PACKAGE_FOLDER)
        log("Download Package: Deleted old PACKAGE folder for fresh generation")
    
    # Regenerate package from scratch
    log("Download Package: Generating fresh merged package...")
    merge_all_pdfs()
    log("Download Package: Fresh package created")
    
    if not os.path.exists(PACKAGE_FOLDER):
        return jsonify({"error": "Merged package folder not found"}), 404
    
    # Create ZIP with cache-busting timestamp
    timestamp = str(int(time.time()))
    
    mem = io.BytesIO()
    with zipfile.ZipFile(mem, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _, files in os.walk(PACKAGE_FOLDER):
            for f in files:
                full = os.path.join(root, f)
                z.write(full, os.path.relpath(full, PACKAGE_FOLDER))
    mem.seek(0)
    
    # Add no-cache headers to prevent browser caching
    response = send_file(
        mem, 
        as_attachment=True, 
        download_name=f"MERGED_PACKAGE_{timestamp}.zip",
        mimetype='application/zip'
    )
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    
    return response


@bp.route("/download_member/<member_key>")
def download_member(member_key):
    """Download all files for a specific member as a ZIP."""
    from app.core.config import SUMMARY_PDF_FOLDER, TORIS_CERT_FOLDER, SEA_PAY_PG13_FOLDER
    
    safe_prefix = member_key.replace(" ", "_").replace(",", "_")
    
    mem = io.BytesIO()
    file_count = 0
    
    with zipfile.ZipFile(mem, "w", zipfile.ZIP_DEFLATED) as z:
        summary_path = os.path.join(SUMMARY_PDF_FOLDER, f"{safe_prefix}_SUMMARY.pdf")
        if os.path.exists(summary_path):
            z.write(summary_path, os.path.basename(summary_path))
            file_count += 1
        
        if os.path.exists(TORIS_CERT_FOLDER):
            toris_files = [f for f in os.listdir(TORIS_CERT_FOLDER) 
                          if f.startswith(safe_prefix) and f.endswith('.pdf')]
            for f in toris_files:
                full_path = os.path.join(TORIS_CERT_FOLDER, f)
                z.write(full_path, f)
                file_count += 1
        
        if os.path.exists(SEA_PAY_PG13_FOLDER):
            pg13_files = [f for f in os.listdir(SEA_PAY_PG13_FOLDER) 
                         if f.startswith(safe_prefix) and f.endswith('.pdf')]
            for f in sorted(pg13_files):
                full_path = os.path.join(SEA_PAY_PG13_FOLDER, f)
                z.write(full_path, f)
                file_count += 1
    
    if file_count == 0:
        return jsonify({"error": f"No files found for member {member_key}"}), 404
    
    mem.seek(0)
    return send_file(
        mem, 
        as_attachment=True, 
        download_name=f"{safe_prefix}_FILES.zip",
        mimetype='application/zip'
    )


@bp.route("/download_member_summary/<member_key>")
def download_member_summary(member_key):
    """Download only the summary PDF for a member."""
    from app.core.config import SUMMARY_PDF_FOLDER
    
    safe_prefix = member_key.replace(" ", "_").replace(",", "_")
    summary_path = os.path.join(SUMMARY_PDF_FOLDER, f"{safe_prefix}_SUMMARY.pdf")
    
    if not os.path.exists(summary_path):
        return jsonify({"error": f"Summary not found for {member_key}"}), 404
    
    return send_file(
        summary_path,
        as_attachment=True,
        download_name=f"{safe_prefix}_SUMMARY.pdf"
    )


@bp.route("/download_member_toris/<member_key>")
def download_member_toris(member_key):
    """Download only the TORIS cert for a member."""
    from app.core.config import TORIS_CERT_FOLDER
    
    safe_prefix = member_key.replace(" ", "_").replace(",", "_")
    
    if not os.path.exists(TORIS_CERT_FOLDER):
        return jsonify({"error": "TORIS folder not found"}), 404
    
    toris_files = [f for f in os.listdir(TORIS_CERT_FOLDER) 
                  if f.startswith(safe_prefix) and f.endswith('.pdf')]
    
    if not toris_files:
        return jsonify({"error": f"TORIS cert not found for {member_key}"}), 404
    
    toris_path = os.path.join(TORIS_CERT_FOLDER, toris_files[0])
    return send_file(
        toris_path,
        as_attachment=True,
        download_name=toris_files[0]
    )


@bp.route("/download_member_pg13s/<member_key>")
def download_member_pg13s(member_key):
    """Download only the PG-13 forms for a member as a ZIP."""
    from app.core.config import SEA_PAY_PG13_FOLDER
    
    safe_prefix = member_key.replace(" ", "_").replace(",", "_")
    
    if not os.path.exists(SEA_PAY_PG13_FOLDER):
        return jsonify({"error": "PG-13 folder not found"}), 404
    
    pg13_files = [f for f in os.listdir(SEA_PAY_PG13_FOLDER) 
                 if f.startswith(safe_prefix) and f.endswith('.pdf')]
    
    if not pg13_files:
        return jsonify({"error": f"No PG-13 forms found for {member_key}"}), 404
    
    if len(pg13_files) == 1:
        pg13_path = os.path.join(SEA_PAY_PG13_FOLDER, pg13_files[0])
        return send_file(
            pg13_path,
            as_attachment=True,
            download_name=pg13_files[0]
        )
    
    mem = io.BytesIO()
    with zipfile.ZipFile(mem, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(pg13_files):
            full_path = os.path.join(SEA_PAY_PG13_FOLDER, f)
            z.write(full_path, f)
    
    mem.seek(0)
    return send_file(
        mem,
        as_attachment=True,
        download_name=f"{safe_prefix}_PG13_FORMS.zip",
        mimetype='application/zip'
    )


@bp.route("/download_custom", methods=["POST"])
def download_custom():
    """
    🔹 PATCH: Enhanced error handling and logging for custom downloads.
    Download or merge custom selection of members and file types.
    """
    from app.core.config import SUMMARY_PDF_FOLDER, TORIS_CERT_FOLDER, SEA_PAY_PG13_FOLDER
    from PyPDF2 import PdfWriter, PdfReader
    
    data = request.json
    action = data.get("action", "download")
    selections = data.get("selections", {})
    
    log(f"CUSTOM DOWNLOAD REQUEST → Action: {action}, Selections: {len(selections)} members")
    
    if not selections:
        log("ERROR: No selections provided")
        return jsonify({"error": "No selections provided"}), 400
    
    if action == "download":
        mem = io.BytesIO()
        file_count = 0
        
        with zipfile.ZipFile(mem, "w", zipfile.ZIP_DEFLATED) as z:
            for member_key, options in selections.items():
                safe_prefix = member_key.replace(" ", "_").replace(",", "_")
                log(f"Processing member: {member_key} (safe: {safe_prefix})")
                
                if options.get("summary"):
                    summary_path = os.path.join(SUMMARY_PDF_FOLDER, f"{safe_prefix}_SUMMARY.pdf")
                    if os.path.exists(summary_path):
                        z.write(summary_path, os.path.basename(summary_path))
                        file_count += 1
                        log(f"  ✓ Added summary: {os.path.basename(summary_path)}")
                    else:
                        log(f"  ✗ Summary not found: {summary_path}")
                
                if options.get("toris") and os.path.exists(TORIS_CERT_FOLDER):
                    toris_files = [f for f in os.listdir(TORIS_CERT_FOLDER) 
                                  if f.startswith(safe_prefix) and f.endswith('.pdf')]
                    for f in toris_files:
                        z.write(os.path.join(TORIS_CERT_FOLDER, f), f)
                        file_count += 1
                        log(f"  ✓ Added TORIS: {f}")
                    if not toris_files:
                        log(f"  ✗ No TORIS files found for {safe_prefix}")
                
                if options.get("pg13") and os.path.exists(SEA_PAY_PG13_FOLDER):
                    pg13_files = [f for f in os.listdir(SEA_PAY_PG13_FOLDER) 
                                 if f.startswith(safe_prefix) and f.endswith('.pdf')]
                    for f in sorted(pg13_files):
                        z.write(os.path.join(SEA_PAY_PG13_FOLDER, f), f)
                        file_count += 1
                        log(f"  ✓ Added PG-13: {f}")
                    if not pg13_files:
                        log(f"  ✗ No PG-13 files found for {safe_prefix}")
        
        log(f"CUSTOM DOWNLOAD COMPLETE → {file_count} files added to ZIP")
        
        if file_count == 0:
            log("ERROR: No files found for selection")
            return jsonify({"error": "No files found for selection"}), 404
        
        mem.seek(0)
        return send_file(mem, as_attachment=True, download_name="CUSTOM_SELECTION.zip", mimetype='application/zip')
    
    elif action == "merge":
        writer = PdfWriter()
        page_count = 0
        
        for member_key, options in selections.items():
            safe_prefix = member_key.replace(" ", "_").replace(",", "_")
            parent_bookmark = writer.add_outline_item(member_key, page_count)
            log(f"Merging member: {member_key}")
            
            if options.get("summary"):
                summary_path = os.path.join(SUMMARY_PDF_FOLDER, f"{safe_prefix}_SUMMARY.pdf")
                if os.path.exists(summary_path):
                    reader = PdfReader(summary_path)
                    writer.add_outline_item("Summary", page_count, parent=parent_bookmark)
                    for page in reader.pages:
                        writer.add_page(page)
                        page_count += 1
                    log(f"  ✓ Merged summary ({len(reader.pages)} pages)")
            
            if options.get("toris") and os.path.exists(TORIS_CERT_FOLDER):
                toris_files = [f for f in os.listdir(TORIS_CERT_FOLDER) 
                              if f.startswith(safe_prefix) and f.endswith('.pdf')]
                for f in toris_files:
                    reader = PdfReader(os.path.join(TORIS_CERT_FOLDER, f))
                    writer.add_outline_item("TORIS Certification", page_count, parent=parent_bookmark)
                    for page in reader.pages:
                        writer.add_page(page)
                        page_count += 1
                    log(f"  ✓ Merged TORIS ({len(reader.pages)} pages)")
            
            if options.get("pg13") and os.path.exists(SEA_PAY_PG13_FOLDER):
                pg13_files = [f for f in os.listdir(SEA_PAY_PG13_FOLDER) 
                             if f.startswith(safe_prefix) and f.endswith('.pdf')]
                if pg13_files:
                    pg13_parent = writer.add_outline_item("PG-13 Forms", page_count, parent=parent_bookmark)
                    for f in sorted(pg13_files):
                        reader = PdfReader(os.path.join(SEA_PAY_PG13_FOLDER, f))
                        match = re.search(r'__PG13__(.+?)__', f)
                        if match:
                            ship_name = match.group(1).replace("_", " ")
                        else:
                            ship_name = f
                        writer.add_outline_item(ship_name, page_count, parent=pg13_parent)
                        for page in reader.pages:
                            writer.add_page(page)
                            page_count += 1
                    log(f"  ✓ Merged {len(pg13_files)} PG-13 forms")
        
        log(f"CUSTOM MERGE COMPLETE → {page_count} pages")
        
        if page_count == 0:
            log("ERROR: No pages to merge")
            return jsonify({"error": "No pages to merge"}), 404
        
        mem = io.BytesIO()
        writer.write(mem)
        mem.seek(0)
        return send_file(mem, as_attachment=True, download_name="CUSTOM_MERGED_PACKAGE.pdf", mimetype='application/pdf')
    
    return jsonify({"error": "Invalid action"}), 400


@bp.route("/reset", methods=["POST"])
def reset():
    """Reset all data including original backup."""
    for root, _, files in os.walk(DATA_DIR):
        for f in files:
            try:
                os.remove(os.path.join(root, f))
            except Exception as e:
                log(f"RESET INPUT FILE ERROR → {e}")

    for root, _, files in os.walk(OUTPUT_DIR):
        for f in files:
            try:
                os.remove(os.path.join(root, f))
            except Exception as e:
                log(f"RESET OUTPUT FILE ERROR → {e}")

    original_path = REVIEW_JSON_PATH.replace('.json', '_ORIGINAL.json')
    if os.path.exists(original_path):
        try:
            os.remove(original_path)
            log("REMOVED ORIGINAL REVIEW BACKUP")
        except Exception as e:
            log(f"RESET ORIGINAL BACKUP ERROR → {e}")

    clear_logs()
    reset_progress()
    log("RESET COMPLETE (files cleared)")
    return jsonify({"status": "reset"})

# =============================================================================
# REBUILD SINGLE MEMBER ROUTES
# =============================================================================


@bp.route("/rebuild_member/<path:member_key>", methods=["POST"])
def rebuild_member(member_key):
    """
    Rebuild outputs for a single member only.
    Much faster than rebuilding everything.
    
    POST body (optional):
    {
        "consolidate_pg13": true/false,
        "consolidate_all_missions": true/false
    }
    """
    try:
        payload = request.get_json(silent=True) or {}
        consolidate_pg13 = payload.get("consolidate_pg13", False)
        consolidate_all_missions = payload.get("consolidate_all_missions", False)
        
        log(f"=== REBUILD SINGLE MEMBER STARTED: {member_key} ===")
        
        result = rebuild_single_member(member_key, consolidate_pg13=consolidate_pg13, consolidate_all_missions=consolidate_all_missions)
        
        if result["status"] == "error":
            log(f"REBUILD SINGLE MEMBER ERROR → {result['message']}")
            return jsonify(result), 404
        
        log(f"=== REBUILD SINGLE MEMBER COMPLETE: {member_key} ===")
        return jsonify(result)
        
    except Exception as e:
        log(f"REBUILD SINGLE MEMBER ERROR → {e}")
        return jsonify({"status": "error", "error": str(e)}), 500


# Alternative: Rebuild member after saving override
@bp.route("/api/override/save_and_rebuild", methods=["POST"])
def api_override_save_and_rebuild():
    """
    Save an override AND immediately rebuild that member's outputs.
    
    This is a convenience endpoint that combines:
    1. Saving the override (like /api/override)
    2. Rebuilding just that member (like /rebuild_member)
    
    POST body:
    {
        "member_key": "STG1 NIVERA,RYAN",
        "sheet_file": "filename.pdf",
        "event_index": 123,
        "status": "valid" | "invalid" | "",
        "reason": "optional reason text",
        "consolidate_pg13": true/false  (optional, default false),
        "consolidate_all_missions": true/false  (optional, default false)
    }
    """
    try:
        payload = request.get_json(silent=True) or {}
        
        member_key = (payload.get("member_key") or "").strip()
        sheet_file = (payload.get("sheet_file") or "").strip()
        event_index = _to_int(payload.get("event_index"), default=None)
        
        if not member_key or not sheet_file or event_index is None:
            return jsonify({"error": "member_key, sheet_file, event_index required"}), 400
        
        status = _norm_status(payload.get("status"))
        reason = (payload.get("reason") or "").strip()
        source = payload.get("source", "manual")
        consolidate_pg13 = payload.get("consolidate_pg13", False)
        consolidate_all_missions = payload.get("consolidate_all_missions", False)
        
        # 1. Save the override
        save_override(
            member_key=member_key,
            sheet_file=sheet_file,
            event_index=event_index,
            status=status or None,
            reason=reason,
            source=source,
        )
        
        # 2. Apply overrides and update review JSON
        state = _load_review()
        if member_key in state:
            state[member_key] = apply_overrides(member_key, state[member_key])
            _write_review(state)
        
        # 3. Rebuild just this member's outputs
        rebuild_result = rebuild_single_member(member_key, consolidate_pg13=consolidate_pg13, consolidate_all_missions=consolidate_all_missions)
        
        if rebuild_result["status"] == "error":
            return jsonify({
                "status": "error",
                "message": f"Override saved but rebuild failed: {rebuild_result.get('message')}"
            }), 500
        
        return jsonify({
            "status": "success",
            "override_saved": True,
            "rebuild_complete": True,
            "rebuild_info": rebuild_result
        })
        
    except Exception as e:
        log(f"SAVE AND REBUILD ERROR → {e}")
        return jsonify({"status": "error", "error": str(e)}), 500


# ------------------------------------------------
# CERTIFYING OFFICER ROUTES
# ------------------------------------------------
# ------------------------------------------------
# CERTIFYING OFFICER ROUTES (FIXED: single source of truth)
# ------------------------------------------------

@bp.route("/api/certifying_officer", methods=["GET", "POST"])
def api_certifying_officer():
    """
    GET:  Return current certifying officer information.
    POST: Save certifying officer information.
    """
    try:
        if request.method == "GET":
            officer = load_certifying_officer()
            return jsonify({"status": "success", "officer": officer})

        # POST
        data = request.get_json(silent=True) or {}

        rate = (data.get("rate") or "").strip().upper()
        last_name = (data.get("last_name") or "").strip().upper()
        first_name = (data.get("first_name") or "").strip().upper()
        middle_name = (data.get("middle_name") or "").strip().upper()

        date_yyyymmdd = (data.get("date_yyyymmdd") or "").strip()
        if date_yyyymmdd:
            if not (len(date_yyyymmdd) == 8 and date_yyyymmdd.isdigit()):
                return jsonify({"status": "error", "error": "date_yyyymmdd must be 8 digits (YYYYMMDD)"}), 400

        if not last_name:
            return jsonify({"status": "error", "error": "last_name is required"}), 400

        # IMPORTANT: call with 4 positional args (matches your config.save_certifying_officer signature)
        save_certifying_officer(rate, last_name, first_name, middle_name, date_yyyymmdd)

        return jsonify({
            "status": "success",
            "officer": {
                "rate": rate,
                "last_name": last_name,
                "first_name": first_name,
                "middle_name": middle_name,
                "date_yyyymmdd": date_yyyymmdd,
            }
        })

    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500

@bp.route("/api/certifying_officer_choices", methods=["GET"])
def get_certifying_officer_choices():
    """
    Return certifying-officer choices from config/atgsd_n811.csv

    CSV headers expected:
      rate,last,first

    The "first" field may include a middle initial (e.g. "RYAN N")
    """
    try:
        choices = []

        # 🔹 PATCH: Read from N811 roster CSV in CONFIG_DIR (NOT RATE_FILE)
        N811_CSV = os.path.join(CONFIG_DIR, "atgsd_n811.csv")
        if not os.path.exists(N811_CSV):
            return jsonify({"status": "success", "choices": choices})

        def clean(v):
            return (v or "").replace("\t", " ").strip().upper()

        with open(N811_CSV, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                rate = clean(row.get("rate"))
                last = clean(row.get("last"))
                first_raw = clean(row.get("first"))

                if not last:
                    continue

                parts = re.split(r"\s+", first_raw)
                first_name = parts[0] if parts else ""
                middle_initial = ""

                if len(parts) > 1:
                    middle = re.sub(r"[^A-Z]", "", parts[1])
                    middle_initial = middle[:1] if middle else ""

                display = f"{rate} {last}, {first_name}"
                if middle_initial:
                    display += f" {middle_initial}."

                choices.append({
                    "rate": rate,
                    "last_name": last,
                    "first_name": first_name,
                    "middle_initial": middle_initial,
                    "display": display,
                })

        choices.sort(key=lambda x: (x["last_name"], x["first_name"], x["rate"]))
        return jsonify({"status": "success", "choices": choices})

    except Exception as e:
        log(f"CERTIFYING OFFICER CHOICES ERROR → {e}")
        return jsonify({"status": "error", "error": str(e)}), 500


# ============================================================================
# SIGNATURE MANAGEMENT API ENDPOINTS
# ============================================================================

@bp.route("/api/signatures/list", methods=["GET"])
def list_signatures():
    """
    Get signature library + per-member assignments.

    Query params:
      - include_thumbnails=true|false
      - include_full_res=true|false  (for export — returns full image_base64)
      - member_key=<member_key> (optional; returns assignments for that member)
    """
    try:
        from app.core.config import load_signatures, get_all_signatures, get_assignment_status

        include_thumbnails = request.args.get("include_thumbnails", "false").lower() == "true"
        include_full_res = request.args.get("include_full_res", "false").lower() == "true"
        member_key = (request.args.get("member_key") or "").strip() or None

        data = load_signatures()
        signatures = get_all_signatures(include_thumbnails=include_thumbnails, include_full_res=include_full_res)

        assignments_by_member = data.get("assignments_by_member", {}) or {}
        if member_key and member_key not in assignments_by_member:
            assignments_for_member = {
                "toris_certifying_officer": None,
                "pg13_certifying_official": None,
                "pg13_verifying_official": None,
            }
        elif member_key:
            assignments_for_member = assignments_by_member.get(member_key)
        else:
            assignments_for_member = {
                "toris_certifying_officer": None,
                "pg13_certifying_official": None,
                "pg13_verifying_official": None,
            }

        status = get_assignment_status(member_key=member_key) if member_key else get_assignment_status()

        return jsonify({
            "status": "success",
            "signatures": signatures,
            "member_key": member_key,
            "assignments": assignments_for_member,
            "assignments_by_member": assignments_by_member if not member_key else None,
            "assignment_status": status,
            "assignment_rules": data.get("assignment_rules", {})
        })
    except Exception as e:
        log(f"❌ LIST SIGNATURES ERROR → {e}")
        return jsonify({"status": "error", "message": str(e)}), 500



@bp.route("/api/signatures/status", methods=["GET"])
def signatures_status():
    """Return signature assignment status summary.
    If member_key is provided, returns status for that member only.
    """
    try:
        from app.core.config import get_assignment_status
        member_key = (request.args.get("member_key") or "").strip() or None
        status = get_assignment_status(member_key=member_key) if member_key else get_assignment_status()
        return jsonify({"status": "success", "assignment_status": status, "member_key": member_key})
    except Exception as e:
        log(f"❌ SIGNATURE STATUS ERROR → {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@bp.route("/api/signatures/create", methods=["POST"])
def create_signature():
    """
    Create a new signature in the library.
    Mobile-friendly with device tracking.
    """
    try:
        from app.core.config import save_signature
        
        data = request.get_json()
        name = data.get('name', '').strip()
        role = data.get('role', '').strip()
        sig_b64 = data.get('signature_base64', '').strip()
        device_id = data.get('device_id', 'unknown')
        device_name = data.get('device_name', 'Unknown Device')
        
        if not name:
            return jsonify({
                'status': 'error',
                'message': 'Signature name is required'
            }), 400
        
        if not sig_b64:
            return jsonify({
                'status': 'error',
                'message': 'Signature image is required'
            }), 400
        
        # Validate base64
        try:
            import base64
            base64.b64decode(sig_b64)
        except:
            return jsonify({
                'status': 'error',
                'message': 'Invalid base64 encoding'
            }), 400
        
        sig_id = save_signature(name, role, sig_b64, device_id, device_name)
        
        if sig_id:
            log(f"✅ SIGNATURE CREATED → {name} (ID: {sig_id}) from {device_name}")
            return jsonify({
                'status': 'success',
                'signature_id': sig_id,
                'message': 'Signature created successfully'
            })
        else:
            return jsonify({
                'status': 'error',
                'message': 'Failed to save signature'
            }), 500
            
    except Exception as e:
        log(f"❌ CREATE SIGNATURE ERROR → {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@bp.route("/api/signatures/assign", methods=["POST"])
def assign_signature_to_location():
    """
    Assign a signature to a member + document location.

    Body:
      { "member_key": "...", "location": "...", "signature_id": "sig_xxx" | null }
    """
    try:
        from app.core.config import assign_signature

        data = request.get_json() or {}
        member_key = (data.get("member_key") or "").strip()
        location = (data.get("location") or "").strip()
        signature_id = data.get("signature_id")

        if not member_key:
            return jsonify({"status": "error", "message": "member_key is required"}), 400
        if not location:
            return jsonify({"status": "error", "message": "Location is required"}), 400

        success, message = assign_signature(member_key, location, signature_id)

        if success:
            log(f"✅ SIGNATURE ASSIGNED → {member_key} / {location} = {signature_id}")
            return jsonify({"status": "success", "message": message})
        else:
            return jsonify({"status": "error", "message": message}), 400

    except Exception as e:
        log(f"❌ ASSIGN SIGNATURE ERROR → {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@bp.route("/api/signatures/auto-assign", methods=["POST"])
def auto_assign_signatures_endpoint():
    """
    Auto-assign signatures for ONE member (no signature reuse allowed).
    Body: { "member_key": "..." }
    """
    try:
        from app.core.config import auto_assign_signatures

        data = request.get_json() or {}
        member_key = (data.get("member_key") or "").strip()
        if not member_key:
            return jsonify({"status": "error", "message": "member_key is required"}), 400

        success, message, assignments_made = auto_assign_signatures(member_key)

        if success:
            log(f"✅ AUTO-ASSIGN → {message}")
            return jsonify({"status": "success", "message": message, "assignments_made": assignments_made})
        else:
            return jsonify({"status": "error", "message": message}), 400

    except Exception as e:
        log(f"❌ AUTO-ASSIGN ERROR → {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@bp.route("/api/signatures/get/<signature_id>", methods=["GET"])
def get_signature(signature_id):
    """
    Get a specific signature with full image data.
    """
    try:
        from app.core.config import load_signatures
        
        thumbnail_only = request.args.get('thumbnail_only', 'false').lower() == 'true'
        
        data = load_signatures()
        signature = next((s for s in data['signatures'] if s['id'] == signature_id), None)
        
        if signature:
            result = {
                'id': signature['id'],
                'name': signature['name'],
                'role': signature['role'],
                'created': signature['created'],
                'device_name': signature.get('device_name', 'Unknown'),
                'metadata': signature.get('metadata', {})
            }
            
            if thumbnail_only:
                result['thumbnail_base64'] = signature.get('thumbnail_base64', '')
            else:
                result['image_base64'] = signature['image_base64']
                result['thumbnail_base64'] = signature.get('thumbnail_base64', '')
            
            return jsonify({
                'status': 'success',
                'signature': result
            })
        else:
            return jsonify({
                'status': 'error',
                'message': 'Signature not found'
            }), 404
            
    except Exception as e:
        log(f"❌ GET SIGNATURE ERROR → {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500



@bp.route("/api/signatures/download/<signature_id>", methods=["GET"])
def download_signature(signature_id):
    """
    Download a single signature as a PNG file (for saving to phone/PC).
    """
    try:
        from app.core.config import load_signatures
        import base64
        from io import BytesIO
        from flask import send_file

        data = load_signatures()
        sig = next((s for s in data.get("signatures", []) if s.get("id") == signature_id), None)
        if not sig:
            return jsonify({"status": "error", "message": "Signature not found"}), 404

        png_bytes = base64.b64decode(sig["image_base64"])
        buf = BytesIO(png_bytes)
        buf.seek(0)
        filename = f"{sig.get('name','signature').strip().replace(' ', '_')}_{signature_id}.png"
        return send_file(buf, mimetype="image/png", as_attachment=True, download_name=filename)
    except Exception as e:
        log(f"❌ DOWNLOAD SIGNATURE ERROR → {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@bp.route("/api/signatures/import", methods=["POST"])
def import_signature_png():
    """
    Import a signature PNG (from phone/PC) into the signature library.

    multipart/form-data:
      - file: PNG
      - name: string (required)
      - role: string (optional)
      - device_id: string (optional)
      - device_name: string (optional)
    """
    try:
        from app.core.config import save_signature
        import base64

        if "file" not in request.files:
            return jsonify({"status": "error", "message": "file is required"}), 400

        f = request.files["file"]
        name = (request.form.get("name") or "").strip()
        role = (request.form.get("role") or "").strip()
        device_id = request.form.get("device_id") or "import"
        device_name = request.form.get("device_name") or "Imported"

        if not name:
            return jsonify({"status": "error", "message": "name is required"}), 400

        content = f.read()
        if not content:
            return jsonify({"status": "error", "message": "empty file"}), 400

        sig_b64 = base64.b64encode(content).decode("utf-8")
        sig_id = save_signature(name, role, sig_b64, device_id=device_id, device_name=device_name)

        if sig_id:
            log(f"✅ SIGNATURE IMPORTED → {name} (ID: {sig_id})")
            return jsonify({"status": "success", "signature_id": sig_id, "message": "Signature imported successfully"})
        return jsonify({"status": "error", "message": "Failed to import signature"}), 500

    except Exception as e:
        log(f"❌ IMPORT SIGNATURE ERROR → {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@bp.route("/api/signatures/import-multi", methods=["POST"])
def import_signatures_multi():
    """
    Import multiple signature PNGs at once.

    multipart/form-data:
      - files[]: one or more PNG files
      - base_name: base name prefix (e.g. "NIVERA") — auto-numbered if multiple
      - role: string (optional)
      - device_id: string (optional)
      - device_name: string (optional)

    Returns: { status, imported, skipped, failed, results: [...] }
    """
    try:
        from app.core.config import save_signature, load_signatures
        import base64 as b64_mod

        files = request.files.getlist("files[]")
        if not files:
            return jsonify({"status": "error", "message": "No files provided"}), 400

        base_name = (request.form.get("base_name") or "").strip()
        role = (request.form.get("role") or "").strip()
        device_id = request.form.get("device_id") or "import"
        device_name = request.form.get("device_name") or "Multi-Import"

        if not base_name:
            return jsonify({"status": "error", "message": "base_name is required"}), 400

        # Collect existing names for duplicate detection
        data = load_signatures()
        existing_names = {s.get("name", "").lower() for s in data.get("signatures", [])}

        results = []
        imported = 0
        skipped = 0
        failed = 0

        for idx, f in enumerate(files, start=1):
            # Build final name: base_name + zero-padded number
            num_str = str(idx).zfill(3)
            final_name = f"{base_name}{num_str}" if len(files) > 1 else base_name

            # Duplicate detection — auto-rename by appending _import
            if final_name.lower() in existing_names:
                # Try incrementing suffix until unique
                suffix = 2
                candidate = f"{final_name}_import{suffix}"
                while candidate.lower() in existing_names:
                    suffix += 1
                    candidate = f"{final_name}_import{suffix}"
                final_name = candidate
                log(f"⚠️ Duplicate name — renamed to {final_name}")

            content = f.read()
            if not content:
                results.append({"file": f.filename, "name": final_name, "status": "failed", "reason": "empty file"})
                failed += 1
                continue

            sig_b64 = b64_mod.b64encode(content).decode("utf-8")
            sig_id = save_signature(final_name, role, sig_b64, device_id=device_id, device_name=device_name)

            if sig_id:
                existing_names.add(final_name.lower())  # track within this import batch
                imported += 1
                results.append({"file": f.filename, "name": final_name, "status": "imported", "id": sig_id})
                log(f"✅ MULTI-IMPORT → {final_name} (ID: {sig_id})")
            else:
                failed += 1
                results.append({"file": f.filename, "name": final_name, "status": "failed", "reason": "save error"})

        return jsonify({
            "status": "success",
            "imported": imported,
            "skipped": skipped,
            "failed": failed,
            "results": results
        })

    except Exception as e:
        log(f"❌ MULTI-IMPORT ERROR → {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@bp.route("/api/signatures/delete/<signature_id>", methods=["DELETE"])
def delete_signature_endpoint(signature_id):
    """Delete a signature from the library."""
    try:
        from app.core.config import delete_signature
        
        success = delete_signature(signature_id)
        
        if success:
            log(f"✅ SIGNATURE DELETED → {signature_id}")
            return jsonify({
                'status': 'success',
                'message': 'Signature deleted successfully'
            })
        else:
            return jsonify({
                'status': 'error',
                'message': 'Failed to delete signature'
            }), 500
            
    except Exception as e:
        log(f"❌ DELETE SIGNATURE ERROR → {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@bp.route("/api/signatures/sync", methods=["POST"])
def sync_signatures():
    """
    Sync signatures from mobile device to server.
    Accepts batch upload of multiple signatures.
    """
    try:
        from app.core.config import save_signature
        
        data = request.get_json()
        signatures_to_sync = data.get('signatures', [])
        
        synced = []
        errors = []
        
        for sig_data in signatures_to_sync:
            try:
                local_id = sig_data.get('local_id')
                name = sig_data.get('name', '').strip()
                role = sig_data.get('role', '').strip()
                sig_b64 = sig_data.get('signature_base64', '').strip()
                device_id = sig_data.get('device_id', 'unknown')
                device_name = sig_data.get('device_name', 'Unknown Device')
                
                if not name or not sig_b64:
                    errors.append({'local_id': local_id, 'error': 'Missing required fields'})
                    continue
                
                server_id = save_signature(name, role, sig_b64, device_id, device_name)
                
                if server_id:
                    synced.append({
                        'local_id': local_id,
                        'server_id': server_id
                    })
                else:
                    errors.append({'local_id': local_id, 'error': 'Failed to save'})
                    
            except Exception as e:
                errors.append({'local_id': sig_data.get('local_id'), 'error': str(e)})
        
        log(f"✅ SYNC COMPLETE → {len(synced)} signatures synced, {len(errors)} errors")
        
        return jsonify({
            'status': 'success' if len(synced) > 0 else 'error',
            'synced': synced,
            'errors': errors,
            'message': f"Synced {len(synced)} signature(s)"
        })
        
    except Exception as e:
        log(f"❌ SYNC ERROR → {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500
