import os
import io
import json
import zipfile
import shutil
import threading

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
)

from app.processing import process_all
import app.core.rates as rates

from app.core.overrides import (
    save_override,
    clear_overrides,
    apply_overrides,
)

# 🔹 PATCH IMPORT (isolated, no refactor)
from app.processing import rebuild_outputs_from_review

bp = Blueprint("routes", __name__)

FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "web", "frontend")


# =========================================================
# UI
# =========================================================

@bp.route("/")
def home():
    return send_from_directory(FRONTEND_DIR, "index.html")


# =========================================================
# PROCESS
# =========================================================

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
            set_progress(status="COMPLETE", percent=100, current_step="Complete")
            log("PROCESS COMPLETE")
        except Exception as e:
            log(f"PROCESS ERROR → {e}")
            set_progress(status="ERROR", percent=0, current_step="Error")

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"status": "STARTED"})


# =========================================================
# 🔹 PATCH: REBUILD OUTPUTS ONLY (NO OCR, NO PARSE)
# =========================================================

@bp.route("/rebuild_outputs", methods=["POST"])
def rebuild_outputs():
    try:
        log("=== REBUILD OUTPUTS STARTED ===")
        rebuild_outputs_from_review()
        log("=== REBUILD OUTPUTS COMPLETE ===")
        return jsonify({"status": "ok"})
    except Exception as e:
        log(f"REBUILD OUTPUTS ERROR → {e}")
        return jsonify({"status": "error", "error": str(e)}), 500


# =========================================================
# PROGRESS (UI-CONTRACT SAFE)
# =========================================================

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


# =========================================================
# REVIEW & OVERRIDE
# =========================================================

def _load_review():
    if not os.path.exists(REVIEW_JSON_PATH):
        return {}
    try:
        with open(REVIEW_JSON_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log(f"REVIEW JSON READ ERROR → {e}")
        return {}


def _write_review(state: dict) -> None:
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
    state = _load_review()
    member = state.get(member_key)
    if not member:
        return jsonify({}), 404

    for sheet in member.get("sheets", []):
        if sheet.get("source_file") == sheet_file:
            return jsonify({
                **sheet,
                "valid_rows": sheet.get("rows", []),
                "invalid_events": sheet.get("invalid_events", []),
            })

    return jsonify({}), 404


@bp.route("/api/override", methods=["POST"])
def api_override():
    payload = request.get_json(silent=True) or {}
    if not payload.get("member_key"):
        return jsonify({"error": "member_key required"}), 400

    save_override(**payload)

    state = _load_review()
    mk = payload["member_key"]
    if mk in state:
        state[mk] = apply_overrides(mk, state[mk])
        _write_review(state)

    return jsonify({"status": "saved"})


@bp.route("/api/override", methods=["DELETE"])
def api_override_clear():
    payload = request.get_json(silent=True) or {}
    mk = payload.get("member_key")
    if not mk:
        return jsonify({"error": "member_key required"}), 400

    clear_overrides(mk)

    state = _load_review()
    if mk in state:
        state[mk] = apply_overrides(mk, state[mk])
        _write_review(state)

    return jsonify({"status": "cleared"})


# =========================================================
# DOWNLOAD / RESET
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

# =========================================================
# 🔹 PATCH: DOWNLOAD MERGED PACKAGE
# =========================================================

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
    return send_file(
        mem,
        as_attachment=True,
        download_name="MERGED_PACKAGE.zip",
    )


@bp.route("/reset", methods=["POST"])
def reset():
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

    clear_logs()
    reset_progress()
    log("RESET COMPLETE (files cleared, folders preserved)")
    return jsonify({"status": "reset"})


