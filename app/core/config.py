import os

# -----------------------------------
# DIRECTORY ROOTS
# -----------------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))                # /app/app/core
PROJECT_ROOT = os.path.abspath(os.path.join(BASE_DIR, "..", ".."))  # repo root: ATGSD-SEA-PAY-PROCESSOR

# -----------------------------------
# TEMPLATE / CORE FILES
# -----------------------------------

# NAVPERS 1070/613 template PDF (used by PG-13 / 1070 generation)
TEMPLATE = os.path.join(PROJECT_ROOT, "pdf_template", "NAVPERS_1070_613_TEMPLATE.pdf")

# CSV roster file (RATE, LAST NAME, FIRST NAME, MIDDLE INITIAL)
RATE_FILE = os.path.join(PROJECT_ROOT, "config", "atgsd_n811.csv")

# Ships list text file (used by ships.py for ship matching)
SHIP_FILE = os.path.join(PROJECT_ROOT, "ships.txt")

# -----------------------------------
# OUTPUT ROOT & LEGACY SUBFOLDERS
# (these keep existing behavior working)
# -----------------------------------

OUTPUT_DIR = os.path.join(PROJECT_ROOT, "output")

# Final packaged ZIP / combined outputs
PACKAGE_FOLDER = os.path.join(OUTPUT_DIR, "PACKAGE")

# Summary TXT and PDF folders
SUMMARY_TXT_FOLDER = os.path.join(OUTPUT_DIR, "SUMMARY_TXT")
SUMMARY_PDF_FOLDER = os.path.join(OUTPUT_DIR, "SUMMARY_PDF")

# TORIS marked Sea Duty Certification Sheets
TORIS_CERT_FOLDER = os.path.join(OUTPUT_DIR, "TORIS_CERT")

# PG-13 outputs (per ship)
SEA_PAY_PG13_FOLDER = os.path.join(OUTPUT_DIR, "SEA_PAY_PG13")

# Optional tracker folder if you use it elsewhere
TRACKER_FOLDER = os.path.join(OUTPUT_DIR, "TRACKER")

# -----------------------------------
# NEW DATA MODEL / JSON OUTPUT FOLDERS
# -----------------------------------

# Root structured data directory
DATA_DIR = os.path.join(PROJECT_ROOT, "data")

# Parsed sheets (future use if we split parsing into JSON files)
PARSED_DIR = os.path.join(DATA_DIR, "parsed")

# Manual overrides per member/sheet (future phase)
OVERRIDES_DIR = os.path.join(DATA_DIR, "overrides")

# Validation reports (per member or global)
REPORTS_DIR = os.path.join(DATA_DIR, "reports")

# Preview artifacts (PG-13 text, 1070/613 drafts, etc.)
PREVIEWS_DIR = os.path.join(DATA_DIR, "previews")

# Phase 2–3 review JSON (per member, per sheet, per event)
REVIEW_JSON_PATH = os.path.join(OUTPUT_DIR, "SEA_PAY_REVIEW.json")

# -----------------------------------
# FONT SETTINGS (used by pdf_writer / fill_1070)
# -----------------------------------

FONT_NAME = "Times-Roman"
FONT_SIZE = 12

# -----------------------------------
# ENSURE DIRECTORIES EXIST
# -----------------------------------
for p in [
    OUTPUT_DIR,
    PACKAGE_FOLDER,
    SUMMARY_TXT_FOLDER,
    SUMMARY_PDF_FOLDER,
    TORIS_CERT_FOLDER,
    SEA_PAY_PG13_FOLDER,
    TRACKER_FOLDER,
    DATA_DIR,
    PARSED_DIR,
    OVERRIDES_DIR,
    REPORTS_DIR,
    PREVIEWS_DIR,
]:
    os.makedirs(p, exist_ok=True)
