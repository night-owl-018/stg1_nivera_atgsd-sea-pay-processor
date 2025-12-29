import os
import json
from datetime import datetime
from app.core.config import OVERRIDES_DIR


def _override_path(member_key):
    """
    Convert 'STGC MYSLINSKI,SARAH' → 'STGC_MYSLINSKI_SARAH.json'
    """
    safe = member_key.replace(" ", "_").replace(",", "_")
    return os.path.join(OVERRIDES_DIR, f"{safe}.json")


# -----------------------------------------------------------
# LOAD OVERRIDES FOR ONE MEMBER
# -----------------------------------------------------------
def load_overrides(member_key):
    path = _override_path(member_key)
    if not os.path.exists(path):
        return {"overrides": []}

    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"overrides": []}


# -----------------------------------------------------------
# SAVE OVERRIDE ENTRY
# -----------------------------------------------------------
def save_override(member_key, sheet_file, event_index, status, reason, source):
    """
    Save or update an override entry.
    FIX: Replace existing override for same event instead of appending duplicates.
    """
    data = load_overrides(member_key)

    # Create new override entry
    new_override = {
        "sheet_file": sheet_file,
        "event_index": event_index,
        "override_status": status,
        "override_reason": reason,
        "source": source,
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }

    # CRITICAL FIX: Remove any existing override for this exact event
    # Keep only overrides that don't match this sheet_file + event_index
    data["overrides"] = [
        ov for ov in data.get("overrides", [])
        if not (ov.get("sheet_file") == sheet_file and ov.get("event_index") == event_index)
    ]

    # Append the new override
    data["overrides"].append(new_override)

    os.makedirs(OVERRIDES_DIR, exist_ok=True)
    with open(_override_path(member_key), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


# -----------------------------------------------------------
# CLEAR OVERRIDES FOR A MEMBER
# -----------------------------------------------------------
def clear_overrides(member_key):
    path = _override_path(member_key)
    if os.path.exists(path):
        os.remove(path)


# -----------------------------------------------------------
# APPLY OVERRIDES DURING REVIEW MERGE
# -----------------------------------------------------------
def apply_overrides(member_key, review_state_member):
    """
    Mutates review_state_member in-place,
    applying all overrides to rows OR invalid_events.

    Convention:
    - event_index >= 0  → rows[event_index]
    - event_index < 0   → invalid_events[-event_index - 1]
    
    PATCH: Bidirectional movement between valid/invalid arrays
    FIX: Process overrides in order (highest to lowest index) to prevent index shifting bugs
    FIX: Preserve ALL fields when moving between arrays
    FIX: Handle override updates correctly (replace old overrides, not duplicate)
    """

    overrides = load_overrides(member_key).get("overrides", [])
    if not overrides:
        return review_state_member

    # Group overrides by sheet for organized processing
    sheet_overrides = {}
    for ov in overrides:
        sheet_file = ov["sheet_file"]
        if sheet_file not in sheet_overrides:
            sheet_overrides[sheet_file] = {}
        
        # FIX: Use dict keyed by event_index to ensure only ONE override per event
        # This handles cases where user changes their mind (valid→invalid→valid)
        event_idx = ov["event_index"]
        sheet_overrides[sheet_file][event_idx] = ov

    # Process each sheet
    for sheet in review_state_member.get("sheets", []):
        sheet_file = sheet.get("source_file")
        if sheet_file not in sheet_overrides:
            continue

        # Get unique overrides (one per event_index)
        unique_overrides = list(sheet_overrides[sheet_file].values())

        # Separate overrides into categories
        moves_to_invalid = []  # valid → invalid (removes from rows)
        moves_to_valid = []    # invalid → valid (removes from invalid_events)
        in_place_updates = []  # updates without moving

        for ov in unique_overrides:
            idx = ov["event_index"]
            status = ov["override_status"]
            
            if idx >= 0:  # Currently in valid rows
                if status == "invalid":
                    moves_to_invalid.append(ov)
                else:
                    in_place_updates.append(ov)
            else:  # Currently in invalid events
                if status == "valid":
                    moves_to_valid.append(ov)
                else:
                    in_place_updates.append(ov)

        # CRITICAL FIX: Sort to process from highest to lowest index
        # This prevents index shifting from breaking subsequent operations
        moves_to_invalid.sort(key=lambda x: x["event_index"], reverse=True)
        moves_to_valid.sort(key=lambda x: x["event_index"])  # More negative = higher actual index

        # Step 1: Process in-place updates (no array modifications)
        for ov in in_place_updates:
            idx = ov["event_index"]
            status = ov["override_status"]
            reason = ov["override_reason"]
            source = ov.get("source")

            if idx >= 0:  # Valid row
                if idx >= len(sheet.get("rows", [])):
                    continue
                r = sheet["rows"][idx]
            else:  # Invalid event
                invalid_index = -idx - 1
                if invalid_index >= len(sheet.get("invalid_events", [])):
                    continue
                r = sheet["invalid_events"][invalid_index]

            # Update override metadata
            if "override" not in r:
                r["override"] = {}
            r["override"]["status"] = status
            r["override"]["reason"] = reason
            r["override"]["source"] = source
            
            if "final_classification" not in r:
                r["final_classification"] = {}
            r["final_classification"]["is_valid"] = (status == "valid")
            r["final_classification"]["reason"] = reason
            r["final_classification"]["source"] = "override"
            
            if "status" in r:
                r["status"] = status
                r["status_reason"] = reason

        # Step 2: Move valid → invalid (process highest index first)
        for ov in moves_to_invalid:
            idx = ov["event_index"]
            status = ov["override_status"]
            reason = ov["override_reason"]
            source = ov.get("source")

            if idx >= len(sheet.get("rows", [])):
                continue

            r = sheet["rows"][idx]

            # CRITICAL FIX: Copy ALL existing fields, then override specific ones
            new_invalid = dict(r)  # Copy all fields from original row
            
            # Update/add override-specific fields
            new_invalid.update({
                "reason": reason or "Forced invalid by override",
                "category": "override",
                "source": "override",
                "override": {
                    "status": status,
                    "reason": reason,
                    "source": source,
                    "history": r.get("override", {}).get("history", []),
                },
                "final_classification": {
                    "is_valid": False,
                    "reason": reason,
                    "source": "override",
                },
            })
            
            # Ensure status fields are set correctly
            if "status" in new_invalid:
                new_invalid["status"] = "invalid"
                new_invalid["status_reason"] = reason
            
            # Add to invalid_events
            sheet["invalid_events"].append(new_invalid)
            
            # Remove from rows (highest index first = no shifting issues)
            sheet["rows"].pop(idx)

        # Step 3: Move invalid → valid (process highest index first)
        for ov in moves_to_valid:
            idx = ov["event_index"]
            status = ov["override_status"]
            reason = ov["override_reason"]
            source = ov.get("source")

            invalid_index = -idx - 1
            if invalid_index >= len(sheet.get("invalid_events", [])):
                continue

            e = sheet["invalid_events"][invalid_index]

            # CRITICAL FIX: Copy ALL existing fields, then override specific ones
            new_row = dict(e)  # Copy all fields from original invalid event
            
            # Update/add row-specific fields
            new_row.update({
                "status": "valid",
                "status_reason": reason,
                "override": {
                    "status": status,
                    "reason": reason,
                    "source": source,
                    "history": e.get("override", {}).get("history", []),
                },
                "final_classification": {
                    "is_valid": True,
                    "reason": reason,
                    "source": "override",
                },
            })
            
            # Ensure these fields exist (set to defaults if not present)
            if "is_inport" not in new_row:
                new_row["is_inport"] = False
            if "inport_label" not in new_row:
                new_row["inport_label"] = None
            if "is_mission" not in new_row:
                new_row["is_mission"] = False
            if "label" not in new_row:
                new_row["label"] = None
            if "confidence" not in new_row:
                new_row["confidence"] = 1.0
            
            # Add to rows
            sheet["rows"].append(new_row)
            
            # Remove from invalid_events (highest index first = no shifting issues)
            sheet["invalid_events"].pop(invalid_index)

    return review_state_member
