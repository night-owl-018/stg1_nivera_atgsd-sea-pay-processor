import os
import io
import json
import zipfile
import shutil
import threading
import re  # 🔹 PATCH: Add missing import for regex operations in custom download
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
)

from app.processing import process_all
import app.core.rates as rates
from app.core.overrides import (
    save_override,
    clear_overrides,
    apply_overrides,
    load_overrides,
)

from app.processing import rebuild_outputs_from_review
from app.core.merge import merge_all_pdfs

bp = Blueprint("routes", __name__)
FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "web", "frontend")


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


@bp.route("/process", methods=["POST"])
def process_route():
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

    def _run():
        try:
            set_progress(status="PROCESSING", percent=5, current_step="Processing")
            process_all(strike_color=strike_color)

            original_path = REVIEW_JSON_PATH.replace('.json', '_ORIGINAL.json')
            if os.path.exists(REVIEW_JSON_PATH):
                shutil.copy(REVIEW_JSON_PATH, original_path)
                log(f"CREATED ORIGINAL REVIEW BACKUP → {original_path}")

            set_progress(status="COMPLETE", percent=100, current_step="Complete")
            log("PROCESS COMPLETE")
        except Exception as e:
            log(f"PROCESS ERROR → {e}")
            set_progress(status="ERROR", percent=0, current_step="Error")

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"status": "STARTED"})


@bp.route("/rebuild_outputs", methods=["POST"])
def rebuild_outputs():
    try:
        log("=== REBUILD OUTPUTS STARTED ===")
        rebuild_outputs_from_review()
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
    return jsonify(sorted(_load_review().keys()))


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

        if status == "" and reason == "":
            _delete_single_override(member_key, sheet_file, event_index)
        else:
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

    if status == "" and reason == "":
        _delete_single_override(member_key, sheet_file, event_index)
    else:
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
    if not os.path.exists(PACKAGE_FOLDER):
        return jsonify({"error": "Merged package folder not found"}), 404
    mem = io.BytesIO()
    with zipfile.ZipFile(mem, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _, files in os.walk(PACKAGE_FOLDER):
            for f in files:
                full = os.path.join(root, f)
                z.write(full, os.path.relpath(full, PACKAGE_FOLDER))
    mem.seek(0)
    return send_file(mem, as_attachment=True, download_name="MERGED_PACKAGE.zip")


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
                
                # Add summary if selected
                if options.get("summary"):
                    summary_path = os.path.join(SUMMARY_PDF_FOLDER, f"{safe_prefix}_SUMMARY.pdf")
                    if os.path.exists(summary_path):
                        z.write(summary_path, os.path.basename(summary_path))
                        file_count += 1
                        log(f"  ✓ Added summary: {os.path.basename(summary_path)}")
                    else:
                        log(f"  ✗ Summary not found: {summary_path}")
                
                # Add TORIS if selected
                if options.get("toris") and os.path.exists(TORIS_CERT_FOLDER):
                    toris_files = [f for f in os.listdir(TORIS_CERT_FOLDER) 
                                  if f.startswith(safe_prefix) and f.endswith('.pdf')]
                    for f in toris_files:
                        z.write(os.path.join(TORIS_CERT_FOLDER, f), f)
                        file_count += 1
                        log(f"  ✓ Added TORIS: {f}")
                    if not toris_files:
                        log(f"  ✗ No TORIS files found for {safe_prefix}")
                
                # Add PG-13s if selected
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
            
            # Add summary if selected
            if options.get("summary"):
                summary_path = os.path.join(SUMMARY_PDF_FOLDER, f"{safe_prefix}_SUMMARY.pdf")
                if os.path.exists(summary_path):
                    reader = PdfReader(summary_path)
                    writer.add_outline_item("Summary", page_count, parent=parent_bookmark)
                    for page in reader.pages:
                        writer.add_page(page)
                        page_count += 1
                    log(f"  ✓ Merged summary ({len(reader.pages)} pages)")
            
            # Add TORIS if selected
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
            
            # Add PG-13s if selected
            if options.get("pg13") and os.path.exists(SEA_PAY_PG13_FOLDER):
                pg13_files = [f for f in os.listdir(SEA_PAY_PG13_FOLDER) 
                             if f.startswith(safe_prefix) and f.endswith('.pdf')]
                if pg13_files:
                    pg13_parent = writer.add_outline_item("PG-13 Forms", page_count, parent=parent_bookmark)
                    for f in sorted(pg13_files):
                        reader = PdfReader(os.path.join(SEA_PAY_PG13_FOLDER, f))
                        match = re.search(r'PG13__(.+?)__\d', f)
                        ship_name = match.group(1).replace("_", " ") if match else f
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
