import threading
import time
import os
import io
import zipfile
import shutil

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
)
from .processing import process_all
import app.core.rates as rates


bp = Blueprint("routes", __name__)


@bp.route("/", methods=["GET"])
def home():
    return render_template(
        "index.html",
        template_path=TEMPLATE,
        rate_path=RATE_FILE,
    )


@bp.route("/process", methods=["POST"])
def process_route():
    clear_logs()
    reset_progress()
    log("=== PROCESS STARTED ===")

    # Save uploaded TORIS PDFs
    files = request.files.getlist("files")
    for f in files:
        if f.filename:
            save_path = os.path.join(DATA_DIR, f.filename)
            f.save(save_path)
            log(f"SAVED INPUT FILE → {save_path}")

    # Save template
    template_file = request.files.get("template_file")
    if template_file and template_file.filename:
        template_file.save(TEMPLATE)
        log(f"UPDATED TEMPLATE → {TEMPLATE}")

    # Save CSV
    rate_file = request.files.get("rate_file")
    if rate_file and rate_file.filename:
        rate_file.save(RATE_FILE)
        log(f"UPDATED CSV FILE → {RATE_FILE}")

        # Reload CSV (patched)
        try:
            rates.load_rates(RATE_FILE)
            log("RATES RELOADED FROM CSV")
        except Exception as e:
            log(f"CSV RELOAD ERROR → {e}")

    strike_color = request.form.get("strike_color", "black")

    def _run():
        try:
            process_all(strike_color=strike_color)
        except Exception as e:
            log(f"PROCESS ERROR → {e}")
            set_progress(status="error", current_step="Processing error")

    worker = threading.Thread(target=_run, daemon=True)
    worker.start()

    return jsonify({"status": "STARTED"})


@bp.route("/logs")
def get_logs():
    return "\n".join(LIVE_LOGS)


@bp.route("/progress")
def progress_route():
    return jsonify(get_progress())


@bp.route("/stream")
def stream_logs():
    def event_stream():
        last_len = 0
        while True:
            current = "\n".join(LIVE_LOGS)
            if len(current) != last_len:
                last_len = len(current)
                yield f"data: {current}\n\n"
            time.sleep(1)

    return Response(event_stream(), mimetype="text/event-stream")


@bp.route("/download_merged")
def download_merged():
    merged_files = [
        f
        for f in os.listdir(PACKAGE_FOLDER)
        if f.startswith("MERGED_") and f.endswith(".pdf")
    ]

    if not merged_files:
        return "No merged files found.", 404

    latest = max(
        merged_files,
        key=lambda f: os.path.getmtime(os.path.join(PACKAGE_FOLDER, f)),
    )

    return send_from_directory(
        PACKAGE_FOLDER,
        latest,
        as_attachment=True,
    )


@bp.route("/download_summary")
def download_summary():
    mem_zip = io.BytesIO()
    with zipfile.ZipFile(mem_zip, "w", zipfile.ZIP_DEFLATED) as z:
        # TXT
        if os.path.exists(SUMMARY_TXT_FOLDER):
            for root, _, files in os.walk(SUMMARY_TXT_FOLDER):
                for f in files:
                    full = os.path.join(root, f)
                    arc = os.path.relpath(full, SUMMARY_TXT_FOLDER)
                    z.write(full, f"SUMMARY_TXT/{arc}")

        # PDF
        if os.path.exists(SUMMARY_PDF_FOLDER):
            for root, _, files in os.walk(SUMMARY_PDF_FOLDER):
                for f in files:
                    full = os.path.join(root, f)
                    arc = os.path.relpath(full, SUMMARY_PDF_FOLDER)
                    z.write(full, f"SUMMARY_PDF/{arc}")

    mem_zip.seek(0)
    return send_file(
        mem_zip,
        as_attachment=True,
        download_name="SUMMARY_BUNDLE.zip",
    )


@bp.route("/download_marked_sheets")
def download_marked_sheets():
    mem_zip = io.BytesIO()

    with zipfile.ZipFile(mem_zip, "w", zipfile.ZIP_DEFLATED) as z:
        if os.path.exists(TORIS_CERT_FOLDER):
            for root, _, files in os.walk(TORIS_CERT_FOLDER):
                for f in files:
                    full = os.path.join(root, f)
                    arc = os.path.relpath(full, TORIS_CERT_FOLDER)
                    z.write(full, f"TORIS_MARKED/{arc}")

    mem_zip.seek(0)
    return send_file(
        mem_zip,
        as_attachment=True,
        download_name="TORIS_MARKED_SHEETS.zip",
    )


@bp.route("/download_tracking")
def download_tracking():
    tracker_folder = os.path.join(OUTPUT_DIR, "TRACKER")
    mem_zip = io.BytesIO()

    with zipfile.ZipFile(mem_zip, "w", zipfile.ZIP_DEFLATED) as z:
        if os.path.exists(tracker_folder):
            for root, _, files in os.walk(tracker_folder):
                for f in files:
                    full = os.path.join(root, f)
                    arc = os.path.relpath(full, tracker_folder)
                    z.write(full, f"TRACKER/{arc}")

    mem_zip.seek(0)
    return send_file(mem_zip, as_attachment=True, download_name="TRACKER.zip")


@bp.route("/download_all")
def download_all():
    mem_zip = io.BytesIO()
    with zipfile.ZipFile(mem_zip, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _, files in os.walk(OUTPUT_DIR):
            for f in files:
                full = os.path.join(root, f)
                arc = os.path.relpath(full, OUTPUT_DIR)
                z.write(full, arc)

    mem_zip.seek(0)
    return send_file(mem_zip, as_attachment=True, download_name="ALL_OUTPUT.zip")


@bp.route("/reset", methods=["POST"])
def reset_all():
    # Wipe /data/
    for root, dirs, files in os.walk(DATA_DIR):
        for f in files:
            try:
                os.remove(os.path.join(root, f))
            except Exception:
                pass
        for d in dirs:
            try:
                shutil.rmtree(os.path.join(root, d), ignore_errors=True)
            except Exception:
                pass

    # Wipe /output/
    for root, dirs, files in os.walk(OUTPUT_DIR):
        for f in files:
            try:
                os.remove(os.path.join(root, f))
            except Exception:
                pass
        for d in dirs:
            try:
                shutil.rmtree(os.path.join(root, d), ignore_errors=True)
            except Exception:
                pass

    clear_logs()

    # PATCH: UI expects "message"
    return jsonify(
        {
            "message": "Reset complete",
            "status": "reset",
        }
    )
