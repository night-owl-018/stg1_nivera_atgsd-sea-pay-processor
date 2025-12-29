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


def _make_event_signature(event):
    """
    Create a unique signature for an event based on its core content.
    This signature remains stable even when event moves between arrays.
    """
    date = str(event.get("date", ""))
    ship = str(event.get("ship", ""))
    raw = str(event.get("raw", ""))
    occ_idx = str(event.get("occ_idx", ""))
    
    return f"{date}|{ship}|{occ_idx}|{raw}"


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
    Replaces any existing override for the same event.
    """
    data = load_overrides(member_key)

    new_override = {
        "sheet_file": sheet_file,
        "event_index": event_index,
        "override_status": status,
        "override_reason": reason,
        "source": source,
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }

    # Remove any existing override for this event
    data["overrides"] = [
        ov for ov in data.get("overrides", [])
        if not (ov.get("sheet_file") == sheet_file and ov.get("event_index") == event_index)
    ]

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
    Apply overrides by matching events based on their content signature.
    
    CRITICAL FIX: When events move between valid/invalid arrays, their indices change.
    We match by event signature (date|ship|occ_idx|raw) to find them after movement.
    """

    overrides = load_overrides(member_key).get("overrides", [])
    if not overrides:
        return review_state_member

    for sheet in review_state_member.get("sheets", []):
        sheet_file = sheet.get("source_file")
        
        # Filter overrides for this sheet
        sheet_overrides = [ov for ov in overrides if ov.get("sheet_file") == sheet_file]
        if not sheet_overrides:
            continue

        valid_rows = sheet.get("rows", [])
        invalid_events = sheet.get("invalid_events", [])
        
        # Build signature maps for quick lookup
        valid_signatures = {_make_event_signature(row): (row, idx) for idx, row in enumerate(valid_rows)}
        invalid_signatures = {_make_event_signature(event): (event, idx) for idx, event in enumerate(invalid_events)}
        
        # Collect operations to execute after iteration
        moves_to_invalid = []  # (source_index, new_invalid_entry)
        moves_to_valid = []    # (source_index, new_valid_entry)
        
        for ov in sheet_overrides:
            original_idx = ov["event_index"]
            status = ov["override_status"]
            reason = ov["override_reason"]
            source = ov.get("source")

            # STEP 1: Find the event by original index
            target_event = None
            is_in_valid = None
            actual_idx = None
            event_sig = None

            if original_idx >= 0 and original_idx < len(valid_rows):
                target_event = valid_rows[original_idx]
                is_in_valid = True
                actual_idx = original_idx
                event_sig = _make_event_signature(target_event)
            elif original_idx < 0:
                invalid_index = -original_idx - 1
                if invalid_index < len(invalid_events):
                    target_event = invalid_events[invalid_index]
                    is_in_valid = False
                    actual_idx = invalid_index
                    event_sig = _make_event_signature(target_event)

            # STEP 2: If not at original index, search by signature
            if target_event is None and original_idx >= 0:
                # Event was in valid, might have moved to invalid
                # We can't search without knowing the signature, so skip
                continue
            elif target_event is None and original_idx < 0:
                # Event was in invalid, might have moved to valid
                # Try to find it by checking if any valid event matches expected pattern
                # For now, skip if not found at expected index
                continue

            # STEP 3: Check if event has moved by comparing with signature maps
            if event_sig in valid_signatures and not is_in_valid:
                # Event moved from invalid to valid!
                target_event, actual_idx = valid_signatures[event_sig]
                is_in_valid = True
            elif event_sig in invalid_signatures and is_in_valid:
                # Event moved from valid to invalid!
                target_event, actual_idx = invalid_signatures[event_sig]
                is_in_valid = False

            # STEP 4: Apply override
            if is_in_valid:
                if status == "invalid":
                    # Move valid → invalid
                    new_invalid = dict(target_event)
                    new_invalid.update({
                        "reason": reason or "Forced invalid by override",
                        "category": "override",
                        "source": "override",
                        "override": {
                            "status": status,
                            "reason": reason,
                            "source": source,
                            "history": target_event.get("override", {}).get("history", []),
                        },
                        "final_classification": {
                            "is_valid": False,
                            "reason": reason,
                            "source": "override",
                        },
                        "status": "invalid",
                        "status_reason": reason,
                    })
                    moves_to_invalid.append((actual_idx, new_invalid))
                else:
                    # Update in place
                    if "override" not in target_event:
                        target_event["override"] = {}
                    target_event["override"].update({
                        "status": status,
                        "reason": reason,
                        "source": source,
                    })
                    if "final_classification" not in target_event:
                        target_event["final_classification"] = {}
                    target_event["final_classification"].update({
                        "is_valid": True,
                        "reason": reason,
                        "source": "override",
                    })
                    if "status" in target_event:
                        target_event["status"] = status or "valid"
                        target_event["status_reason"] = reason

            else:  # Currently in invalid
                if status == "valid":
                    # Move invalid → valid
                    new_row = dict(target_event)
                    new_row.update({
                        "status": "valid",
                        "status_reason": reason,
                        "override": {
                            "status": status,
                            "reason": reason,
                            "source": source,
                            "history": target_event.get("override", {}).get("history", []),
                        },
                        "final_classification": {
                            "is_valid": True,
                            "reason": reason,
                            "source": "override",
                        },
                    })
                    
                    # Ensure required fields exist
                    for field, default in [("is_inport", False), ("inport_label", None), 
                                          ("is_mission", False), ("label", None), ("confidence", 1.0)]:
                        if field not in new_row:
                            new_row[field] = default
                    
                    moves_to_valid.append((actual_idx, new_row))
                else:
                    # Update in place
                    if "override" not in target_event:
                        target_event["override"] = {}
                    target_event["override"].update({
                        "status": status,
                        "reason": reason,
                        "source": source,
                    })
                    if "final_classification" not in target_event:
                        target_event["final_classification"] = {}
                    target_event["final_classification"].update({
                        "is_valid": False,
                        "reason": reason,
                        "source": "override",
                    })

        # Execute moves (highest index first to avoid shifting)
        moves_to_invalid.sort(reverse=True, key=lambda x: x[0])
        for idx, new_invalid in moves_to_invalid:
            invalid_events.append(new_invalid)
            valid_rows.pop(idx)

        moves_to_valid.sort(reverse=True, key=lambda x: x[0])
        for idx, new_row in moves_to_valid:
            valid_rows.append(new_row)
            invalid_events.pop(idx)

        # Update sheet
        sheet["rows"] = valid_rows
        sheet["invalid_events"] = invalid_events

    return review_state_member
