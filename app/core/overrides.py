import os
import json

from app.core.config import OVERRIDES_DIR


def _override_path(member_key: str, sheet_file: str) -> str:
    safe_member = member_key.replace("/", "_").replace("\\", "_")
    safe_sheet = sheet_file.replace("/", "_").replace("\\", "_")
    return os.path.join(OVERRIDES_DIR, safe_member, safe_sheet + ".json")


# -----------------------------------------------------------
# PATCH: Extract event text so it doesn't disappear when rows move between VALID/INVALID
# -----------------------------------------------------------
def _extract_event_text(obj):
    if not isinstance(obj, dict):
        return ""
    for k in ("event", "event_details", "Event", "event_text", "label"):
        v = obj.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


# -----------------------------------------------------------
# OVERRIDE STORAGE
# -----------------------------------------------------------

def load_overrides(member_key: str, sheet_file: str) -> dict:
    path = _override_path(member_key, sheet_file)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def save_override(member_key: str, sheet_file: str, event_index: int, status: str = None, reason: str = "", source: str = "manual"):
    os.makedirs(os.path.dirname(_override_path(member_key, sheet_file)), exist_ok=True)
    data = load_overrides(member_key, sheet_file)

    key = str(event_index)
    if status is None and (reason or "").strip() == "":
        if key in data:
            del data[key]
    else:
        data[key] = {
            "status": status,
            "reason": reason or "",
            "source": source or "manual"
        }

    with open(_override_path(member_key, sheet_file), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def clear_overrides(member_key: str, sheet_file: str):
    path = _override_path(member_key, sheet_file)
    if os.path.exists(path):
        os.remove(path)


# -----------------------------------------------------------
# APPLY OVERRIDES TO A REVIEW JSON STRUCTURE
# -----------------------------------------------------------

def apply_overrides(review_state: dict) -> dict:
    """
    review_state schema (per member, per sheet) expected:
      review_state[member_key][sheet_file] = {
          "valid_rows": [...],
          "invalid_events": [...],
      }

    Overrides keyed by event_index:
      >= 0 : indexes into valid_rows
      < 0  : indexes into invalid_events as (-idx - 1)
    """

    if not review_state:
        return review_state

    for member_key, sheets in review_state.items():
        if not isinstance(sheets, dict):
            continue

        for sheet_file, payload in sheets.items():
            if not isinstance(payload, dict):
                continue

            valid_rows = payload.get("valid_rows") or []
            invalid_events = payload.get("invalid_events") or []

            overrides = load_overrides(member_key, sheet_file)

            # If no overrides, ensure "override" objects still exist for UI (optional)
            for i, r in enumerate(valid_rows):
                if isinstance(r, dict):
                    r.setdefault("override", {"status": "", "reason": "", "source": "auto"})
                    r["event_index"] = i
            for i, e in enumerate(invalid_events):
                if isinstance(e, dict):
                    e.setdefault("override", {"status": "", "reason": "", "source": "auto"})
                    e["event_index"] = -(i + 1)

            # Apply overrides
            # We will build new lists to allow moving items between valid/invalid
            new_valid = []
            new_invalid = []

            # Track which invalid indexes are moved
            moved_from_invalid = set()

            # 1) Process VALID rows
            for i, r in enumerate(valid_rows):
                if not isinstance(r, dict):
                    continue

                key = str(i)
                ov = overrides.get(key)

                # default override object
                r.setdefault("override", {"status": "", "reason": "", "source": "auto"})
                r["event_index"] = i

                if not ov:
                    # no override: keep as is
                    new_valid.append(r)
                    continue

                forced_status = ov.get("status") or ""
                forced_reason = ov.get("reason") or ""
                forced_source = ov.get("source") or "manual"

                # attach override info
                r["override"]["status"] = forced_status
                r["override"]["reason"] = forced_reason
                r["override"]["source"] = forced_source

                if forced_status == "invalid":
                    # move to invalid
                    event_text = _extract_event_text(r)  # PATCH
                    new_invalid.append({
                        "date": r.get("date"),
                        "ship": r.get("ship"),
                        "event": event_text,              # PATCH
                        "event_details": event_text,      # PATCH
                        "final": "invalid",
                        "reason": forced_reason or "Forced invalid by override",
                        "override": r.get("override"),
                        "event_index": r.get("event_index"),
                        "raw": r.get("raw", ""),
                        "system_classification": r.get("system_classification", ""),
                        "matched_ship": r.get("matched_ship", r.get("ship")),
                    })
                else:
                    # keep valid (forced valid or auto)
                    new_valid.append(r)

            # 2) Process INVALID events
            for i, e in enumerate(invalid_events):
                if not isinstance(e, dict):
                    continue

                idx = -(i + 1)
                key = str(idx)

                ov = overrides.get(key)

                e.setdefault("override", {"status": "", "reason": "", "source": "auto"})
                e["event_index"] = idx

                if not ov:
                    # no override: keep invalid as-is
                    new_invalid.append(e)
                    continue

                forced_status = ov.get("status") or ""
                forced_reason = ov.get("reason") or ""
                forced_source = ov.get("source") or "manual"

                e["override"]["status"] = forced_status
                e["override"]["reason"] = forced_reason
                e["override"]["source"] = forced_source

                if forced_status == "valid":
                    # move to valid
                    moved_from_invalid.add(i)
                    event_text = _extract_event_text(e)  # PATCH
                    new_row = {
                        "date": e.get("date"),
                        "ship": e.get("ship"),
                        "event": event_text,             # PATCH
                        "event_details": event_text,     # PATCH
                        "final": "valid",
                        "reason": forced_reason or "",
                        "override": e.get("override"),
                        "event_index": e.get("event_index"),
                        "raw": e.get("raw", ""),
                        "system_classification": e.get("system_classification", ""),
                        "matched_ship": e.get("matched_ship", e.get("ship")),
                    }
                    new_valid.append(new_row)
                else:
                    # keep invalid (forced invalid or auto)
                    new_invalid.append(e)

            # Re-number event_index for UI consistency (optional, but keep your current behavior)
            for i, r in enumerate(new_valid):
                if isinstance(r, dict):
                    r["event_index"] = i
            for i, e in enumerate(new_invalid):
                if isinstance(e, dict):
                    e["event_index"] = -(i + 1)

            payload["valid_rows"] = new_valid
            payload["invalid_events"] = new_invalid

    return review_state
