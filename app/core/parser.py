import re
from datetime import datetime, timedelta
# --- THIS LINE HAS BEEN FIXED ---
from .ships import match_ship

# ==============================================================================
# NEW HELPER FUNCTION
# ==============================================================================
def _determine_year_from_context(month, day, sheet_start, sheet_end, fallback_year):
    """
    Intelligently determine the year for a given month/day using the
    year-rollover logic from the reporting period, as you specified.
    """
    if not sheet_start or not sheet_end:
        return fallback_year

    if month < sheet_start.month:
        return sheet_end.year
    else:
        return sheet_start.year

# ==============================================================================
# EXISTING AND PATCHED FUNCTIONS
# ==============================================================================

def extract_year_from_filename(fn):
    """Extract 4-digit year from filename or fallback to current year."""
    m = re.search(r"(20\d{2})", fn)
    return m.group(1) if m else str(datetime.now().year)

def detect_inport_label(raw, upper):
    """Standardizes MITE/SBTT labels."""
    up = upper
    if "ASW MITE" in up:
        return "ASW MITE"
    if "ASTAC MITE" in up:
        return "ASTAC MITE"
    if "SBTT" in up:
        ship = match_ship(raw)
        return f"{ship} SBTT" if ship else "SBTT"
    if "MITE" in up:
        return "MITE"
    return None

def sanitize_event_parentheses(s: str) -> str:
    """Cleans OCR garbage *inside* parentheses for known event types."""
    if not s or "(" not in s or ")" not in s:
        return s

    def _clean_group(m):
        content = m.group(1)
        if any(keyword in content for keyword in ["ASW", "ASTAC", "MITE", "SBTT"]):
            cleaned_content = re.sub(r"[^\w\s-]", "", content).strip()
            return f"({cleaned_content})"
        else:
            return m.group(0)

    return re.sub(r"\(([^)]+)\)", _clean_group, s)

def parse_rows(text, sheet_start=None, sheet_end=None, fallback_year=None):
    """
    Parses raw OCR text into structured rows using context-aware date logic.
    """
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    rows = []
    
    for i, line in enumerate(lines):
        m = re.match(r"\s*(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?", line)
        if not m:
            continue

        mm_str, dd_str, parsed_year_str = m.groups()
        
        if parsed_year_str and len(parsed_year_str) == 4:
            y = parsed_year_str
        elif parsed_year_str and len(parsed_year_str) == 2:
            y = "20" + parsed_year_str
        else:
            y = _determine_year_from_context(int(mm_str), int(dd_str), sheet_start, sheet_end, fallback_year)

        if not y:
            continue

        date = f"{mm_str.zfill(2)}/{dd_str.zfill(2)}/{y}"
        ship = match_ship(line)
        event = detect_inport_label(line, line.upper())
        
        rows.append({"date": date, "ship": ship or "UNKNOWN", "event": event, "line_num": i})
        
    return rows

def group_by_ship(rows):
    """
    Groups continuous dates for each ship, correctly handling weekend jumps.
    """
    grouped = {}
    for r in rows:
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

        for d in dates[1:]:
            is_consecutive = (d - prev).days == 1
            is_weekend_jump = (prev.weekday() == 4 and (d - prev).days == 3) # 4 = Friday

            if is_consecutive or is_weekend_jump:
                prev = d
            else:
                output.append({"ship": ship, "start": start, "end": prev})
                start = prev = d
        
        output.append({"ship": ship, "start": start, "end": prev})
        
    return output
