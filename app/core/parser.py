import re
from datetime import datetime, timedelta

# --- PATCH 1: THE IMPORT FIX ---
# Changed "from app.core.ships" to a relative import to allow the program to start.
from .ships import match_ship

# --- PATCH 2: THE YEAR LOGIC FIX (PART 1 of 2) ---
# Added this new helper function to implement the "year rollover" logic.
def _determine_year_from_context(month, day, sheet_start, sheet_end, fallback_year):
    """
    Intelligently determine the year for a given month/day using the
    year-rollover logic from the reporting period.
    """
    if not sheet_start or not sheet_end:
        return fallback_year

    # If the schedule month is numerically smaller than the reporting period's start month,
    # it means the year must have rolled over (e.g., month '1' is less than start_month '11').
    if month < sheet_start.month:
        return sheet_end.year
    else:
        return sheet_start.year

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
    if "ASW MITE" in up:
        return "ASW MITE"
    if "ASTAC MITE" in up:
        return "ASTAC MITE"
    if "SBTT" in up:
        ship = match_ship(raw)
        if ship:
            return f"{ship} SBTT"
        return "SBTT"
    if "MITE" in up:
        return "MITE"
    return None

def sanitize_event_parentheses(s: str) -> str:
    """
    Cleans OCR garbage *inside* parentheses for known event types.
    """
    if not s or "(" not in s or ")" not in s:
        return s
    def _clean_group(m):
        inner = m.group(1)
        up = inner.upper()
        if not any(k in up for k in ("ASW", "ASTAC", "MITE", "SBTT")):
            return "(" + inner + ")"
        inner = inner.replace("°", "")
        inner = inner.replace("\uFFFD", "")  # replacement char
        inner = inner.replace("þ", " ")
        inner = re.sub(r"\bICA\b", "", inner, flags=re.IGNORECASE)
        inner = " ".join(inner.split()).strip()
        return "(" + inner + ")"
    return re.sub(r"\(([^)]*)\)", _clean_group, s)

# --- PATCH 2: THE YEAR LOGIC FIX (PART 2 of 2) ---
# Modified the function signature and internal date logic.
def parse_rows(text, sheet_start=None, sheet_end=None, fallback_year=None):
    """
    TORIS Sea Duty parser, enriched for UI / JSON review state.
    """
    rows = []
    skipped_duplicates = []
    skipped_unknown = []
    lines = text.splitlines()
    per_date_entries = {}
    date_order = []

    for i, line in enumerate(lines):
        m = re.match(r"\s*(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?", line)
        if not m:
            continue
            
        # --- This block now uses our intelligent year logic ---
        mm_str, dd_str, parsed_year_str = m.groups()
        if parsed_year_str and len(parsed_year_str) == 4:
            y = parsed_year_str
        elif parsed_year_str and len(parsed_year_str) == 2:
            y = "20" + parsed_year_str
        else:
            y = _determine_year_from_context(int(mm_str), int(dd_str), sheet_start, sheet_end, fallback_year)

        if not y:
            continue
        # --- End of date logic patch ---

        date = f"{mm_str.zfill(2)}/{dd_str.zfill(2)}/{y}"
        raw = line[m.end():]
        
        for j in range(1, 4):
            if i + j < len(lines):
                next_line = lines[i + j].strip()
                if re.match(r"\s*(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?", next_line):
                    break
                raw += " " + next_line
        
        cleaned = raw.strip()
        cleaned = sanitize_event_parentheses(cleaned)
        up = cleaned.upper()
        entry = {
            "raw": cleaned, "upper": up, "date": date, "line_index": i,
            "occ_idx": None, "ship": None, "kind": None, "is_inport": False,
            "inport_label": None,
        }
        if date not in per_date_entries:
            per_date_entries[date] = []
            date_order.append(date)
        per_date_entries[date].append(entry)

    def is_mission(e):
        up = e["upper"]
        return any(tag in up for tag in ("M1", "M-1", "M2", "M-2"))

    for date in date_order:
        entries = per_date_entries[date]
        occ = 0
        for e in entries:
            occ += 1
            e["occ_idx"] = occ
            raw = e["raw"]
            up = e["upper"]
            label = detect_inport_label(raw, up)
            if label:
                e["is_inport"] = True
                e["inport_label"] = label
                e["kind"] = "inport"
            else:
                e["is_inport"] = False
                ship = match_ship(raw)
                e["ship"] = ship
                e["kind"] = "valid" if ship else "unknown"

        for e in entries:
            if e["kind"] == "inport":
                skipped_unknown.append({
                    "date": date, "raw": e["raw"], "occ_idx": e["occ_idx"],
                    "ship": e["inport_label"], "reason": f"In-Port Shore Side Event ({e['inport_label']})",
                })

        valids = [e for e in entries if e["kind"] == "valid"]
        if not valids:
            for e in entries:
                if e["kind"] == "unknown":
                    skipped_unknown.append({
                        "date": date, "raw": e["raw"], "occ_idx": e["occ_idx"],
                        "ship": None, "reason": "Unknown or Non-Platform Event",
                    })
            continue

        ships_set = set(e["ship"] for e in valids)
        if len(ships_set) == 1:
            kept = valids[0]
        else:
            mission_valids = [e for e in valids if is_mission(e)]
            kept = sorted(mission_valids or valids, key=lambda x: x["occ_idx"])[0]

        rows.append({
            "date": date, "ship": kept["ship"], "occ_idx": kept["occ_idx"], "raw": kept["raw"],
            "is_inport": False, "inport_label": None, "is_mission": is_mission(kept), "label": None,
        })

        for e in valids:
            if e is kept:
                continue
            skipped_duplicates.append({
                "date": date, "raw": e["raw"], "ship": e["ship"],
                "occ_idx": e["occ_idx"], "reason": "Duplicate entry for date",
            })
        for e in entries:
            if e["kind"] == "unknown":
                skipped_unknown.append({
                    "date": date, "raw": e["raw"], "occ_idx": e["occ_idx"],
                    "ship": None, "reason": "Unknown or Non-Platform Event",
                })
    return rows, skipped_duplicates, skipped_unknown

# --- PATCH 3: THE WEEKEND GROUPING FIX ---
# Updated this function to correctly group dates over a weekend.
def group_by_ship(rows):
    """Group continuous dates for each ship into start-end periods."""
    grouped = {}
    for r in rows:
        dt = datetime.strptime(r["date"], "%m/%d/%Y")
        grouped.setdefault(r["ship"], []).append(dt)
        
    output = []
    for ship, dates in grouped.items():
        if not dates: # Added safety check for empty date lists
            continue
            
        dates = sorted(set(dates))
        start = prev = dates[0]
        
        # --- This loop now correctly handles weekend gaps ---
        for d in dates[1:]:
            is_consecutive = (d - prev).days == 1
            is_weekend_jump = (prev.weekday() == 4 and (d - prev).days == 3) # 4 = Friday

            if is_consecutive or is_weekend_jump:
                prev = d
            else:
                output.append({"ship": ship, "start": start, "end": prev})
                start = prev = d
        # --- End of weekend logic patch ---
        
        output.append({"ship": ship, "start": start, "end": prev})
        
    return output
