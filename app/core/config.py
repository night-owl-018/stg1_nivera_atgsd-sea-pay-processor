import os

# ------------------------------------------------
# PATH CONFIG
# ------------------------------------------------

DATA_DIR = "/data"
OUTPUT_DIR = "/output"
TEMPLATE_DIR = "/templates"
CONFIG_DIR = "/config"

# Core files
TEMPLATE = os.path.join(TEMPLATE_DIR, "NAVPERS_1070_613_TEMPLATE.pdf")
RATE_FILE = os.path.join(CONFIG_DIR, "atgsd_n811.csv")
SHIP_FILE = "/app/ships.txt"

# Structured output folders
SEA_PAY_PG13_FOLDER = os.path.join(OUTPUT_DIR, "SEA_PAY_PG13")
TORIS_CERT_FOLDER = os.path.join(OUTPUT_DIR, "TORIS_SEA_PAY_CERT_SHEET")
SUMMARY_TXT_FOLDER = os.path.join(OUTPUT_DIR, "SUMMARY_TXT")
SUMMARY_PDF_FOLDER = os.path.join(OUTPUT_DIR, "SUMMARY_PDF")
TRACKER_FOLDER = os.path.join(OUTPUT_DIR, "TRACKER")
PACKAGE_FOLDER = os.path.join(OUTPUT_DIR, "PACKAGE")


def init_output_folders():
    """
    Ensure all expected output folders exist.
    This keeps behavior predictable even on a fresh container.
    """
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(TEMPLATE_DIR, exist_ok=True)
    os.makedirs(CONFIG_DIR, exist_ok=True)

    for folder in (
        SEA_PAY_PG13_FOLDER,
        TORIS_CERT_FOLDER,
        SUMMARY_TXT_FOLDER,
        SUMMARY_PDF_FOLDER,
        TRACKER_FOLDER,
        PACKAGE_FOLDER,
    ):
        os.makedirs(folder, exist_ok=True)


# Run once on import so CLI / Flask both get a sane layout
init_output_folders()

# ------------------------------------------------
# FONT CONFIG
# ------------------------------------------------

FONT_NAME = "Times-Roman"
FONT_SIZE = 10


