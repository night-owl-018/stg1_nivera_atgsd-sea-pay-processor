import os
import tempfile
import zipfile
from flask import Blueprint, render_template, request, send_from_directory, jsonify

# Correct imports (relative)
from .core.logger import LIVE_LOGS, log, clear_logs
from .core.config import DATA_DIR, OUTPUT_DIR, TEMPLATE, RATE_FILE
from .processing import process_all
import app.core.rates as rates  # keep as-is for correct loading

bp = Blueprint("main", __name__)

# ------------------------------------------------
# HOME PAGE
# ------------------------------------------------
@bp.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        strike_color = request.form.get("strike_color", "black")

        # Main PDFs
        for f in request.files.getlist("files"):
            if f.filename:
                f.save(os.path.join(DATA_DIR, f.filename))

        # Template override
        tpl = request.files.get("template_file")
        if tpl and tpl.filename:
            tpl.save(TEMPLATE)

        # Rates CSV upload
        csvf = request.files.get("rate_file")
        if csvf and csvf.filename:
            csvf.save(RATE_FILE)

            # Reload CSV
            rates.RATES = rates.load_rates()
            rates.CSV_IDENTITIES.clear()

            # Normalize identities
            for key, rate in rates.RATES.items():
                last, first = key.split(",", 1)

                def normalize_for_id(text):
                    import re
                    t = re.sub(r"\(.*?\)", "", text.upper())
                    t = re.sub(r"[^A-Z ]", "", t)
                    return " ".join(t.split())

                full_norm = normalize_for_id(f"{first} {last}")
                rates.CSV_IDENTITIES.append((full_norm, rate, last, first))

        # Run processor
        process_all(strike_color=strike_color)

    return render_template(
        "index.html",
        logs="\n".join(LIVE_LOGS),
        template_path=TEMPLATE,
        rate_path=RATE_FILE,
    )

# ------------------------------------------------
# LIVE LOGS
# ------------------------------------------------
@bp.route("/logs")
def get_logs():
    return "\n".join(LIVE_LOGS)

# ------------------------------------------------
# DOWNLOAD ALL OUTPUT
# ------------------------------------------------
@bp.route("/download_all")
def download_all():
    zip_path = os.path.join(tempfile.gettempdir(), "SeaPay_Output.zip")

    if os.path.exists(zip_path):
        os.remove(zip_path)

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for f in os.listdir(OUTPUT_DIR):
            full = os.path.join(OUTPUT_DIR, f)
            if os.path.isfile(full):
                z.write(full, arcname=f)

    return send_from_directory(
        os.path.dirname(zip_path),
        os.path.basename(zip_path),
        as_attachment=True,
        download_name="SeaPay_Output.zip"
    )

# ------------------------------------------------
# DOWNLOAD MASTER MERGED PDF
# ------------------------------------------------
@bp.route("/download_merged")
def download_merged():
    merged_files = sorted(
        f for f in os.listdir(OUTPUT_DIR)
        if f.startswith("MERGED_SeaPay_Forms_")
    )

    if not merged_files:
        return "No merged PDF available. Run the processor first.", 404

    latest = merged_files[-1]

    return send_from_directory(
        OUTPUT_DIR,
        latest,
        as_attachment=True
    )

# ------------------------------------------------
# DOWNLOAD SUMMARY TEXT FILES
# ------------------------------------------------
@bp.route("/download_summary")
def download_summary():
    summary_dir = os.path.join(OUTPUT_DIR, "summary")
    zip_path = os.path.join(tempfile.gettempdir(), "SeaPay_Summaries.zip")

    if os.path.exists(zip_path):
        os.remove(zip_path)

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        if os.path.exists(summary_dir):
            for f in os.listdir(summary_dir):
                full = os.path.join(summary_dir, f)
                if os.path.isfile(full):
                    z.write(full, arcname=f)

    return send_from_directory(
        os.path.dirname(zip_path),
        os.path.basename(zip_path),
        as_attachment=True,
        download_name="SeaPay_Summaries.zip"
    )

