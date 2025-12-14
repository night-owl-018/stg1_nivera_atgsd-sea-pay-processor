import os
import io
import json
import zipfile
import shutil
import threading

from flask import (
    Blueprint,
    request,
    jsonify,
    send_file,
    send_from_directory,
)

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
)

from app.processing import process_all
import app.core.rates as rates

from app.core.overrides import (
    save_override,
    clear_overrides,
    apply_overrides,
)

bp = Blueprint("routes", __name__)


# =========================================================
# UI
# =========================================================

@bp.route("/")
def home():
    return send_from_directory(
        os.path.join(os.path.dirname(__file__), "web", "frontend"),
        "index.html",
    )


# =========================================================
# PROCESS
# =========================================================

@bp.route("/process", methods=["POST"])
def process_route():
    clear_logs()
    reset_progress()

    log("=== PROCESS STARTED ===")
    set_progress(status="PROCESSING", percent=1)

    # Ensure DATA_DIR exists before saving files
    os.makedirs(DATA_DIR, exist_ok=True)

    files = request.files.getlist("files") or request.files.getlist("pdfs")

    for f in files:
        if f and f.filename:
            f.save(os.path.join(DATA_DIR, f.filename))
            log(f"SAVED INPUT FILE → {os.path.join(DATA_DIR, f.filename)}")

    if "template_pdf" in request.files:
        request.files["template_pdf"].save(TEMPLATE)
        log("UPDATED TEMPLATE PDF")

    if "rates_csv" in request.files:
        request.files["rates_csv"].save(RATE_FILE)
        rates.load_rates()
        log("RATES CSV RELOADED")

    strike_color = request.form.get("strikeout_color", "Black")

    def _run():
        try:
            set_progress(percent=10)
            process_all(strike_color=strike_color)
            set_progress(status="COMPLETE", percent=100)
            log("PROCESS COMPLETE")
        except Exception as e:
            log(f"PROCESS ERROR → {e}")
            set_progress(status="ERROR", percent=0)

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"status": "STARTED"})

# =========================================================
# PROGRESS (UI-CONTRACT SAFE)
# =========================================================

@bp.route("/progress")
def progress():
    p = get_progress()
    # Keep response shape flexible for older/newer index.html versions
    return jsonify({
        "status": p.get("status", "IDLE"),
        "percent": p.get("percent", 0),
        "log": "\n".join(p.get("log", [])),
    })


# =========================================================
# REVIEW & OVERRIDE
# =========================================================

def _load_review():
    if not os.path.exists(REVIEW_JSON_PATH):
        return {}
    with open(REVIEW_JSON_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


@bp.route("/api/members")
def api_members():
    return jsonify(sorted(_load_review().keys()))


@bp.route("/api/member/<path:member_key>/sheets")
def api_member_sheets(member_key):
    state = _load_review()
    member = state.get(member_key, {})
    return jsonify([s.get("source_file") for s in member.get("sheets", [])])


@bp.route("/api/member/<path:member_key>/sheet/<path:sheet_id>")
def api_single_sheet(member_key, sheet_id):
    state = _load_review()
    member = state.get(member_key)
    if not member:
        return jsonify({}), 404

    for sheet in member.get("sheets", []):
        if sheet.get("source_file") == sheet_id:
            # Restructure response to match frontend expectations
            return jsonify({
                "source_file": sheet.get("source_file"),
                "reporting_period": sheet.get("reporting_period"),
                "stats": sheet.get("stats"),
                "valid_rows": sheet.get("rows", []),
                "invalid_events": sheet.get("invalid_events", []),
                "parsing_warnings": sheet.get("parsing_warnings", []),
                "parse_confidence": sheet.get("parse_confidence", 1.0)
            })

    return jsonify({}), 404


@bp.route("/api/override", methods=["POST"])
def api_override():
    payload = request.get_json() or {}
    save_override(**payload)

    state = _load_review()
    if payload.get("member_key") in state:
        state[payload["member_key"]] = apply_overrides(
            payload["member_key"],
            state[payload["member_key"]],
        )

        with open(REVIEW_JSON_PATH, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)

    return jsonify({"status": "saved"})


@bp.route("/api/override", methods=["DELETE"])
def api_override_clear():
    payload = request.get_json() or {}
    if payload.get("member_key"):
        clear_overrides(payload["member_key"])
    return jsonify({"status": "cleared"})


# =========================================================
# DOWNLOAD / RESET
# =========================================================

@bp.route("/download_merged")
def download_merged():
    if not os.path.exists(PACKAGE_FOLDER):
        return jsonify({"error": "No merged package found"}), 404
    
    mem = io.BytesIO()
    with zipfile.ZipFile(mem, "w", zipfile.ZIP_DEFLATED) as z:
        for f in os.listdir(PACKAGE_FOLDER):
            full = os.path.join(PACKAGE_FOLDER, f)
            if os.path.isfile(full):
                z.write(full, f)
    mem.seek(0)
    return send_file(mem, as_attachment=True, download_name="MERGED_PACKAGE.zip")


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


@bp.route("/reset", methods=["POST"])
def reset():
    shutil.rmtree(DATA_DIR, ignore_errors=True)
    shutil.rmtree(OUTPUT_DIR, ignore_errors=True)
    
    # Recreate directories after deletion
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    clear_logs()
    reset_progress()
    return jsonify({"status": "reset"})


# =========================================================
# HEALTH CHECK
# =========================================================

@bp.route("/health")
def health():
    """Health check for Docker/Unraid monitoring"""
    return jsonify({
        "status": "healthy",
        "template_exists": os.path.exists(TEMPLATE),
        "rates_exists": os.path.exists(RATE_FILE),
        "data_dir_writable": os.access(DATA_DIR, os.W_OK),
        "output_dir_writable": os.access(OUTPUT_DIR, os.W_OK)
    })
