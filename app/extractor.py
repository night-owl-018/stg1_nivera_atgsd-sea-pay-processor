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
    - Time ranges, e.g. 0830, 1600, 0000, 2359
    - Extra numbers or garbage after ship name
    """
    # Remove parentheses: (ASW C-1)
    name = re.sub(r"\(.*?\)", "", name)

    # Remove 3-4 digit time blocks (0830, 1600, 0000, 2359)
    name = re.sub(r"\b\d{3,4}\b", "", name)

    # Remove stray asterisks or weird characters
    name = name.replace("*", " ")

    # Collapse multiple spaces
    name = re.sub(r"\s+", " ", name)

    # Final clean + uppercase
    return name.strip().upper()


def group_events_by_ship(events):
    """
    events: list[(date, full_event_string)]
    Returns: list[(ship_name, start_date, end_date)]
    """
    grouped = {}
    for dt, name in events:
        ship = clean_event_name(name)
        if not ship:
            continue
        grouped.setdefault(ship, []).append(dt)

    result = []
    for ship, dates in grouped.items():
        dates = sorted(dates)
        result.append((ship, dates[0], dates[-1]))
    return result


def extract_sailors_and_events(pdf_path):
    """
    Parse SEA DUTY CERT PDF and return:
    [
      {
        "name": "LAST FIRST MIDDLE",
        "events": [
          ("CHOSIN", date(2025, 9, 8), date(2025, 10, 29)),
          ("PAUL HAMILTON", ...),
          ...
        ]
      },
      ...
    ]
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

                # 1. Detect NAME line
                if line.startswith(NAME_PREFIX):
                    after = line[len(NAME_PREFIX):].strip()
                    if "SSN" in after:
                        name_part = after.split("SSN", 1)[0].strip()
                    else:
                        name_part = after

                    # Save previous sailor (if exists)
                    if current_name and current_events:
                        sailors.append({
                            "name": current_name,
                            "events": group_events_by_ship(current_events)
                        })

                    current_name = name_part
                    current_events = []
                    continue

                # 2. Detect event lines: "8/11/2025 CHOSIN (ASW C-1)"
                parts = line.split(" ", 1)
                if len(parts) == 2:
                    date_candidate, rest = parts
                    dt = parse_date(date_candidate)
                    if dt and current_name:
                        event_raw = rest.strip()

                        # Skip MITE events
                        if SKIP_KEYWORD in event_raw.upper():
                            continue

                        current_events.append((dt, event_raw))
                        continue

                # 3. Detect end of sailor block
                if SIGNATURE_MARKER in line and current_name:
                    sailors.append({
                        "name": current_name,
                        "events": group_events_by_ship(current_events)
                    })
                    current_name = None
                    current_events = []

    return sailors
