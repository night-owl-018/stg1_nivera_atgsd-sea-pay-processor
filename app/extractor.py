import pdfplumber
import re
from datetime import datetime
from app.config import NAME_PREFIX, SIGNATURE_MARKER, SKIP_KEYWORD


def parse_date(date_str):
    """Parse M/D/YYYY or M/D/YY from SEA DUTY CERT sheet."""
    for fmt in ("%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(date_str, fmt).date()
        except ValueError:
            continue
    return None


def clean_event_name(name: str) -> str:
    """
    Extract ONLY the ship name from the Event column.

    Removes:
    - Parentheses, e.g. (ASW C-1)
    - Time-like blocks, e.g. 0830, 1600, 0000, 2359
    - Asterisks and extra junk
    """
    # Remove anything in parentheses: (ASW C-1), (ASW AS-2*1), etc.
    name = re.sub(r"\(.*?\)", "", name)

    # Remove 3–4 digit "times" like 0830, 1600, 0000, 2359
    name = re.sub(r"\b\d{3,4}\b", "", name)

    # Remove stray asterisks
    name = name.replace("*", " ")

    # Collapse multiple spaces
    name = re.sub(r"\s+", " ", name)

    return name.strip().upper()


def group_events_by_ship(events):
    """
    events: list[(date, full_event_string)]

    Returns:
      list[(ship_name, start_date, end_date)]

    Each ship for that sailor becomes ONE PG-13, using the
    earliest and latest date for that ship.
    """
    grouped = {}

    for dt, raw_name in events:
        ship = clean_event_name(raw_name)
        if not ship:
            continue
        grouped.setdefault(ship, []).append(dt)

    result = []
    for ship, dates in grouped.items():
        dates = sorted(dates)
        result.append((ship, dates[0], dates[-1]))  # begin / end for that ship

    return result


def extract_sailors_and_events(pdf_path):
    """
    Parse SEA DUTY CERT PDF and return:
      [
        {
          "name": "BRANDON ANDERSEN",
          "events": [
            ("CHOSIN", date(2025, 9, 8),  date(2025, 10, 29)),
            ("PAUL HAMILTON", date(...), date(...)),
            ...
          ]
        },
        ...
      ]

    - 'name' comes from the Name: line on the sheet.
    - 'events' are grouped by ship name (Event column), using
      first and last date for that ship.
    """
    sailors = []
    current_name = None
    current_events = []

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if not text:
                continue

            for raw_line in text.split("\n"):
                line = raw_line.strip()

                # 1. Detect name line (e.g. "Name: BRANDON ANDERSEN SSN/DOD #: ...")
                if line.startswith(NAME_PREFIX):
                    after = line[len(NAME_PREFIX):].strip()
                    if "SSN" in after:
                        name_part = after.split("SSN", 1)[0].strip()
                    else:
                        name_part = after

                    # Save previous sailor if we had one
                    if current_name and current_events:
                        events_grouped = group_events_by_ship(current_events)
                        sailors.append({
                            "name": current_name,
                            "events": events_grouped,
                        })

                    current_name = name_part
                    current_events = []
                    continue

                # 2. Detect event line: "8/11/2025 PAUL HAMILTON (ASW T-1)"
                parts = line.split(" ", 1)
                if len(parts) == 2:
                    date_candidate, rest = parts
                    dt = parse_date(date_candidate)
                    if dt and current_name:
                        event_raw = rest.strip()

                        # Skip MITE events completely
                        if SKIP_KEYWORD in event_raw.upper():
                            continue

                        current_events.append((dt, event_raw))
                        continue

                # 3. End-of-sailor marker (SIGNATURE OF CERTIFYING OFFICER & DATE)
                if SIGNATURE_MARKER in line and current_name:
                    events_grouped = group_events_by_ship(current_events)
                    sailors.append({
                        "name": current_name,
                        "events": events_grouped,
                    })
                    current_name = None
                    current_events = []

    return sailors
