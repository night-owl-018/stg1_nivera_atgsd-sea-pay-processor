import os
import json

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
# NEW: CERTIFYING OFFICER CONFIG
# -----------------------------------

CERTIFYING_OFFICER_FILE = os.path.join(OUTPUT_DIR, "certifying_officer.json")

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
# CERTIFYING OFFICER HELPER FUNCTIONS
# -----------------------------------

def load_certifying_officer():
    """
    Load certifying officer information from JSON file.
    Returns dict with keys: rate, last_name, first_name, middle_name
    Returns empty dict if file doesn't exist or can't be read.
    """
    if not os.path.exists(CERTIFYING_OFFICER_FILE):
        return {}

    try:
        with open(CERTIFYING_OFFICER_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return {
                'rate': data.get('rate', '').strip(),
                'last_name': data.get('last_name', '').strip(),
                'first_name': data.get('first_name', '').strip(),
                'middle_name': data.get('middle_name', '').strip(),
            }
    except Exception as e:
        print(f"Warning: Could not load certifying officer info: {e}")
        return {}


def save_certifying_officer(rate, last_name, first_name, middle_name):
    """
    Save certifying officer information to JSON file.
    """
    data = {
        'rate': rate.strip(),
        'last_name': last_name.strip(),
        'first_name': first_name.strip(),
        'middle_name': middle_name.strip(),
    }

    try:
        with open(CERTIFYING_OFFICER_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
        return True
    except Exception as e:
        print(f"Error: Could not save certifying officer info: {e}")
        return False


def get_certifying_officer_name():
    """
    Get formatted certifying officer name for display on TORIS forms.
    Returns formatted name or empty string if not set.
    Format: "LAST_NAME, FULL_FIRST_NAME M." (e.g., "NIVERA, RYAN N.")
    NOTE: No rate prefix, full first name, middle initial only
    """
    officer = load_certifying_officer()
    if not officer or not officer.get('last_name'):
        return ""
    
    # Build name as: LASTNAME, FIRSTNAME M.
    name_parts = [officer['last_name']]
    
    if officer.get('first_name'):
        # Use FULL first name (not just initial)
        first_name = officer['first_name'].upper()
        
        if officer.get('middle_name'):
            # Add middle initial with period
            middle_initial = officer['middle_name'][0].upper()
            name_parts.append(f"{first_name} {middle_initial}.")
        else:
            name_parts.append(first_name)
    
    return ", ".join(name_parts)


def get_certifying_officer_name_pg13():
    """
    Get formatted certifying officer name for display on PG-13 forms.
    Returns formatted name or empty string if not set.
    Format: "F. M. LAST_NAME" (e.g., "R. N. NIVERA")
    """
    officer = load_certifying_officer()
    if not officer or not officer.get('last_name'):
        return ""
    
    parts = []
    if officer.get('first_name'):
        # Take first letter of first name
        first_initial = officer['first_name'][0].upper()
        parts.append(f"{first_initial}.")
    
    if officer.get('middle_name'):
        # Take first letter of middle name
        middle_initial = officer['middle_name'][0].upper()
        parts.append(f"{middle_initial}.")
    
    if officer.get('last_name'):
        parts.append(officer['last_name'])
    
    return " ".join(parts)


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
