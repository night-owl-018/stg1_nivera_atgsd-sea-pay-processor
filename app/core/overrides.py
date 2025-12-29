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
    data = load_overrides(member_key)

    data["overrides"].append(
        {
            "sheet_file": sheet_file,
            "event_index": event_index,
            "override_status": status,
            "override_reason": reason,
            "source": source,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }
    )

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
    """

    overrides = load_overrides(member_key).get("overrides", [])
    if not overrides:
        return review_state_member

    for ov in overrides:
        sheet_file = ov["sheet_file"]
        idx = ov["event_index"]
        status = ov["override_status"]
        reason = ov["override_reason"]
        source = ov.get("source")

        for sheet in review_state_member.get("sheets", []):
            if sheet.get("source_file") != sheet_file:
                continue

            # -------------------------
            # ROW OVERRIDE (VALID → INVALID)
            # PATCH: Move to invalid_events if forcing invalid
            # -------------------------
            if idx >= 0:
                if idx >= len(sheet.get("rows", [])):
                    continue

                r = sheet["rows"][idx]

                r["override"]["status"] = status
                r["override"]["reason"] = reason
                r["override"]["source"] = source

                r["final_classification"]["is_valid"] = (status == "valid")
                r["final_classification"]["reason"] = reason
                r["final_classification"]["source"] = "override"

                r["status"] = status
                r["status_reason"] = reason

                # PATCH: If forcing to invalid, move row to invalid_events array
                if status == "invalid":
                    # Create invalid event entry from row
                    new_invalid = {
                        "date": r.get("date"),
                        "ship": r.get("ship"),
                        "occ_idx": r.get("occ_idx"),
                        "raw": r.get("raw", ""),
                        "reason": reason or "Forced invalid by override",
                        "category": "override",
                        "source": "override",
                        "system_classification": r.get("system_classification", {}),
                        "override": {
                            "status": status,
                            "reason": reason,
                            "source": source,
                            "history": [],
                        },
                        "final_classification": {
                            "is_valid": False,
                            "reason": reason,
                            "source": "override",
                        },
                    }
                    
                    # Add to invalid_events array
                    sheet["invalid_events"].append(new_invalid)
                    
                    # Remove from rows array
                    sheet["rows"].pop(idx)

            # -------------------------
            # INVALID EVENT OVERRIDE (INVALID → VALID)
            # PATCH: Move to rows if forcing valid
            # -------------------------
            else:
                invalid_index = -idx - 1
                if invalid_index >= len(sheet.get("invalid_events", [])):
                    continue

                e = sheet["invalid_events"][invalid_index]

                e["override"]["status"] = status
                e["override"]["reason"] = reason
                e["override"]["source"] = source

                e["final_classification"]["is_valid"] = (status == "valid")
                e["final_classification"]["reason"] = reason
                e["final_classification"]["source"] = "override"

                # PATCH: If forcing to valid, move event to rows array
                if status == "valid":
                    # Create row entry from invalid event
                    new_row = {
                        "date": e.get("date"),
                        "ship": e.get("ship"),
                        "occ_idx": e.get("occ_idx"),
                        "raw": e.get("raw", ""),
                        "is_inport": False,
                        "inport_label": None,
                        "is_mission": False,
                        "label": None,
                        "status": "valid",
                        "status_reason": reason,
                        "confidence": 1.0,
                        "system_classification": e.get("system_classification", {}),
                        "override": {
                            "status": status,
                            "reason": reason,
                            "source": source,
                            "history": [],
                        },
                        "final_classification": {
                            "is_valid": True,
                            "reason": reason,
                            "source": "override",
                        },
                    }
                    
                    # Add to rows array
                    sheet["rows"].append(new_row)
                    
                    # Remove from invalid_events array
                    sheet["invalid_events"].pop(invalid_index)

    return review_state_member
