import os

# -----------------------------------
# DIRECTORY ROOTS
# -----------------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))          # /app/core
PROJECT_ROOT = os.path.abspath(os.path.join(BASE_DIR, "..", ".."))  # repo root

# -----------------------------------
# REQUIRED ORIGINAL PATHS (UI + ROUTES)
# -----------------------------------

# Template for PG-13 merge and TORIS
TEMPLATE = os.path.join(PROJECT_ROOT, "pdf_template", "NAVPERS_1070_613_TEMPLATE.pdf")

# Output root
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "output")

# Subfolders required by route logic
PACKAGE_FOLDER = os.path.join(OUTPUT_DIR, "PACKAGE")
SUMMARY_TXT_FOLDER = os.path.join(OUTPUT_DIR, "SUMMARY_TXT")
SUMMARY_PDF_FOLDER = os.path.join(OUTPUT_DIR, "SUMMARY_PDF")
TORIS_CERT_FOLDER = os.path.join(OUTPUT_DIR, "TORIS_CERT")
SEA_PAY_PG13_FOLDER = os.path.join(OUTPUT_DIR, "SEA_PAY_PG13")

# -----------------------------------
# CSV FILE
# -----------------------------------
RATE_FILE = os.path.join(PROJECT_ROOT, "config", "atgsd_n811.csv")

# -----------------------------------
# NEW — DATA MODEL + JSON OUTPUT
# -----------------------------------

DATA_DIR = os.path.join(PROJECT_ROOT, "data")
PARSED_DIR = os.path.join(DATA_DIR, "parsed")
OVERRIDES_DIR = os.path.join(DATA_DIR, "overrides")
REPORTS_DIR = os.path.join(DATA_DIR, "reports")
PREVIEWS_DIR = os.path.join(DATA_DIR, "previews")

REVIEW_JSON_PATH = os.path.join(OUTPUT_DIR, "SEA_PAY_REVIEW.json")

# -----------------------------------
# FONT SETTINGS (used by pdf_writer)
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
    DATA_DIR,
    PARSED_DIR,
    OVERRIDES_DIR,
    REPORTS_DIR,
    PREVIEWS_DIR,
]:
    os.makedirs(p, exist_ok=True)
