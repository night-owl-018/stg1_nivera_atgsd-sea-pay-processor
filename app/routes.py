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
    OVERRIDES_DIR,  # 🔹 PATCH: Import OVERRIDES_DIR
)

from app.processing import process_all
import app.core.rates as rates
from app.core.overrides import (
    save_override,
    clear_overrides,
    apply_overrides,
    load_overrides,  # 🔹 PATCH: Import load_overrides
)

from app.processing import rebuild_outputs_from_review
from app.core.merge import merge_all_pdfs

bp = Blueprint("routes", __name__)
FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "web", "frontend")


# 🔹 --- START OF PATCH --- 🔹

def _get_override_path(member_key):
    """
    Local copy of private function from overrides.py to ensure stable path generation.
    Convert 'STG1 NIVERA,RYAN' → 'STG1_NIVERA_RYAN.json'
    """
    safe = member_key.replace(" ", "_").replace(",", "_")
    return os.path.join(OVERRIDES_DIR, f"{safe}.json")


def _delete_single_override(member_key, sheet_file, event_index):
    """
    Deletes a single override entry for a specific event. This is a helper
    for the batch endpoint and fixes the bug where the old DELETE endpoint
    cleared all overrides for a member instead of just one.
    """
    path = _get_override_path(member_key)
    if not os.path.exists(path):
        return

    data = load_overrides(member_key)
    overrides = data.get("overrides", [])
    original_count = len(overrides)

    # Filter out the override to be deleted
    data["overrides"] = [
        ov for ov in overrides
        if not (ov.get("sheet_file") == sheet_file and ov.get("event_index") == event_index)
    ]

    # If the file is now empty, delete it. Otherwise, write the updated list.
    if not data["overrides"]:
        clear_overrides(member_key)
    elif len(data["overrides"]) < original_count:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)


def _norm_status(v):
    """
    Only allow UI dropdown values:
      "" | "valid" | "invalid"
    Anything else becomes "" (Auto).
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

# 🔹 --- END OF PATCH --- 🔹


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

            # This patch is from your original code, it is preserved
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
    """
    Load the ORIGINAL review state (before any overrides).
    """
    original_path = REVIEW_JSON_PATH.replace('.json', '_ORIGINAL.json')

    if os.path.exists(original_path):
        try:
            with open(original_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            log(
                f"CRITICAL: Could not load '{original_path}'. "
                f"This file is the required source of truth. Falling back to '{REVIEW_JSON_PATH}', "
                f"but the state may be inconsistent. Error: {e}"
            )

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
    """
    Load a single sheet with overrides applied.
    """
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


# 🔹 --- START OF PATCH --- 🔹

@bp.route("/api/overrides/batch", methods=["POST"])
def api_override_batch():
    """
    Receives a list of override changes and applies them in a single batch.
    Correctly saves status + reason, and correctly deletes ONLY one override.
    """
    payload_list = request.get_json(silent=True) or []
    if not isinstance(payload_list, list):
        return jsonify({"error": "Request payload must be a list of override objects"}), 400

    affected_members = set()

    for payload in payload_list:
        member_key = (payload.get("member_key") or "").strip()
        sheet_file = (payload.get("sheet_file") or "").strip()
        event_index = _to_int(payload.get("event_index"), default=None)

        if not member_key or not sheet_file or event_index is None:
            # Skip bad entries instead of corrupting override files
            continue

        affected_members.add(member_key)

        status = _norm_status(payload.get("status"))
        reason = (payload.get("reason") or "").strip()
        source = payload.get("source", "manual")

        # Only delete if BOTH are empty (Auto + empty reason)
        if status == "" and reason == "":
            _delete_single_override(
                member_key=member_key,
                sheet_file=sheet_file,
                event_index=event_index,
            )
        else:
            save_override(
                member_key=member_key,
                sheet_file=sheet_file,
                event_index=event_index,
                status=status or None,   # "" stays Auto
                reason=reason,           # Reason saved
                source=source,
            )

    # Rebuild applied review state so rebuild_outputs uses current overrides
    if affected_members:
        state = _load_review()
        for mk in affected_members:
            if mk in state:
                state[mk] = apply_overrides(mk, state[mk])
        _write_review(state)

    return jsonify({"status": "batch processed"})

# 🔹 --- END OF PATCH --- 🔹


# NOTE: The following single-override endpoints are kept for backwards compatibility
# but are no longer used by the patched frontend.

@bp.route("/api/override", methods=["POST"])
def api_override():
    """
    Save a single override and regenerate review state.
    PATCH: this used to call save_override(**payload) which is WRONG for your overrides.py signature.
    """
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
    """
    Clear overrides for a member and regenerate review state.
    """
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


@bp.route("/reset", methods=["POST"])
def reset():
    """
    Reset all data including original backup.
    """
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
