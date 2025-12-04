import re
from datetime import datetime, timedelta

from app.core.ships import match_ship


# ------------------------------------------------
# DATE HANDLING
# ------------------------------------------------

def extract_year_from_filename(fn):
    m = re.search(r"(20\d{2})", fn)
    return m.group(1) if m else str(datetime.now().year)


# ********** SMART PARSER (MISSION-FIRST, ONE SHIP PER DATE) **********

def parse_rows(text, year):
    """
    Parse all dated rows from the OCR text.

    Rules:
    - We assign an occ_idx PER DATE in the order the OCR text is read.
    - SBTT and unknown/invalid ships go to skipped_unknown (and will be struck out).
    - For valid ships on the same date:
        * If there is only ONE ship that day -> keep the first valid, rest are duplicates.
        * If there are MULTIPLE different ships -> prefer entries that look like mission
          rides (contain 'M-1', 'M1', 'M-2', 'M2'). Among those, keep the earliest one;
          all others become duplicates.
    - Exactly ONE valid ship per date is kept in rows.
    """
    rows = []
    skipped_duplicates = []
    skipped_unknown = []

    lines = text.splitlines()

    # First pass: collect candidate entries per date
    per_date_entries = {}   # date -> [entries]
    date_order = []         # preserve order of first appearance

    for i, line in enumerate(lines):
        m = re.match(r"\s*(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?", line)
        if not m:
            continue

        mm, dd, yy = m.groups()
        y = ("20" + yy) if yy and len(yy) == 2 else yy or year
        date = f"{mm.zfill(2)}/{dd.zfill(2)}/{y}"

        raw = line[m.end():]
        if i + 1 < len(lines):
            raw += " " + lines[i + 1]

        cleaned_raw = raw.strip()
        upper_raw = cleaned_raw.upper()

        entry = {
            "raw": cleaned_raw,
            "upper": upper_raw,
            "line_index": i,
            "date": date,
            "ship": None,
            "kind": None,      # "valid", "unknown", "sbtt"
            "occ_idx": None,
        }

        if date not in per_date_entries:
            per_date_entries[date] = []
            date_order.append(date)

        per_date_entries[date].append(entry)

    # Second pass: classify, choose winner per date
    def is_mission_entry(e):
        up = e["upper"]
        return ("M-1" in up) or ("M1" in up) or ("M-2" in up) or ("M2" in up)

    for date in date_order:
        entries = per_date_entries[date]

        # assign occ_idx in order and classify
        occ = 0
        for e in entries:
            occ += 1
            e["occ_idx"] = occ
            upper = e["upper"]

            if "SBTT" in upper:
                e["kind"] = "sbtt"
                skipped_unknown.append({
                    "date": date,
                    "raw": "SBTT",
                    "occ_idx": occ,
                })
                continue

            ship = match_ship(e["raw"])
            e["ship"] = ship

            if not ship:
                e["kind"] = "unknown"
                skipped_unknown.append({
                    "date": date,
                    "raw": e["raw"],
                    "occ_idx": occ,
                })
            else:
                e["kind"] = "valid"

        valids = [e for e in entries if e["kind"] == "valid"]
        if not valids:
            continue  # no valid ships for this date

        ships_set = set(e["ship"] for e in valids)

        if len(ships_set) == 1:
            # Only one ship for that date; keep the first valid entry
            kept = valids[0]
        else:
            # Multiple ships same date: prefer mission entries
            mission_valids = [e for e in valids if is_mission_entry(e)]
            if mission_valids:
                kept = sorted(mission_valids, key=lambda e: e["occ_idx"])[0]
            else:
                kept = sorted(valids, key=lambda e: e["occ_idx"])[0]

        # Add the kept row as valid
        rows.append({
            "date": date,
            "ship": kept["ship"],
            "occ_idx": kept["occ_idx"],
        })

        # All other valid entries become duplicates
        for e in valids:
            if e is kept:
                continue
            skipped_duplicates.append({
                "date": date,
                "ship": e["ship"],
                "occ_idx": e["occ_idx"],
            })

    return rows, skipped_duplicates, skipped_unknown


# ------------------------------------------------
# GROUPING BY SHIP
# ------------------------------------------------

def group_by_ship(rows):
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
