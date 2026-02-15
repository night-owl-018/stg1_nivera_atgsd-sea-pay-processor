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
# SIGNATURE MANAGEMENT FUNCTIONS (PER-MEMBER, NO REUSE)
# -----------------------------------

def load_signatures():
    """
    Load signature library + per-member assignments.

    signatures.json format (v2):
      {
        "version": 2,
        "signatures": [ {id, name, role, created, device_id, device_name, image_base64, thumbnail_base64, metadata}, ... ],
        "assignments_by_member": {
           "<member_key>": {
              "toris_certifying_officer": "<sig_id>|null",
              "pg13_certifying_official": "<sig_id>|null",
              "pg13_verifying_official": "<sig_id>|null"
           },
           ...
        },
        "assignment_rules": {
           "prevent_duplicate_per_member": true,
           "prevent_reuse_across_members": true
        }
      }

    Legacy (v1) files with "assignments" will be migrated on load.
    """
    print(f"🔄 load_signatures() called")
    print(f"📁 SIGNATURES_FILE path: {SIGNATURES_FILE}")
    
    default_data = {
        "version": 2,
        "signatures": [],
        "assignments_by_member": {},
        "assignment_rules": {
            "prevent_duplicate_per_member": True,
            "prevent_reuse_across_members": True,
        },
    }

    # Ensure output directory exists
    output_dir = os.path.dirname(SIGNATURES_FILE)
    print(f"📁 Output directory: {output_dir}")
    print(f"📁 Directory exists: {os.path.exists(output_dir)}")
    
    try:
        os.makedirs(output_dir, exist_ok=True)
        print(f"✅ Ensured output directory exists")
    except Exception as e:
        print(f"❌ ERROR: Could not create output directory: {e}")
        import traceback
        traceback.print_exc()

    # Check if file exists
    file_exists = os.path.exists(SIGNATURES_FILE)
    print(f"📄 signatures.json exists: {file_exists}")
    
    if not file_exists:
        # Create default file on first run
        print(f"📝 Creating default signatures.json file...")
        try:
            _save_signatures_data(default_data)
            print(f"✅ Created new signatures file at {SIGNATURES_FILE}")
        except Exception as e:
            print(f"❌ WARNING: Could not create signatures file: {e}")
            import traceback
            traceback.print_exc()
        return default_data

    # File exists, try to load it
    print(f"📖 Loading existing signatures.json...")
    try:
        with open(SIGNATURES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
        print(f"✅ Loaded signatures.json successfully")
        print(f"📊 Data keys: {list(data.keys())}")
        print(f"📊 Signatures count: {len(data.get('signatures', []))}")
        print(f"📊 Members count: {len(data.get('assignments_by_member', {}))}")
    except Exception as e:
        print(f"❌ ERROR: Could not load signatures: {e}")
        import traceback
        traceback.print_exc()
        return default_data

    # Migrate legacy structure (v1)
    if "assignments_by_member" not in data and "assignments" in data:
        print(f"🔄 Migrating legacy v1 format to v2...")
        # v1 had global assignments; keep them under a pseudo-member key
        legacy_assignments = data.get("assignments") or {}
        data["assignments_by_member"] = {
            "__GLOBAL__": {
                "toris_certifying_officer": legacy_assignments.get("toris_certifying_officer"),
                "pg13_certifying_official": legacy_assignments.get("pg13_certifying_official"),
                "pg13_verifying_official": legacy_assignments.get("pg13_verifying_official"),
            }
        }
        data["version"] = 2
        print(f"✅ Migration complete")

    # Ensure keys exist
    for k, v in default_data.items():
        if k not in data:
            data[k] = v

    # Clean per-member assignment dicts
    for member_key, a in (data.get("assignments_by_member") or {}).items():
        if not isinstance(a, dict):
            data["assignments_by_member"][member_key] = {}
            a = data["assignments_by_member"][member_key]
        for loc in ("toris_certifying_officer", "pg13_certifying_official", "pg13_verifying_official"):
            a.setdefault(loc, None)

    print(f"✅ load_signatures() completed successfully")
    return data


def _save_signatures_data(data):
    print(f"💾 _save_signatures_data() called")
    print(f"📁 Saving to: {SIGNATURES_FILE}")
    print(f"📊 Data to save - signatures: {len(data.get('signatures', []))}, members: {len(data.get('assignments_by_member', {}))}")
    
    try:
        output_dir = os.path.dirname(SIGNATURES_FILE)
        print(f"📁 Ensuring directory exists: {output_dir}")
        os.makedirs(output_dir, exist_ok=True)
        print(f"✅ Directory ensured")
        
        print(f"📝 Writing JSON file...")
        with open(SIGNATURES_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        print(f"✅ Signatures saved to {SIGNATURES_FILE}")
        
        # Verify file was written
        if os.path.exists(SIGNATURES_FILE):
            file_size = os.path.getsize(SIGNATURES_FILE)
            print(f"✅ File verified, size: {file_size} bytes")
        else:
            print(f"❌ WARNING: File was not created!")
            
    except PermissionError as e:
        print(f"❌ PERMISSION ERROR: Cannot write to {SIGNATURES_FILE}")
        print(f"   Details: {e}")
        print(f"   Check Docker volume mounts and directory permissions")
        import traceback
        traceback.print_exc()
        raise
    except Exception as e:
        print(f"❌ ERROR saving signatures: {e}")
        import traceback
        traceback.print_exc()
        raise


def save_signature(name, role, image_base64, device_id=None, device_name=None):
    """
    Save a new signature to the library.
    Returns: signature_id or None on error.
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

        orig_w, orig_h = img.size

        thumb = img.copy()
        thumb.thumbnail((150, 50), Image.Resampling.LANCZOS)

        thumb_buffer = BytesIO()
        thumb.save(thumb_buffer, format="PNG")
        thumb_base64 = base64.b64encode(thumb_buffer.getvalue()).decode("utf-8")

        metadata = {"width": orig_w, "height": orig_h, "format": "PNG"}
    except Exception as e:
        print(f"Warning: Could not generate thumbnail: {e}")
        thumb_base64 = image_base64
        metadata = {}

    new_signature = {
        "id": sig_id,
        "name": name.strip(),
        "role": role.strip(),
        "created": datetime.now().isoformat(),
        "device_id": device_id or "unknown",
        "device_name": device_name or "Unknown Device",
        "image_base64": image_base64.strip(),
        "thumbnail_base64": thumb_base64,
        "metadata": metadata,
    }

    data["signatures"].append(new_signature)

    try:
        _save_signatures_data(data)
        return sig_id
    except Exception as e:
        print(f"Error: Could not save signature: {e}")
        return None


def _active_locations():
    return ("toris_certifying_officer", "pg13_certifying_official", "pg13_verifying_official")


def _ensure_member(data, member_key):
    if not member_key:
        member_key = "__UNKNOWN__"
    if member_key not in data["assignments_by_member"]:
        data["assignments_by_member"][member_key] = {loc: None for loc in _active_locations()}
    else:
        for loc in _active_locations():
            data["assignments_by_member"][member_key].setdefault(loc, None)
    return member_key


def _all_assigned_signature_ids(data, exclude_member=None, exclude_location=None):
    used = set()
    for mkey, a in (data.get("assignments_by_member") or {}).items():
        for loc in _active_locations():
            sid = a.get(loc)
            if not sid:
                continue
            if exclude_member is not None and mkey == exclude_member and (exclude_location is None or loc == exclude_location):
                continue
            used.add(sid)
    return used


def validate_member_assignments(data, member_key):
    """Validate that a member does not reuse the same signature across their 3 locations."""
    rules = data.get("assignment_rules") or {}
    if not rules.get("prevent_duplicate_per_member", True):
        return True, None

    a = (data.get("assignments_by_member") or {}).get(member_key) or {}
    assigned = [a.get(loc) for loc in _active_locations() if a.get(loc)]
    if len(assigned) != len(set(assigned)):
        return False, "Cannot reuse the same signature for multiple locations for the same member"
    return True, None


def validate_global_reuse(data, member_key, location, signature_id):
    """Validate that a signature is not reused across members/locations."""
    rules = data.get("assignment_rules") or {}
    if not rules.get("prevent_reuse_across_members", True):
        return True, None

    if not signature_id:
        return True, None

    used_elsewhere = _all_assigned_signature_ids(data, exclude_member=member_key, exclude_location=location)
    if signature_id in used_elsewhere:
        return False, "This signature is already assigned to another member. Each signature can only be used once."
    return True, None


def assign_signature(member_key, location, signature_id):
    """
    Assign a signature to a specific member + location.

    Enforces:
      - within the member: all 3 locations must be different (if enabled)
      - across members: a signature ID cannot be reused anywhere (if enabled)
    """
    valid_locations = list(_active_locations())
    if location not in valid_locations:
        return False, f"Invalid location. Must be one of: {valid_locations}"

    data = load_signatures()
    member_key = _ensure_member(data, member_key)

    # Verify signature exists if not None
    if signature_id is not None:
        sig_exists = any(s.get("id") == signature_id for s in data.get("signatures", []))
        if not sig_exists:
            return False, f"Signature ID {signature_id} not found"

    # Set and validate
    data["assignments_by_member"][member_key][location] = signature_id

    ok, msg = validate_member_assignments(data, member_key)
    if not ok:
        # rollback
        data["assignments_by_member"][member_key][location] = None
        return False, msg

    ok, msg = validate_global_reuse(data, member_key, location, signature_id)
    if not ok:
        data["assignments_by_member"][member_key][location] = None
        return False, msg

    try:
        _save_signatures_data(data)
        return True, "Assignment successful"
    except Exception as e:
        return False, f"Error saving assignment: {e}"


def get_signature_for_member_location(member_key, location):
    """Return PIL Image for a member + location, or None."""
    import base64
    from io import BytesIO
    from PIL import Image

    data = load_signatures()
    member_key = member_key or "__UNKNOWN__"
    a = (data.get("assignments_by_member") or {}).get(member_key) or {}
    sig_id = a.get(location)

    if not sig_id:
        return None

    signature = next((s for s in data.get("signatures", []) if s.get("id") == sig_id), None)
    if not signature:
        return None

    try:
        img_data = base64.b64decode(signature["image_base64"])
        return Image.open(BytesIO(img_data))
    except Exception as e:
        print(f"Error loading signature image: {e}")
        return None


def get_all_signatures(include_thumbnails=False):
    data = load_signatures()
    result = []
    for s in data.get("signatures", []):
        sig_info = {
            "id": s.get("id"),
            "name": s.get("name", ""),
            "role": s.get("role", ""),
            "created": s.get("created", ""),
            "device_name": s.get("device_name", "Unknown"),
            "metadata": s.get("metadata", {}),
        }
        if include_thumbnails:
            sig_info["thumbnail_base64"] = s.get("thumbnail_base64", "")
        result.append(sig_info)
    return result


def delete_signature(signature_id):
    """Delete a signature and clear any member assignments using it."""
    data = load_signatures()
    data["signatures"] = [s for s in data.get("signatures", []) if s.get("id") != signature_id]

    for mkey, a in (data.get("assignments_by_member") or {}).items():
        for loc in _active_locations():
            if a.get(loc) == signature_id:
                a[loc] = None

    try:
        _save_signatures_data(data)
        return True
    except Exception as e:
        print(f"Error: Could not delete signature: {e}")
        return False


def auto_assign_signatures(member_key):
    """
    Auto-assign the first available unused signatures to this member's unassigned locations.
    Respects global no-reuse rule.
    """
    data = load_signatures()
    member_key = _ensure_member(data, member_key)

    sigs = [s.get("id") for s in data.get("signatures", []) if s.get("id")]
    if not sigs:
        return False, "No signatures available to assign", {}

    assignments = data["assignments_by_member"][member_key]
    unassigned = [loc for loc in _active_locations() if assignments.get(loc) is None]
    if not unassigned:
        return True, "All locations already have signatures assigned", {}

    used_elsewhere = _all_assigned_signature_ids(data, exclude_member=member_key)
    # also exclude signatures already used by this member
    used_by_member = set(v for v in assignments.values() if v)
    used = used_elsewhere | used_by_member

    available = [sid for sid in sigs if sid not in used]
    if not available:
        return False, "No unused signatures available (all are already assigned)", {}

    made = {}
    for i, loc in enumerate(unassigned):
        if i >= len(available):
            break
        sid = available[i]
        assignments[loc] = sid
        made[loc] = sid

    # validate per-member again
    ok, msg = validate_member_assignments(data, member_key)
    if not ok:
        # rollback those we set
        for loc in made:
            assignments[loc] = None
        return False, msg, {}

    try:
        _save_signatures_data(data)
        return True, f"Auto-assigned {len(made)} signature(s) for {member_key}", made
    except Exception as e:
        return False, f"Error saving assignments: {e}", {}


def get_assignment_status(member_key=None):
    """
    If member_key provided: status for that member.
    Else: global summary.
    """
    data = load_signatures()
    members = data.get("assignments_by_member") or {}

    def status_for(mkey):
        a = members.get(mkey) or {loc: None for loc in _active_locations()}
        issues = []
        for loc in _active_locations():
            if not a.get(loc):
                issues.append(f"{loc} has no signature assigned")
        return {
            "member_key": mkey,
            "assigned": {loc: a.get(loc) for loc in _active_locations()},
            "issues": issues,
        }

    if member_key:
        return status_for(member_key)

    # global
    used = _all_assigned_signature_ids(data)
    return {
        "total_signatures": len(data.get("signatures", [])),
        "total_members_with_assignments": len(members),
        "total_assigned": len(used),
        "members": [status_for(m) for m in sorted(members.keys())],
    }
