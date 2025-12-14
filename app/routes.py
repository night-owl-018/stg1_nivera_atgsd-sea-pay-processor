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
# UI ROUTE (UNCHANGED)
# =========================================================

@bp.route("/", methods=["GET"])
def home():
    return send_from_directory(
        os.path.join(os.path.dirname(__file__), "web", "frontend"),
        "index.html",
    )

# =========================================================
# PROCESS ROUTE (UNCHANGED LOGIC)
# =========================================================

@bp.route("/process", methods=["POST"])
def process_route():
    clear_logs()
    reset_progress()
    log("=== PROCESS STARTED ===")

    set_progress(status="PROCESSING", percent=0)

    files = request.files.getlist("files") or request.files.getlist("pdfs")
    for f in files:
        if f and f.filename:
            path = os.path.join(DATA_DIR, f.filename)
            f.save(path)
            log(f"SAVED INPUT FILE → {path}")

    template_file = request.files.get("template_pdf")
    if template_file and template_file.filename:
        template_file.save(TEMPLATE)
        log(f"UPDATED TEMPLATE → {TEMPLATE}")

    rate_file = request.files.get("rates_csv")
    if rate_file and rate_file.filename:
        rate_file.save(RATE_FILE)
        rates.load_rates()
        log("RATES RELOADED")

    strike_color = request.form.get("strikeout_color", "Black")

    def _run():
        try:
            process_all(strike_color=strike_color)
            set_progress(status="COMPLETE", percent=100)
            log("PROCESS COMPLETE")
        except Exception as e:
            log(f"PROCESS ERROR → {e}")
            set_progress(status="ERROR", percent=0)

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"status": "STARTED"})

# =========================================================
# PROGRESS (FIXED — NO NEW IMPORTS)
# =========================================================

@bp.route("/progress")
def progress_route():
    p = get_progress() or {}

    logs = p.get("log", [])
    if isinstance(logs, list):
        logs = "\n".join(logs)

    return jsonify({
        "status": p.get("status", "Idle"),
        "percent": p.get("percent", 0),
        "log": logs,
    })

# =========================================================
# REVIEW / OVERRIDE (PATCH 2 — SINGLE SHEET)
# =========================================================

def _load_review_state():
    if not os.path.exists(REVIEW_JSON_PATH):
        return {}
    with open(REVIEW_JSON_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

@bp.route("/api/members")
def api_members():
    return jsonify(sorted(_load_review_state().keys()))

@bp.route("/api/member/<path:member_key>/sheets")
def api_member_sheets(member_key):
    state = _load_review_state()
    member = state.get(member_key)
    if not member:
        return jsonify([])

    return jsonify([
        sheet.get("source_file")
        for sheet in member.get("sheets", [])
        if sheet.get("source_file")
    ])

@bp.route("/api/member/<path:member_key>/sheet/<path:sheet_id>")
def api_single_sheet(member_key, sheet_id):
    state = _load_review_state()
    member = state.get(member_key)
    if not member:
        return jsonify({}), 404

    for sheet in member.get("sheets", []):
        if sheet.get("source_file") == sheet_id:
            return jsonify({
                "valid_rows": sheet.get("rows", []),
                "invalid_events": sheet.get("invalid_events", []),
            })

    return jsonify({}), 404

@bp.route("/api/override", methods=["POST"])
def api_override_save():
    payload = request.get_json(silent=True) or {}
    save_override(**payload)

    state = _load_review_state()
    state[payload["member_key"]] = apply_overrides(
        payload["member_key"],
        state[payload["member_key"]],
    )

    with open(REVIEW_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)

    return jsonify({"status": "override_saved"})

@bp.route("/api/override", methods=["DELETE"])
def api_override_clear():
    payload = request.get_json(silent=True) or {}
    clear_overrides(payload["member_key"])
    return jsonify({"status": "cleared"})

# =========================================================
# DOWNLOAD / RESET (UNCHANGED)
# =========================================================

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
def reset_all():
    shutil.rmtree(DATA_DIR, ignore_errors=True)
    shutil.rmtree(OUTPUT_DIR, ignore_errors=True)
    clear_logs()
    return jsonify({"status": "reset"})
