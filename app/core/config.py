import os

# -----------------------------------
# DIRECTORY ROOTS
# -----------------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))                # /app/app/core
PROJECT_ROOT = os.path.abspath(os.path.join(BASE_DIR, "..", ".."))  # repo root

# -----------------------------------
# DOCKER-AWARE PATHS (explicit mounts)
# -----------------------------------

TEMPLATE_DIR = "/app/pdf_template"
CONFIG_DIR = "/app/config"
DATA_DIR = "/app/data"

# Output directory (Docker-mapped)
OUTPUT_DIR = "/app/output"

# -----------------------------------
# TEMPLATE / CORE FILES
# -----------------------------------

TEMPLATE = os.path.join(TEMPLATE_DIR, "NAVPERS_1070_613_TEMPLATE.pdf")
RATE_FILE = os.path.join(CONFIG_DIR, "atgsd_n811.csv")
SHIP_FILE = os.path.join(PROJECT_ROOT, "ships.txt")

# -----------------------------------
# OUTPUT SUBFOLDERS
# -----------------------------------

PACKAGE_FOLDER = os.path.join(OUTPUT_DIR, "PACKAGE")
SUMMARY_TXT_FOLDER = os.path.join(OUTPUT_DIR, "SUMMARY_TXT")
SUMMARY_PDF_FOLDER = os.path.join(OUTPUT_DIR, "SUMMARY_PDF")
TORIS_CERT_FOLDER = os.path.join(OUTPUT_DIR, "TORIS_CERT")
SEA_PAY_PG13_FOLDER = os.path.join(OUTPUT_DIR, "SEA_PAY_PG13")
TRACKER_FOLDER = os.path.join(OUTPUT_DIR, "TRACKER")

# -----------------------------------
# REVIEW / OVERRIDE OUTPUTS
# -----------------------------------

PARSED_DIR = os.path.join(OUTPUT_DIR, "parsed")
OVERRIDES_DIR = os.path.join(OUTPUT_DIR, "overrides")
REPORTS_DIR = os.path.join(OUTPUT_DIR, "reports")
PREVIEWS_DIR = os.path.join(OUTPUT_DIR, "previews")

REVIEW_JSON_PATH = os.path.join(OUTPUT_DIR, "SEA_PAY_REVIEW.json")

# -----------------------------------
# FONT SETTINGS
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
