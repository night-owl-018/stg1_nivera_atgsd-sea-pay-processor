import re
from datetime import datetime, timedelta

from app.core.ships import match_ship


def extract_year_from_filename(fn):
    """Extract 4-digit year from filename or fallback to current year."""
    m = re.search(r"(20\d{2})", fn)
    return m.group(1) if m else str(datetime.now().year)


# ----------------------------------------------------------
# DETECT TRAINING EVENT TYPE (SBTT / MITE VARIANTS)
# ----------------------------------------------------------
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
      (ASW 1°)      -> (ASW 1)
    Only touches parentheses that look like event labels (ASW/ASTAC/MITE/SBTT).
    """
    if not s or "(" not in s or ")" not in s:
        return s

    def _clean_group(m):
        inner = m.group(1)
        up = inner.upper()

        # Only clean likely event groups
        if not any(k in up for k in ("ASW", "ASTAC", "MITE", "SBTT")):
            return "(" + inner + ")"

        # Remove common OCR junk tokens/glyphs
        inner = inner.replace("°", "")
        inner = inner.replace("\uFFFD", "")  # replacement char
        inner = inner.replace("þ", " ")

        # Remove the specific OCR hallucination token
        inner = re.sub(r"\bICA\b", "", inner, flags=re.IGNORECASE)

        # Normalize whitespace
        inner = " ".join(inner.split()).strip()
        return "(" + inner + ")"

    return re.sub(r"\(([^)]*)\)", _clean_group, s)


# ----------------------------------------------------------
# MAIN TORIS PARSER (SBTT/MITE as invalid entries, not suppressors)
# ----------------------------------------------------------
def parse_rows(text, year):
    """
    TORIS Sea Duty parser, enriched for UI / JSON review state.

    PATCH: MITE/SBTT are now treated as invalid entries on a date,
    not as suppressors of the entire date. Valid ships still go through
    normal duplicate/mission priority logic.
    
    Behavior:
      - MITE/SBTT → added to skipped_unknown as invalid entries
      - Valid ships → normal mission priority + duplicate detection
      - Unknowns → stay invalid
    
    NEW (Phase 2):
      - rows now carry: raw, is_inport, inport_label, is_mission, label
      - skipped_unknown rows carry raw text
    """

    rows = []
    skipped_duplicates = []
    skipped_unknown = []

    lines = text.splitlines()

    per_date_entries = {}
    date_order = []

    # --------------------------------------------------
    # PASS 1 – Group by date (FIX: Multi-line continuation)
    # --------------------------------------------------
    for i, line in enumerate(lines):
        m = re.match(r"\s*(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?", line)
        if not m:
            continue

        mm, dd, yy = m.groups()
        y = ("20" + yy) if yy and len(yy) == 2 else yy or year
        date = f"{mm.zfill(2)}/{dd.zfill(2)}/{y}"

        raw = line[m.end():]
        
        # FIX: Look ahead up to 3 lines to capture multi-line events like:
        # "10/7/2025 OMAHA (ASW"
        # "SBTT)"
        # "þ"
        for j in range(1, 4):
            if i + j < len(lines):
                next_line = lines[i + j].strip()
                # Stop if we hit another date
                if re.match(r"\s*(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?", next_line):
                    break
                raw += " " + next_line

        cleaned = raw.strip()
        cleaned = sanitize_event_parentheses(cleaned)
        up = cleaned.upper()

        entry = {
            "raw": cleaned,
            "upper": up,
            "date": date,
            "line_index": i,
            "occ_idx": None,
            "ship": None,
            "kind": None,
            "is_inport": False,
            "inport_label": None,
        }

        if date not in per_date_entries:
            per_date_entries[date] = []
            date_order.append(date)

        per_date_entries[date].append(entry)

    # Mission check helper
    def is_mission(e):
        up = e["upper"]
        return any(tag in up for tag in ("M1", "M-1", "M2", "M-2"))

    # --------------------------------------------------
    # PASS 2 – Per-date evaluation
    # PATCH: MITE/SBTT are invalid entries, not date suppressors
    # --------------------------------------------------
    for date in date_order:
        entries = per_date_entries[date]
        occ = 0

        # First scan – detect labels, classify ships
        for e in entries:
            occ += 1
            e["occ_idx"] = occ

            raw = e["raw"]
            up = e["upper"]

            # Detect SBTT/MITE variant
            label = detect_inport_label(raw, up)
            if label:
                e["is_inport"] = True
                e["inport_label"] = label
                e["kind"] = "inport"  # Mark as inport training
            else:
                e["is_inport"] = False
                # Compute ship for non-inport entries
                ship = match_ship(raw)
                e["ship"] = ship
                e["kind"] = "valid" if ship else "unknown"

        # ------------------------------------------------------
        # PATCH: Add MITE/SBTT to skipped_unknown (don't suppress date)
        # ------------------------------------------------------
        for e in entries:
            if e["kind"] == "inport":
                skipped_unknown.append({
                    "date": date,
                    "raw": e["raw"],
                    "occ_idx": e["occ_idx"],
                    "ship": e["inport_label"],
                    "reason": f"In-Port Shore Side Event ({e['inport_label']})",
                })

        # ------------------------------------------------------
        # NORMAL VALID SHIP PROCESSING (mission priority + duplicates)
        # ------------------------------------------------------
        valids = [e for e in entries if e["kind"] == "valid"]

        if not valids:
            # Only unknowns (no valid ships)
            for e in entries:
                if e["kind"] == "unknown":
                    skipped_unknown.append({
                        "date": date,
                        "raw": e["raw"],
                        "occ_idx": e["occ_idx"],
                        "ship": None,
                        "reason": "Unknown or Non-Platform Event",
                    })
            continue

        # Multi-ship → mission priority
        ships_set = set(e["ship"] for e in valids)

        if len(ships_set) == 1:
            kept = valids[0]
        else:
            mission_valids = [e for e in valids if is_mission(e)]
            kept = sorted(mission_valids or valids, key=lambda x: x["occ_idx"])[0]

        # save kept row
        rows.append({
            "date": date,
            "ship": kept["ship"],
            "occ_idx": kept["occ_idx"],
            "raw": kept["raw"],
            "is_inport": False,
            "inport_label": None,
            "is_mission": is_mission(kept),
            "label": None,
        })

        # remaining valids → duplicates
        for e in valids:
            if e is kept:
                continue
            skipped_duplicates.append({
                "date": date,
                "raw": e["raw"],
                "ship": e["ship"],
                "occ_idx": e["occ_idx"],
                "reason": "Duplicate entry for date",
            })

        # unknown rows → invalid
        for e in entries:
            if e["kind"] == "unknown":
                skipped_unknown.append({
                    "date": date,
                    "raw": e["raw"],
                    "occ_idx": e["occ_idx"],
                    "ship": None,
                    "reason": "Unknown or Non-Platform Event",
                })

    return rows, skipped_duplicates, skipped_unknown


# ----------------------------------------------------------
# GROUPING LOGIC (unchanged)
# ----------------------------------------------------------
def group_by_ship(rows):
    """Group continuous dates for each ship into start-end periods."""
    grouped = {}

    for r in rows:
        dt = datetime.strptime(r["date"], "%m/%d/%Y")
        grouped.setdefault(r["ship"], []).append(dt)

    output = []

    for ship, dates in grouped.items():
        dates = sorted(set(dates))
        start = prev = dates[0]

        for d in dates[1:]:
            if d == prev + timedelta(days=1):
                prev = d
            else:
                output.append({"ship": ship, "start": start, "end": prev})
                start = prev = d

        output.append({"ship": ship, "start": start, "end": prev})

    return output
