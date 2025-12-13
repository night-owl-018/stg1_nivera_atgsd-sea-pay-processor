import threading
import time
import os
import io
import zipfile
import shutil
import json

from flask import (
    Blueprint,
    render_template,
    request,
    jsonify,
    send_file,
    send_from_directory,
    Response,
)

from .core.logger import (
    LIVE_LOGS,
    log,
    clear_logs,
    get_progress,
    reset_progress,
    set_progress,
)
from .core.config import (
    DATA_DIR,
    OUTPUT_DIR,
    TEMPLATE,
    RATE_FILE,
    PACKAGE_FOLDER,
    SUMMARY_TXT_FOLDER,
    SUMMARY_PDF_FOLDER,
    TORIS_CERT_FOLDER,
    REVIEW_JSON_PATH,
    OVERRIDES_DIR,
)

from .processing import process_all

from app.core.overrides import (
    save_override,
    clear_overrides,
    apply_overrides,
)

bp = Blueprint("bp", __name__, template_folder="web/frontend", static_folder="web/frontend")


# ---------------------------------------------------------
# FRONTEND ROUTES
# ---------------------------------------------------------

@bp.route("/")
def index():
    return send_from_directory(bp.static_folder, "index.html")


@bp.route("/icon.png")
def icon():
    return send_from_directory(bp.static_folder, "icon.png")


# ---------------------------------------------------------
# PROGRESS / LOG ROUTES
# ---------------------------------------------------------

@bp.route("/progress")
def progress():
    return jsonify(get_progress())


@bp.route("/logs")
def logs():
    return jsonify({"logs": LIVE_LOGS})


@bp.route("/clear_logs", methods=["POST"])
def clear_logs_route():
    clear_logs()
    return jsonify({"status": "cleared"})


# ---------------------------------------------------------
# PROCESS ROUTES
# ---------------------------------------------------------

_process_thread = None


@bp.route("/process", methods=["POST"])
def start_process():
    global _process_thread

    if _process_thread and _process_thread.is_alive():
        return jsonify({"error": "Process already running"}), 400

    reset_progress()
    clear_logs()

    # Save uploaded files to /app/data
    uploaded_files = request.files.getlist("files")
    if not uploaded_files:
        return jsonify({"error": "No files uploaded"}), 400

    os.makedirs(DATA_DIR, exist_ok=True)

    saved_paths = []
    for f in uploaded_files:
        if not f.filename:
            continue
        dest = os.path.join(DATA_DIR, f.filename)
        f.save(dest)
        saved_paths.append(dest)
        log(f"SAVED INPUT FILE → {dest}")

    # Optional: upload updated template
    template_file = request.files.get("template")
    if template_file and template_file.filename:
        os.makedirs(os.path.dirname(TEMPLATE), exist_ok=True)
        template_file.save(TEMPLATE)
        log(f"UPDATED TEMPLATE → {TEMPLATE}")

    # Optional: upload updated CSV
    rate_csv = request.files.get("csv")
    if rate_csv and rate_csv.filename:
        os.makedirs(os.path.dirname(RATE_FILE), exist_ok=True)
        rate_csv.save(RATE_FILE)
        log(f"UPDATED CSV FILE → {RATE_FILE}")

    def _run():
        try:
            set_progress(stage="running", pct=0, msg="Processing...")
            process_all(saved_paths)
            set_progress(stage="done", pct=100, msg="Complete")
        except Exception as e:
            log(f"ERROR → {str(e)}")
            set_progress(stage="error", pct=0, msg=str(e))

    _process_thread = threading.Thread(target=_run, daemon=True)
    _process_thread.start()

    return jsonify({"status": "started"})


# ---------------------------------------------------------
# REVIEW / OVERRIDE ROUTES
# ---------------------------------------------------------

