import os
import tempfile
import zipfile

from flask import (
    Blueprint,
    render_template,
    request,
    send_file,
    send_from_directory,
    redirect,
    url_for,
)

from werkzeug.utils import secure_filename

from app.core.logger import LIVE_LOGS, log
from app.core.config import (
    DATA_DIR,
    OUTPUT_DIR,
    TEMPLATE,
    RATE_FILE,
    SEA_PAY_PG13_FOLDER,
    TORIS_CERT_FOLDER,
    SUMMARY_TXT_FOLDER,
    SUMMARY_PDF_FOLDER,
    TRACKER_FOLDER,
    PACKAGE_FOLDER,
)
from app.core import rates
from app.processing import process_all

bp = Blueprint("main", __name__)


# ---------------------------------------------------------
# HOME + UPLOAD
# ---------------------------------------------------------
@bp.route("/", methods=["GET", "POST"])
def index():
    """
    Main page:
      - GET: render UI
      - POST: handle file uploads
    """
    os.makedirs(DATA_DIR, exist_ok=True)

    status_message = None

    if request.method == "POST":
        # Handle PDF uploads
        if "files" not in request.files:
            status_message = "No files part in the request."
        else:
            files = request.files.getlist("files")
            saved_count = 0

            for f in files:
                if not f or not f.filename:
                    continue
                filename = secure_filename(f.filename)
                dest = os.path.join(DATA_DIR, filename)
                f.save(dest)
                saved_count += 1
                log(f"UPLOADED → {filename}")

            status_message = f"{saved_count} file(s) uploaded."

        return redirect(url_for("main.index"))

    # GET
    return render_template(
        "index.html",
        logs="\n".join(LIVE_LOGS),
        template_path=TEMPLATE if os.path.exists(TEMPLATE) else "",
        rate_path=RATE_FILE if os.path.exists(RATE_FILE) else "",
        status_message=status_message,
    )


# ---------------------------------------------------------
# LIVE LOG STREAM
# ---------------------------------------------------------
@bp.route("/logs")
def logs():
    """
    Return last N log lines for the frontend log window.
    This version handles both list and deque safely.
    """
    try:
        safe_logs = list(LIVE_LOGS)
        return "\n".join(safe_logs[-500:])
    except Exception as e:
        return f"LOG ERROR: {e}", 500


# ---------------------------------------------------------
# PROCESS BUTTON
# ---------------------------------------------------------
@bp.route("/process", methods=["POST"])
def process():
    """
    Kick off main processing pipeline.
    """
    strike_color = request.form.get("strikeColor", "black")
    log(f"PROCESS REQUESTED → strike_color={strike_color}")
    process_all(strike_color=strike_color)
    return "Processing complete."


# ---------------------------------------------------------
# MERGED SEA PAY PG13
# ---------------------------------------------------------
@bp.route("/download_merged")
def download_merged():
    """
    Download merged PG13 set.
    """
    merged_path = os.path.join(PACKAGE_FOLDER, "MERGED_SEA_PAY_PG13.pdf")

    if not os.path.exists(merged_path):
        return "Merged Sea Pay PG13 PDF not found. Run the processor first.", 404

    return send_from_directory(
        os.path.dirname(merged_path),
        os.path.basename(merged_path),
        as_attachment=True,
        download_name=os.path.basename(merged_path),
    )


# ---------------------------------------------------------
# MERGED SUMMARY
# ---------------------------------------------------------
@bp.route("/download_summary")
def download_summary():
    """
    Download merged SUMMARY PDF.
    """
    merged_summary = os.path.join(PACKAGE_FOLDER, "MERGED_SUMMARY.pdf")

    if not os.path.exists(merged_summary):
        return "Merged Summary PDF not found. Run the processor first.", 404

    return send_from_directory(
        os.path.dirname(merged_summary),
        os.path.basename(merged_summary),
        as_attachment=True,
        download_name=os.path.basename(merged_summary),
    )


