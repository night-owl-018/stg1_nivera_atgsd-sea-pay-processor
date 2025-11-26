import pdfplumber
import re
from datetime import datetime
from app.config import NAME_PREFIX, SKIP_KEYWORD


def parse_date(s):
    """Parse dates in M/D/YYYY or M/D/YY formats."""
    if not s:
        return None
    for fmt in ("%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(s.strip(), fmt).date()
        except:
            pass
    return None


def clean_ship_name(raw):
    """Remove junk and return a clean uppercase ship name."""
    if not raw:
        return ""

    r = raw
    r = r.replace("þ", " ")
    r = re.sub(r"\(.*?\)", " ", r)              # remove (ASW ...)
    r = re.sub(r"\bUSS\b", " ", r, flags=re.I)  # remove USS prefix
    r = re.sub(r"\b\d{3,4}\b", " ", r)          # remove times (0000)
    r = re.sub(r"[\d\-]", " ", r)               # remove digits and hyphens
    r = re.sub(r"\s+", " ", r)                  # collapse spaces
    return r.strip().upper()


def extract_sailors_and_events(pdf_path):
    sailors = []
    current_name = None
    current_events = []

    print("DEBUG: OPENING PDF", pdf_path)

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            lines = text.split("\n")

            # ----------------------------------------------------------
            # DETECT NEW SAILOR
            # ----------------------------------------------------------
            for line in lines:
                if line.startswith(NAME_PREFIX):
                    name = line.replace(NAME_PREFIX, "").strip()
                    name = name.split("SSN")[0].strip()

                    # Save previous sailor before starting a new one
                    if current_name and current_events:
                        sailors.append({
                            "name": current_name,
                            "events": group_by_ship(current_events)
                        })

                    current_name = name
                    current_events = []
                    print("DEBUG: NEW SAILOR =", current_name)
                    break

            # ----------------------------------------------------------
            # READ TABLE EVENTS
            # ----------------------------------------------------------
            table = page.extract_table()
            if table:
                for row in table:
                    if not row or not isinstance(row[0], str):
                        continue

                    dt = parse_date(row[0])
                    if not dt:
                        continue

                    # Combine ship columns
                    ship_raw = " ".join(c for c in row[1:3] if c)

                    # Skip MITE rows
                    if SKIP_KEYWORD in ship_raw.upper():
                        continue

                    ship_clean = clean_ship_name(ship_raw)
                    if ship_clean:
                        current_events.append((dt, ship_clean))
                        print("DEBUG: EVENT", dt, ship_clean)

            # ----------------------------------------------------------
            # SIGNATURE = END OF SAILOR BLOCK
            # Flexible detection (real Navy PDFs vary)
            # ----------------------------------------------------------
            for line in lines:
                up = line.upper()
                if (
                    "SIGNATURE" in up or
                    "CERTIFYING" in up or
                    "OFFICER" in up or
                    "CERTIFICATION" in up
                ):
                    print("DEBUG: SIGNATURE DETECTED for", current_name)
                    if current_name:
                        sailors.append({
                            "name": current_name,
                            "events": group_by_ship(current_events)
                        })
                    current_name = None
                    current_events = []
                    break

    # ----------------------------------------------------------
    # SAFETY: Save last sailor even if signature not found
    # ----------------------------------------------------------
    if current_name and current_events:
        print("DEBUG: FORCING SAVE FINAL SAILOR", current_name)
        sailors.append({
            "name": current_name,
            "events": group_by_ship(current_events)
        })

    print("DEBUG: FINAL SAILORS =", sailors)
    return sailors


def group_by_ship(events):
    ships = {}
    for dt, ship in events:
        ships.setdefault(ship, []).append(dt)

    final = []
    for ship, dates in ships.items():
        dates.sort()
        final.append((ship, dates[0], dates[-1]))

    return final