# ------------------------------------------------
# DOWNLOAD MARKED STRIKEOUT SHEETS
# ------------------------------------------------
@bp.route("/download_marked_sheets")
def download_marked_sheets():
    marked_dir = os.path.join(OUTPUT_DIR, "marked_sheets")
    zip_path = os.path.join(tempfile.gettempdir(), "Marked_Sheets.zip")

    if os.path.exists(zip_path):
        os.remove(zip_path)

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        if os.path.exists(marked_dir):
            for f in os.listdir(marked_dir):
                full = os.path.join(marked_dir, f)
                if os.path.isfile(full):
                    z.write(full, arcname=f)

    return send_from_directory(
        os.path.dirname(zip_path),
        os.path.basename(zip_path),
        as_attachment=True,
        download_name="Marked_Sheets.zip"
    )

# ------------------------------------------------
# DOWNLOAD VALIDATION REPORTS + LEDGER
# ------------------------------------------------
@bp.route("/download_validation")
def download_validation():
    validation_dir = os.path.join(OUTPUT_DIR, "validation")
    zip_path = os.path.join(tempfile.gettempdir(), "Validation_Reports.zip")

    if os.path.exists(zip_path):
        os.remove(zip_path)

    ledger_files = ["VALIDATION_LEDGER.txt", "VALIDATION_LEDGER.pdf"]

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:

        # Add all validation files
        if os.path.exists(validation_dir):
            for f in os.listdir(validation_dir):
                full = os.path.join(validation_dir, f)
                if os.path.isfile(full):
                    z.write(full, arcname=f)

            # Explicitly ensure ledger is included
            for lf in ledger_files:
                lf_path = os.path.join(validation_dir, lf)
                if os.path.exists(lf_path):
                    z.write(lf_path, arcname=lf)

    return send_from_directory(
        os.path.dirname(zip_path),
        os.path.basename(zip_path),
        as_attachment=True,
        download_name="Validation_Reports.zip"
    )

# ------------------------------------------------
# DOWNLOAD TRACKING PACKAGE (JSON + CSV + VALIDATION + LEDGER)
# ------------------------------------------------
@bp.route("/download_tracking")
def download_tracking():
    validation_dir = os.path.join(OUTPUT_DIR, "validation")
    tracking_dir = os.path.join(OUTPUT_DIR, "tracking")

    zip_path = os.path.join(tempfile.gettempdir(), "SeaPay_Tracking_Package.zip")

    if os.path.exists(zip_path):
        os.remove(zip_path)

    ledger_files = ["VALIDATION_LEDGER.txt", "VALIDATION_LEDGER.pdf"]

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:

        # ALL validation files
        if os.path.exists(validation_dir):
            for f in os.listdir(validation_dir):
                full = os.path.join(validation_dir, f)
                if os.path.isfile(full):
                    z.write(full, arcname=f"validation/{f}")

            # Explicit ledger include
            for lf in ledger_files:
                lf_path = os.path.join(validation_dir, lf)
                if os.path.exists(lf_path):
                    z.write(lf_path, arcname=f"validation/{lf}")

        # Tracking JSON / CSV
        if os.path.exists(tracking_dir):
            for f in os.listdir(tracking_dir):
                full = os.path.join(tracking_dir, f)
                if os.path.isfile(full):
                    z.write(full, arcname=f"tracking/{f}")

    return send_from_directory(
        os.path.dirname(zip_path),
        os.path.basename(zip_path),
        as_attachment=True,
        download_name="SeaPay_Tracking_Package.zip"
    )

# ------------------------------------------------
# RESET
# ------------------------------------------------
from .core.cleanup import cleanup_all_folders

@bp.route("/reset", methods=["POST"])
def reset():
    deleted = cleanup_all_folders()
    clear_logs()
    return jsonify({"status": "success", "message": f"Reset complete! {deleted} files deleted."})
