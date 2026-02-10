import re
from datetime import datetime, timedelta
from app.core.ships import match_ship

# ==============================================================================
# NEW HELPER FUNCTION (As per our discussion)
# ==============================================================================
def _determine_year_from_context(month, day, sheet_start, sheet_end, fallback_year):
    """
    Intelligently determine the year for a given month/day using the
    year-rollover logic from the reporting period, as you specified.
    """
    if not sheet_start or not sheet_end:
        # If we have no context, we have to use the fallback
        return fallback_year

    # If the schedule month is numerically smaller than the reporting period's start month,
    # it means the year must have rolled over. (e.g., month '1' is less than start_month '11')
    if month < sheet_start.month:
        # The year must be the END year of the period (e.g., 2026)
        return sheet_end.year
    else:
        # The year is still the START year of the period (e.g., 2025)
        return sheet_start.year

# ==============================================================================
# EXISTING AND PATCHED FUNCTIONS
# ==============================================================================

def extract_year_from_filename(fn):
    """Extract 4-digit year from filename or fallback to current year."""
    m = re.search(r"(20\d{2})", fn)
    return m.group(1) if m else str(datetime.now().year)

def detect_inport_label(raw, upper):
    """
    Standardizes labels:
    - ASW MITE
    - ASTAC MITE
    - <SHIP> SBTT
    - SBTT
    - MITE
    Returns label or None.
    """
    up = upper
    # Priority 1: explicit ASW/ASTAC MITE
    if "ASW MITE" in up:
        return "ASW MITE"
    if "ASTAC MITE" in up:
        return "ASTAC MITE"

    # Priority 2: SBTT or <SHIP> SBTT
    if "SBTT" in up:
        ship = match_ship(raw)
        if ship:
            return f"{ship} SBTT"
        return "SBTT"

    # Priority 3: generic MITE
    if "MITE" in up:
        return "MITE"
    return None

def sanitize_event_parentheses(s: str) -> str:
    """
    Cleans OCR garbage *inside* parentheses for known event types.
    Fixes cases like:
    (ASW ICA T-3) -> (ASW T-3)
    (ASW 1°) -> (ASW 1)
    Only touches parentheses that look like event labels (ASW/ASTAC/MITE/SBTT).
    """
    if not s or "(" not in s or ")" not in s:
        return s

    def _clean_group(m):
        content = m.group(1)
        # Only clean if it's a known event type
        if any(keyword in content for keyword in ["ASW", "ASTAC", "MITE", "SBTT"]):
            # Remove extraneous characters, but keep essentials
            cleaned_content = re.sub(r"[^\w\s-]", "", content).strip()
            return f"({cleaned_content})"
        else:
            # Return original if not a recognized event
            return m.group(0)

    return re.sub(r"\(([^)]+)\)", _clean_group, s)

# --- MODIFIED FUNCTION ---
def parse_rows(text, sheet_start=None, sheet_end=None, fallback_year=None):
    """
    Parses raw OCR text into structured rows.
    Now uses reporting period context to determine the correct year.
    """
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    rows = []
    
    for i, line in enumerate(lines):
        # --- PATCH APPLIED HERE ---
        # This logic is replaced with the robust, context-aware version.
        m = re.match(r"\s*(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?", line)
        if not m:
            continue

        mm_str, dd_str, parsed_year_str = m.groups()
        
        # Determine the year using the new, robust logic
        if parsed_year_str and len(parsed_year_str) == 4:
            y = parsed_year_str
        elif parsed_year_str and len(parsed_year_str) == 2:
            y = "20" + parsed_year_str
        else:
            # Use the new context-aware function
            y = _determine_year_from_context(int(mm_str), int(dd_str), sheet_start, sheet_end, fallback_year)

        if not y:
            # Could not determine a year, so we must skip this line
            continue

        date = f"{mm_str.zfill(2)}/{dd_str.zfill(2)}/{y}"
        # --- END OF PATCH ---

        ship = match_ship(line)
        event = detect_inport_label(line, line.upper())
        
        rows.append({
            "date": date,
            "ship": ship or "UNKNOWN",
            "event": event,
            "line_num": i
        })
        
    return rows

# --- MODIFIED FUNCTION ---
def group_by_ship(rows):
    """
    Group continuous dates for each ship into start-end periods.
    Now correctly handles weekend jumps (Friday to Monday).
    """
    grouped = {}
    for r in rows:
        # Skip rows with unknown ships
        if r["ship"] == "UNKNOWN":
            continue
        dt = datetime.strptime(r["date"], "%m/%d/%Y")
        grouped.setdefault(r["ship"], []).append(dt)

    output = []
    for ship, dates in grouped.items():
        if not dates:
            continue
        
        dates = sorted(set(dates))
        start = prev = dates[0]

        # --- PATCH APPLIED HERE ---
        # This loop now correctly handles weekend gaps.
        for d in dates[1:]:
            is_consecutive = (d - prev).days == 1
            # 4 = Friday
            is_weekend_jump = (prev.weekday() == 4 and (d - prev).days == 3)

            if is_consecutive or is_weekend_jump:
                prev = d
            else:
                output.append({"ship": ship, "start": start, "end": prev})
                start = prev = d
        # --- END OF PATCH ---
        
        # Append the last period for the current ship
        output.append({"ship": ship, "start": start, "end": prev})
        
    return output