def _load_review_state():
    if not os.path.exists(REVIEW_JSON_PATH):
        return {}
    try:
        with open(REVIEW_JSON_PATH, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


@bp.route("/api/members")
def api_members():
    state = _load_review_state()
    return jsonify(sorted(state.keys()))


@bp.route("/api/member/<path:member_key>/sheets")
def api_member_sheets(member_key):
    """Return a simple list of sheet source filenames for the selected member.

    The UI expects: ["FILE1.pdf", "FILE2.pdf", ...]
    """
    state = _load_review_state()
    member = state.get(member_key)
    if not member:
        return jsonify([])

    # Apply overrides on-the-fly so the UI always reflects the current override file.
    member = apply_overrides(member_key, member)

    sheet_ids = []
    for s in member.get("sheets", []):
        sf = s.get("source_file")
        if sf and sf not in sheet_ids:
            sheet_ids.append(sf)

    return jsonify(sheet_ids)


@bp.route("/api/member/<path:member_key>/sheet/<path:sheet_file>")
def api_member_sheet(member_key, sheet_file):
    """Return the full sheet payload (valid_rows + invalid_events) for one sheet."""
    state = _load_review_state()
    member = state.get(member_key)
    if not member:
        return jsonify({"valid_rows": [], "invalid_events": []})

    member = apply_overrides(member_key, member)

    for s in member.get("sheets", []):
        if s.get("source_file") == sheet_file:
            # Keep the keys the frontend expects.
            return jsonify({
                "member_key": member_key,
                "sheet_file": sheet_file,
                "valid_rows": s.get("rows", []),
                "invalid_events": s.get("invalid_events", []),
                "meta": s.get("meta", {}),
            })

    return jsonify({"valid_rows": [], "invalid_events": []})


@bp.route("/api/override", methods=["POST"])
def api_override_save():
    payload = request.get_json(silent=True) or {}
    save_override(**payload)
    state = _load_review_state()
    state[payload["member_key"]] = apply_overrides(
        payload["member_key"],
        state[payload["member_key"]],
    )
    os.makedirs(os.path.dirname(REVIEW_JSON_PATH), exist_ok=True)
    with open(REVIEW_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, default=str)
    return jsonify({"status": "override_saved"})


@bp.route("/api/override", methods=["DELETE"])
def api_override_clear():
    """Delete overrides.

    - If only member_key is provided: clear ALL overrides for that member.
    - If member_key + sheet_file + event_index are provided: delete ONLY that one override.
    """
    payload = request.get_json(silent=True) or {}
    member_key = payload.get("member_key")
    if not member_key:
        return jsonify({"error": "member_key is required"}), 400

    sheet_file = payload.get("sheet_file")
    event_index = payload.get("event_index")

    # Helper: same safe naming as overrides.py
    def _override_path_local(mk: str) -> str:
        safe = mk.replace(" ", "_").replace(",", "_")
        return os.path.join(OVERRIDES_DIR, f"{safe}.json")

    if sheet_file is None or event_index is None:
        # Clear all overrides for this member
        clear_overrides(member_key)
    else:
        # Remove one specific override entry
        path = _override_path_local(member_key)
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f) or {}
            except Exception:
                data = {}

            ovs = data.get("overrides", [])
            new_ovs = []
            for ov in ovs:
                if ov.get("sheet_file") == sheet_file and ov.get("event_index") == event_index:
                    continue
                new_ovs.append(ov)
            data["overrides"] = new_ovs

            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, default=str)

    # Re-apply overrides to review state and write it back so the UI stays in sync.
    state = _load_review_state()
    if member_key in state:
        state[member_key] = apply_overrides(member_key, state[member_key])
        os.makedirs(os.path.dirname(REVIEW_JSON_PATH), exist_ok=True)
        with open(REVIEW_JSON_PATH, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, default=str)

    return jsonify({"status": "override_deleted"})


# ---------------------------------------------------------
# DOWNLOAD & RESET ROUTES (UNCHANGED)
# ---------------------------------------------------------

@bp.route("/download_merged")
def download_merged():
    path = os.path.join(PACKAGE_FOLDER, "MERGED_SEA_PAY_PG13.pdf")
    if not os.path.exists(path):
        return jsonify({"error": "Merged file not found"}), 404
    return send_file(path, as_attachment=True)


@bp.route("/download_package")
def download_package():
    if not os.path.exists(PACKAGE_FOLDER):
        return jsonify({"error": "Package folder not found"}), 404

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _, files in os.walk(PACKAGE_FOLDER):
            for f in files:
                full = os.path.join(root, f)
                rel = os.path.relpath(full, PACKAGE_FOLDER)
                z.write(full, rel)

    buf.seek(0)
    return send_file(buf, mimetype="application/zip", as_attachment=True, download_name="SEA_PAY_PACKAGE.zip")


@bp.route("/reset", methods=["POST"])
def reset():
    # Clear output folders only
    try:
        for folder in [
            OUTPUT_DIR,
            PACKAGE_FOLDER,
            SUMMARY_TXT_FOLDER,
            SUMMARY_PDF_FOLDER,
            TORIS_CERT_FOLDER,
        ]:
            if os.path.exists(folder):
                shutil.rmtree(folder, ignore_errors=True)
            os.makedirs(folder, exist_ok=True)

        # Keep DATA_DIR but clear old PDFs
        if os.path.exists(DATA_DIR):
            for f in os.listdir(DATA_DIR):
                fp = os.path.join(DATA_DIR, f)
                if os.path.isfile(fp) and fp.lower().endswith(".pdf"):
                    os.remove(fp)

        reset_progress()
        clear_logs()
        return jsonify({"status": "reset_complete"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
