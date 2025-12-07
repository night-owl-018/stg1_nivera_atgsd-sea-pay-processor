import os
from datetime import datetime, timedelta   # PATCH: timedelta already kept

from app.core.logger import log
from app.core.config import SUMMARY_TXT_FOLDER


def fmt(d):
    """Format date as MM-DD-YYYY or 'UNKNOWN'."""
    if not d:
        return "UNKNOWN"
    return datetime.strftime(d, "%m-%d-%Y")


def write_summary_files(summary_data):
    """
    Writes PSD-style text summaries, now with:
    - EVENTS FOLLOWED (chronological log)
    """

    # PATCH — ensure folder exists BEFORE writing any summaries
    os.makedirs(SUMMARY_TXT_FOLDER, exist_ok=True)

    for member_key, info in summary_data.items():

        last = info.get("last", "UNKNOWN")
        first = info.get("first", "UNKNOWN")

        # >>> CRITICAL PATCH FIX <<<
        # rate variable did NOT exist before → caused processor crash
        rate = info.get("rate", "UNKNOWN")

        header = []
        header.append(f"{rate} {last}".upper())
        header.append("")
        header.append("VALID SEA PAY PERIODS (PAY AUTHORIZED):")
        header.append("")

        valid_periods = info.get("valid_periods", [])
        if valid_periods:
            for ship, start, end in valid_periods:
                header.append(f"- {ship} | {fmt(start)} TO {fmt(end)}")
        else:
            header.append("- NONE")

        header.append("")
        header.append("INVALID / NON-PAYABLE ENTRIES:")
        header.append("")

        invalid_events = info.get("invalid_events", [])
        if invalid_events:
            for ship, date, reason in invalid_events:
                header.append(f"- {ship} | {fmt(date)} | {reason}")
        else:
            header.append("- NONE")

        header.append("")
        header.append("EVENTS FOLLOWED:")
        header.append("")

        events_followed = info.get("events_followed", [])
        if events_followed:
            for e in events_followed:
                # e is pre-formatted text (already safe to print)
                header.append(f"- {e}")
        else:
            header.append("- NONE")

        header.append("")

        # >>> PATCH FIX — now rate exists properly <<<
        filename = f"{rate}_{last}_{first}_SUMMARY.txt".replace(" ", "_")
        summary_path = os.path.join(SUMMARY_TXT_FOLDER, filename)

        # PATCH — ensure subfolder exists
        os.makedirs(os.path.dirname(summary_path), exist_ok=True)

        with open(summary_path, "w", encoding="utf-8") as f:
            f.write("\n".join(header))

        log(f"SUMMARY WRITTEN → {summary_path}")
