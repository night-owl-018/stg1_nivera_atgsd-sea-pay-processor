import os
import json
from datetime import datetime
from app.core.config import OVERRIDES_DIR


def _override_path(member_key):
    """
    Convert 'STG1 NIVERA,RYAN' → 'STG1_NIVERA_RYAN.json'
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


def _norm_status(v):
    """
    Normalize override status to what the UI expects.
    UI dropdown values: "", "valid", "invalid"
    """
    if v is None:
        return ""
    v = str(v).strip().lower()
    if v in ("valid", "invalid"):
        return v
    return ""


def _stamp_ui_fields(evt, status, reason, source="override"):
    """
    CRITICAL: these fields are what your FRONTEND reads back after reload.
    If these are missing, dropdown snaps to Auto and reason looks unsaved.
    🔹 FIX: Handle None vs "" properly - always set fields explicitly
    🔹 FIX: Set BOTH override_reason AND reason fields for UI display
    """
    evt["override_status"] = status if status is not None else ""
    evt["override_reason"] = reason if reason is not None else ""
    evt["reason"] = reason if reason is not None else ""  # 🔹 FIX: UI reads this field!
    evt["source"] = source if source else "override"


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
        "override_status": _norm_status(status),
        "override_reason": reason or "",
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
    Apply overrides by matching events in a way that matches your UI behavior.

    FIX:
    - Find events by event_index across BOTH arrays (valid + invalid)
      because that is what the UI sends back to backend.
    - ALWAYS stamp override_status/override_reason onto returned rows
      so dropdown and reason textbox persist after reload.
    """
    overrides = load_overrides(member_key).get("overrides", [])
    if not overrides:
        return review_state_member

    # Build quick lookup: sheet_file -> list of overrides
    by_sheet = {}
    for ov in overrides:
        sf = ov.get("sheet_file")
        if not sf:
            continue
        by_sheet.setdefault(sf, []).append(ov)

    for sheet in review_state_member.get("sheets", []):
        sheet_file = sheet.get("source_file")
        if not sheet_file:
            continue

        sheet_overrides = by_sheet.get(sheet_file, [])
        if not sheet_overrides:
            continue

        valid_rows = sheet.get("rows", [])
        invalid_events = sheet.get("invalid_events", [])

        # Build event_index maps (THIS is the real fix)
        valid_by_eidx = {}
        for i, row in enumerate(valid_rows):
            if isinstance(row, dict) and "event_index" in row:
                valid_by_eidx[row["event_index"]] = (i, row)

        invalid_by_eidx = {}
        for i, ev in enumerate(invalid_events):
            if isinstance(ev, dict) and "event_index" in ev:
                invalid_by_eidx[ev["event_index"]] = (i, ev)

        moves_to_invalid = []  # (valid_idx, new_invalid_entry)
        moves_to_valid = []    # (invalid_idx, new_valid_entry)

        for ov in sheet_overrides:
            event_index = ov.get("event_index")
            status = _norm_status(ov.get("override_status"))
            reason = ov.get("override_reason") or ""
            source = ov.get("source") or "manual"

            # 1) Find the event where it CURRENTLY lives (valid or invalid)
            target_event = None
            current_location = None
            current_idx = None

            if event_index in valid_by_eidx:
                current_idx, target_event = valid_by_eidx[event_index]
                current_location = "valid"
            elif event_index in invalid_by_eidx:
                current_idx, target_event = invalid_by_eidx[event_index]
                current_location = "invalid"
            else:
                # Fallback: signature scan (optional)
                # If your event_index is missing for some reason, try signature lookup.
                # This will NOT break anything; it just gives you a second chance.
                sig_map = {}
                for i, row in enumerate(valid_rows):
                    if isinstance(row, dict):
                        sig_map[_make_event_signature(row)] = ("valid", i, row)
                for i, ev in enumerate(invalid_events):
                    if isinstance(ev, dict):
                        sig_map[_make_event_signature(ev)] = ("invalid", i, ev)

                # We cannot build signature from override record, so we cannot match.
                # If it gets here, the override won't apply.
                continue

            # 2) Apply behavior based on where it is now and desired status
            if current_location == "valid":
                if status == "invalid":
                    # Move valid → invalid
                    new_invalid = dict(target_event)
                    new_invalid.update({
                        "reason": reason if reason is not None else "Forced invalid by override",
                        "category": "override",
                        "source": "override",
                        "override": {
                            "status": status,
                            "reason": reason if reason is not None else "",
                            "source": source,
                            "history": target_event.get("override", {}).get("history", []),
                        },
                        "final_classification": {
                            "is_valid": False,
                            "reason": reason if reason is not None else "",
                            "source": "override",
                        },
                        "status": "invalid",
                        "status_reason": reason if reason is not None else "Forced invalid by override",
                    })
                    _stamp_ui_fields(new_invalid, status, reason, "override")
                    moves_to_invalid.append((current_idx, new_invalid))
                else:
                    # Stay valid (status == "valid" OR Auto "")
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
                        "reason": reason if reason is not None else "",
                        "source": "override" if (status or reason) else target_event.get("final_classification", {}).get("source"),
                    })

                    # Keep actual status as valid if Auto, otherwise set to valid
                    target_event["status"] = "valid"
                    # 🔹 FIX: Always set status_reason, even if blank, to clear old values
                    target_event["status_reason"] = reason if reason is not None else ""

                    _stamp_ui_fields(target_event, status, reason, "override")

            else:
                # Currently invalid
                if status == "valid":
                    # Move invalid → valid
                    new_row = dict(target_event)
                    new_row.update({
                        "status": "valid",
                        "status_reason": reason if reason is not None else "",
                        "override": {
                            "status": status,
                            "reason": reason if reason is not None else "",
                            "source": source,
                            "history": target_event.get("override", {}).get("history", []),
                        },
                        "final_classification": {
                            "is_valid": True,
                            "reason": reason if reason is not None else "",
                            "source": "override",
                        },
                    })

                    # Ensure required fields exist
                    for field, default in [
                        ("is_inport", False),
                        ("inport_label", None),
                        ("is_mission", False),
                        ("label", None),
                        ("confidence", 1.0),
                    ]:
                        if field not in new_row:
                            new_row[field] = default

                    _stamp_ui_fields(new_row, status, reason, "override")
                    moves_to_valid.append((current_idx, new_row))
                else:
                    # Stay invalid (status == "invalid" OR Auto "")
                    if "override" not in target_event:
                        target_event["override"] = {}
                    target_event["override"].update({
                        "status": status,
                        "reason": reason if reason is not None else "",
                        "source": source,
                    })
                    if "final_classification" not in target_event:
                        target_event["final_classification"] = {}
                    target_event["final_classification"].update({
                        "is_valid": False,
                        "reason": reason if reason is not None else "",
                        "source": "override" if (status or reason) else target_event.get("final_classification", {}).get("source"),
                    })

                    # If Auto "", keep it invalid as-is; if invalid, force invalid
                    target_event["status"] = "invalid"
                    # 🔹 FIX: Always set status_reason, even if blank, to clear old values
                    target_event["status_reason"] = reason if reason is not None else ""

                    _stamp_ui_fields(target_event, status, reason, "override")

        # 3) Execute moves (highest index first to avoid shifting)
        moves_to_invalid.sort(reverse=True, key=lambda x: x[0])
        for idx, new_invalid in moves_to_invalid:
            invalid_events.append(new_invalid)
            valid_rows.pop(idx)

        moves_to_valid.sort(reverse=True, key=lambda x: x[0])
        for idx, new_row in moves_to_valid:
            valid_rows.append(new_row)
            invalid_events.pop(idx)

        sheet["rows"] = valid_rows
        sheet["invalid_events"] = invalid_events

    return review_state_member
