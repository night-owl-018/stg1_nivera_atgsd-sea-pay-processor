import pdfplumber
import re
from datetime import datetime
from app.config import NAME_PREFIX, SIGNATURE_MARKER, SKIP_KEYWORD


def parse_date(date_str):
    """Parse M/D/YYYY or M/D/YY."""
    for fmt in ("%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(date_str, fmt).date()
        except ValueError:
            continue
    return None


def clean_event_name(raw):
    """
    Cleans multi-line raw event strings:
    - Merges lines
    - Removes times (0000 2359)
    - Removes (ASW ...)
    - Removes þ checkmark
    """
    raw = raw.replace("þ", " ")

    # Remove parentheses content
    raw = re.sub(r"\(.*?\)", " ", raw)

    # Remove times
    raw = re.sub(r"\b\d{3,4}\b", " ", raw)

    # Remove asterisks
    raw = raw.replace("*", " ")

    # Collapse whitespace
    raw = re.sub(r"\s+", " ", raw)

    return raw.strip().upper()


def group_events_by_ship(events):
    grouped = {}
    for dt, raw_event in events:
        ship = clean_event_name(raw_event)
        if not ship:
            continue
        grouped.setdefault(ship, []).append(dt)

    result = []
    for ship, dates in grouped.items():
        dates = sorted(dates)
        result.append((ship, dates[0], dates[-1]))
    return result


def extract_sailors_and_events(pdf_path):
    sailors = []

    current_name = None
    current_events = []
    pending_event = ""  # Hold multi-line event names
    pending_date = None

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            lines = (page.extract_text() or "").split("\n")

            for raw_line in lines:
                line = raw_line.strip()

                # 1. Detect sailor name line
                if line.startswith(NAME_PREFIX):
                    after = line[len(NAME_PREFIX):].strip()
                    if "SSN" in after:
                        name_part = after.split("SSN", 1)[0].strip()
                    else:
                        name_part = after

                    # Save previous sailor
                    if current_name and current_events:
                        sailors.append({
                            "name": current_name,
                            "events": group_events_by_ship(current_events)
                        })

                    current_name = name_part
                    current_events = []
                    pending_event = ""
                    pending_date = None
                    continue

                # 2. Detect new DATE row (start of new event block)
                parts = line.split(" ", 1)
                if len(parts) == 2:
                    date_candidate, rest = parts
                    dt = parse_date(date_candidate)

                    if dt:
                        # Store previous event if pending
                        if pending_event and pending_date:
                            current_events.append((pending_date, pending_event))

                        pending_date = dt
                        pending_event = rest.strip()
                        continue

                # 3. Otherwise, append continuation lines
                if pending_date and line:
                    pending_event += " " + line
                    continue

                # 4. Detect end-of-sailor block
                if SIGNATURE_MARKER in line and current_name:
                    if pending_event and pending_date:
                        current_events.append((pending_date, pending_event))

                    sailors.append({
                        "name": current_name,
                        "events": group_events_by_ship(current_events)
                    })

                    current_name = None
                    current_events = []
                    pending_event = ""
                    pending_date = None

    return sailors
