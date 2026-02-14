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
# NEW: SIGNATURE STORAGE
# -----------------------------------

SIGNATURES_FILE = os.path.join(OUTPUT_DIR, "signatures.json")

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

FONT_NAME = "TimesNewRoman"
FONT_SIZE = 11

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
                'date_yyyymmdd': data.get('date_yyyymmdd', '').strip(),
            }
    except Exception as e:
        print(f"Warning: Could not load certifying officer info: {e}")
        return {}


def save_certifying_officer(rate, last_name, first_name, middle_name, date_yyyymmdd=""):
    """
    Save certifying officer information to JSON file.
    """
    data = {
        'rate': rate.strip(),
        'last_name': last_name.strip(),
        'first_name': first_name.strip(),
        'middle_name': middle_name.strip(),
        'date_yyyymmdd': (date_yyyymmdd or '').strip(),
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

def get_certifying_date_yyyymmdd():
    """
    Get certifier DATE as YYYYMMDD for PG-13.
    Returns "" if not set or invalid.
    """
    officer = load_certifying_officer()
    d = (officer.get("date_yyyymmdd") or "").strip()
    if d and len(d) == 8 and d.isdigit():
        return d
    return ""

# -----------------------------------
# SIGNATURE MANAGEMENT FUNCTIONS
# -----------------------------------

def load_signatures():
    """Load all signatures and assignments with validation."""
    import uuid
    from datetime import datetime
    
    default_data = {
        'signatures': [],
        'assignments': {
            'toris_certifying_officer': None,
            'pg13_certifying_official': None,
            'pg13_member': None
        },
        'assignment_rules': {
            'prevent_duplicate_per_document': True,
            'auto_rotate_signatures': False
        }
    }
    
    if not os.path.exists(SIGNATURES_FILE):
        return default_data
    
    try:
        with open(SIGNATURES_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            
        # Merge with defaults to ensure all keys exist
        for key in default_data:
            if key not in data:
                data[key] = default_data[key]
        
        return data
    except Exception as e:
        print(f"Warning: Could not load signatures: {e}")
        return default_data


def save_signature(name, role, image_base64, device_id=None, device_name=None):
    """
    Save a new signature to the library.
    Automatically generates thumbnail for fast loading.
    
    Returns: signature_id or None on error
    """
    import uuid
    from datetime import datetime
    from PIL import Image
    from io import BytesIO
    import base64
    
    data = load_signatures()
    
    sig_id = f"sig_{uuid.uuid4().hex[:8]}"
    
    # Generate thumbnail (150x50 preview)
    try:
        img_data = base64.b64decode(image_base64)
        img = Image.open(BytesIO(img_data))
        
        # Get original dimensions
        orig_w, orig_h = img.size
        
        # Create thumbnail
        thumb = img.copy()
        thumb.thumbnail((150, 50), Image.Resampling.LANCZOS)
        
        thumb_buffer = BytesIO()
        thumb.save(thumb_buffer, format='PNG')
        thumb_base64 = base64.b64encode(thumb_buffer.getvalue()).decode('utf-8')
        
        metadata = {
            'width': orig_w,
            'height': orig_h,
            'format': 'PNG'
        }
    except Exception as e:
        print(f"Warning: Could not generate thumbnail: {e}")
        thumb_base64 = image_base64
        metadata = {}
    
    new_signature = {
        'id': sig_id,
        'name': name.strip(),
        'role': role.strip(),
        'created': datetime.now().isoformat(),
        'device_id': device_id or 'unknown',
        'device_name': device_name or 'Unknown Device',
        'image_base64': image_base64.strip(),
        'thumbnail_base64': thumb_base64,
        'metadata': metadata
    }
    
    data['signatures'].append(new_signature)
    
    try:
        with open(SIGNATURES_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
        return sig_id
    except Exception as e:
        print(f"Error: Could not save signature: {e}")
        return None


def validate_assignments(assignments):
    """
    Validate signature assignments to prevent duplicates.
    
    Returns: (is_valid, error_message)
    """
    prevent_duplicate = load_signatures().get('assignment_rules', {}).get('prevent_duplicate_per_document', True)
    
    if not prevent_duplicate:
        return True, None
    
    # Get non-None assignment values
    assigned_sigs = [v for v in assignments.values() if v is not None]
    
    # Check for duplicates
    if len(assigned_sigs) != len(set(assigned_sigs)):
        return False, "Cannot use the same signature for multiple locations on one document"
    
    return True, None


def assign_signature(location, signature_id):
    """
    Assign a signature to a specific document location with validation.
    
    Args:
        location: One of 'toris_certifying_officer', 'pg13_certifying_official', 'pg13_member'
        signature_id: The ID of the signature to assign, or None to clear
    
    Returns: (success, message)
    """
    valid_locations = ['toris_certifying_officer', 'pg13_certifying_official', 'pg13_member']
    
    if location not in valid_locations:
        return False, f"Invalid location. Must be one of: {valid_locations}"
    
    data = load_signatures()
    
    # Verify signature exists if not None
    if signature_id is not None:
        sig_exists = any(s['id'] == signature_id for s in data['signatures'])
        if not sig_exists:
            return False, f"Signature ID {signature_id} not found"
    
    # Create temporary assignments for validation
    temp_assignments = data['assignments'].copy()
    temp_assignments[location] = signature_id
    
    # Validate assignments
    is_valid, error_msg = validate_assignments(temp_assignments)
    if not is_valid:
        return False, error_msg
    
    # Apply assignment
    data['assignments'][location] = signature_id
    
    try:
        with open(SIGNATURES_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
        return True, "Assignment successful"
    except Exception as e:
        return False, f"Error saving assignment: {e}"


def get_signature_for_location(location):
    """
    Get the PIL Image for a specific document location.
    Returns None if no signature assigned or signature not found.
    """
    import base64
    from io import BytesIO
    from PIL import Image
    
    data = load_signatures()
    sig_id = data['assignments'].get(location)
    
    if not sig_id:
        return None
    
    # Find signature by ID
    signature = next((s for s in data['signatures'] if s['id'] == sig_id), None)
    
    if not signature:
        return None
    
    try:
        img_data = base64.b64decode(signature['image_base64'])
        return Image.open(BytesIO(img_data))
    except Exception as e:
        print(f"Error loading signature image: {e}")
        return None


def get_all_signatures(include_thumbnails=False):
    """
    Get list of all saved signatures with metadata.
    
    Args:
        include_thumbnails: If True, include thumbnail_base64 in response
    
    Returns: List of signature dictionaries
    """
    data = load_signatures()
    
    result = []
    for s in data['signatures']:
        sig_info = {
            'id': s['id'],
            'name': s['name'],
            'role': s['role'],
            'created': s['created'],
            'device_name': s.get('device_name', 'Unknown'),
            'metadata': s.get('metadata', {})
        }
        
        if include_thumbnails:
            sig_info['thumbnail_base64'] = s.get('thumbnail_base64', '')
        
        result.append(sig_info)
    
    return result


def delete_signature(signature_id):
    """
    Delete a signature from the library.
    Also clears any assignments using this signature.
    """
    data = load_signatures()
    
    # Remove from signatures list
    data['signatures'] = [s for s in data['signatures'] if s['id'] != signature_id]
    
    # Clear any assignments using this signature
    for location, assigned_id in data['assignments'].items():
        if assigned_id == signature_id:
            data['assignments'][location] = None
    
    try:
        with open(SIGNATURES_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
        return True
    except Exception as e:
        print(f"Error: Could not delete signature: {e}")
        return False


def auto_assign_signatures():
    """
    Automatically assign available signatures to document locations.
    Smart algorithm prevents duplicate assignments.
    
    Returns: (success, message, assignments_made)
    """
    data = load_signatures()
    signatures = data['signatures']
    assignments = data['assignments']
    
    if len(signatures) == 0:
        return False, "No signatures available to assign", {}
    
    # Locations to fill
    locations = ['toris_certifying_officer', 'pg13_certifying_official', 'pg13_member']
    
    # Get currently unassigned locations
    unassigned = [loc for loc in locations if assignments.get(loc) is None]
    
    if len(unassigned) == 0:
        return True, "All locations already have signatures assigned", {}
    
    # Get currently assigned signature IDs
    already_assigned = set(v for v in assignments.values() if v is not None)
    
    # Get available signatures (not yet assigned)
    available_sigs = [s['id'] for s in signatures if s['id'] not in already_assigned]
    
    if len(available_sigs) == 0:
        return False, "No additional signatures available (all are already assigned)", {}
    
    # Assign signatures to unassigned locations
    assignments_made = {}
    for i, location in enumerate(unassigned):
        if i < len(available_sigs):
            sig_id = available_sigs[i]
            success, msg = assign_signature(location, sig_id)
            if success:
                assignments_made[location] = sig_id
    
    if len(assignments_made) > 0:
        return True, f"Auto-assigned {len(assignments_made)} signature(s)", assignments_made
    else:
        return False, "Could not auto-assign signatures", {}


def get_assignment_status():
    """
    Get detailed status of signature assignments.
    
    Returns: Dictionary with assignment status and recommendations
    """
    data = load_signatures()
    signatures = data['signatures']
    assignments = data['assignments']
    
    status = {
        'total_signatures': len(signatures),
        'assignments': {},
        'issues': [],
        'recommendations': []
    }
    
    locations = ['toris_certifying_officer', 'pg13_certifying_official', 'pg13_member']
    location_labels = {
        'toris_certifying_officer': 'TORIS Certifying Officer',
        'pg13_certifying_official': 'PG-13 Certifying Official (Top)',
        'pg13_member': 'PG-13 Member Signature (Bottom)'
    }
    
    for location in locations:
        sig_id = assignments.get(location)
        
        if sig_id is None:
            status['assignments'][location] = {
                'label': location_labels[location],
                'status': 'unassigned',
                'signature': None
            }
            status['issues'].append(f"{location_labels[location]} has no signature assigned")
        else:
            sig = next((s for s in signatures if s['id'] == sig_id), None)
            if sig:
                status['assignments'][location] = {
                    'label': location_labels[location],
                    'status': 'assigned',
                    'signature': {
                        'id': sig['id'],
                        'name': sig['name'],
                        'role': sig.get('role', '')
                    }
                }
            else:
                status['assignments'][location] = {
                    'label': location_labels[location],
                    'status': 'error',
                    'signature': None
                }
                status['issues'].append(f"{location_labels[location]} references non-existent signature {sig_id}")
    
    # Check for duplicate assignments
    assigned_ids = [assignments.get(loc) for loc in locations if assignments.get(loc)]
    if len(assigned_ids) != len(set(assigned_ids)):
        status['issues'].append("Warning: Same signature used multiple times")
    
    # Recommendations
    if len(signatures) == 0:
        status['recommendations'].append("Create at least one signature to get started")
    elif len(assigned_ids) == 0:
        status['recommendations'].append("Assign signatures to document locations")
    elif len(assigned_ids) < 3:
        status['recommendations'].append(f"Create {3 - len(assigned_ids)} more signature(s) to assign to all locations")
    
    return status


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