# ---------------------------------------------------------
# TORIS SEA PAY CERT SHEETS
# ---------------------------------------------------------
@bp.route("/download_marked_sheets")
def download_marked_sheets():
    """
    Zip all TORIS strikeout PDFs.
    """
    if not os.path.isdir(TORIS_CERT_FOLDER):
        return "No TORIS Sea Pay Cert Sheets found.", 404

    pdfs = [
        f for f in os.listdir(TORIS_CERT_FOLDER)
        if f.lower().endswith(".pdf")
    ]
    if not pdfs:
        return "No TORIS Sea Pay Cert Sheets found.", 404

    tmp_zip = os.path.join(tempfile.gettempdir(), "TORIS_Sea_Pay_Cert_Sheets.zip")
    if os.path.exists(tmp_zip):
        os.remove(tmp_zip)

    with zipfile.ZipFile(tmp_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for fn in pdfs:
            full_path = os.path.join(TORIS_CERT_FOLDER, fn)
            zf.write(full_path, arcname=fn)

    return send_file(tmp_zip, as_attachment=True, download_name="TORIS_Sea_Pay_Cert_Sheets.zip")


# ---------------------------------------------------------
# TRACKER FILE
# ---------------------------------------------------------
@bp.route("/download_tracking")
def download_tracking():
    """
    Zip everything in the TRACKER folder.
    """
    if not os.path.isdir(TRACKER_FOLDER):
        return "No tracker files found.", 404

    files = os.listdir(TRACKER_FOLDER)
    if not files:
        return "No tracker files found.", 404

    tmp_zip = os.path.join(tempfile.gettempdir(), "SeaPay_Tracker.zip")
    if os.path.exists(tmp_zip):
        os.remove(tmp_zip)

    with zipfile.ZipFile(tmp_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for fn in files:
            full_path = os.path.join(TRACKER_FOLDER, fn)
            if os.path.isfile(full_path):
                zf.write(full_path, arcname=fn)

    return send_file(tmp_zip, as_attachment=True, download_name="SeaPay_Tracker.zip")


# ---------------------------------------------------------
# EXPORT ZIP
# ---------------------------------------------------------
@bp.route("/download_all")
def download_all():
    """
    Export EVERYTHING under /output as one zip.
    """
    if not os.path.isdir(OUTPUT_DIR):
        return "No output has been generated yet.", 404

    tmp_zip = os.path.join(tempfile.gettempdir(), "SeaPay_Output_All.zip")
    if os.path.exists(tmp_zip):
        os.remove(tmp_zip)

    with zipfile.ZipFile(tmp_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(OUTPUT_DIR):
            for fn in files:
                full_path = os.path.join(root, fn)
                arcname = os.path.relpath(full_path, OUTPUT_DIR)
                zf.write(full_path, arcname=arcname)

    return send_file(tmp_zip, as_attachment=True, download_name="SeaPay_Output_All.zip")


# ---------------------------------------------------------
# RESET BUTTON  (PATCHED)
# ---------------------------------------------------------
@bp.route("/reset", methods=["POST"])
def reset():
    """
    Clear:
      - All uploaded TORIS PDFs in /data
      - All generated files inside specific /output subfolders
      - LIVE_LOGS
    """
    import glob

    # 1. Clear input PDFs
    try:
        if os.path.isdir(DATA_DIR):
            for fp in glob.glob(os.path.join(DATA_DIR, "*")):
                if os.path.isfile(fp):
                    os.remove(fp)
    except Exception as e:
        log(f"RESET ERROR clearing DATA_DIR → {e}")

    # 2. Clear only output subfolder files
    output_folders = [
        PACKAGE_FOLDER,
        SEA_PAY_PG13_FOLDER,
        SUMMARY_PDF_FOLDER,
        SUMMARY_TXT_FOLDER,
        TORIS_CERT_FOLDER,
        TRACKER_FOLDER,
    ]

    for folder in output_folders:
        try:
            if os.path.isdir(folder):
                for fp in glob.glob(os.path.join(folder, "*")):
                    if os.path.isfile(fp):
                        os.remove(fp)
        except Exception as e:
            log(f"RESET ERROR clearing {folder} → {e}")

    # 3. Clear logs
    LIVE_LOGS.clear()
    log("SYSTEM RESET COMPLETE")

    return {"message": "System reset. All generated files cleared."}
