import os
from datetime import datetime, timedelta   # PATCH: added timedelta

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

    # >>> PATCH ADDED HERE <<<
    # Ensure the summary folder exists BEFORE writing files
    os.makedirs(SUMMARY_TXT_FOLDER, exist_ok=True)

    for member_key, info in summary_data.items():
        ...
        # (UNCHANGED CODE)
        ...

        filename = f"{rate}_{last}_{first}_SUMMARY.txt".replace(" ", "_")
        summary_path = os.path.join(SUMMARY_TXT_FOLDER, filename)

        # >>> PATCH ENSURES FOLDER EXISTS <<<
        os.makedirs(os.path.dirname(summary_path), exist_ok=True)

        with open(summary_path, "w", encoding="utf-8") as f:
            f.write("".join(out))

        log(f"SUMMARY WRITTEN → {summary_path}")
