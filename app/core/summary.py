import os

from app.core.logger import log
from app.core.config import OUTPUT_DIR


def write_summary_files(summary_data):
    summary_dir = os.path.join(OUTPUT_DIR, "summary")
    os.makedirs(summary_dir, exist_ok=True)

    compiled_lines = []

    for key in sorted(summary_data.keys()):
        sd = summary_data[key]
        rate = sd["rate"]
        last = sd["last"]
        first = sd["first"]
        periods = sd["periods"]
        skipped_unknown = sd["skipped_unknown"]
        skipped_dupe = sd["skipped_dupe"]

        periods_sorted = sorted(periods, key=lambda p: (p["ship"], p["start"]))
        total_days = sum(p["days"] for p in periods_sorted)

        lines = []
        lines.append("=====================================================================")
        title = f"{rate} {last}, {first}".strip()
        lines.append(title)
        lines.append("=====================================================================")
        lines.append("")
        lines.append("VALID SEA PAY PERIODS")
        lines.append("---------------------------------------------------------------------")

        if periods_sorted:
            for p in periods_sorted:
                s = p["start"].strftime("%m/%d/%Y")
                e = p["end"].strftime("%m/%d/%Y")
                lines.append(f"{p['ship']} : FROM {s} TO {e} ({p['days']} DAYS)")
            lines.append(f"TOTAL VALID DAYS: {total_days}")
        else:
            lines.append("  NONE")
            lines.append("TOTAL VALID DAYS: 0")

        lines.append("")
        lines.append("---------------------------------------------------------------------")
        lines.append("INVALID / EXCLUDED EVENTS / UNRECOGNIZED / NON-SHIP ENTRIES")

        if skipped_unknown:
            for u in skipped_unknown:
                raw = u.get("raw", "")
                lines.append(f"  {u['date']} : {raw}")
        else:
            lines.append("  NONE")

        lines.append("")
        lines.append("---------------------------------------------------------------------")
        lines.append("DUPLICATE DATE CONFLICTS")

        if skipped_dupe:
            for d in skipped_dupe:
                lines.append(f"  {d['date']} : {d['ship']}")
        else:
            lines.append("  NONE")

        lines.append("")

        safe_rate = rate.replace(" ", "") if rate else ""
        base_name = f"{safe_rate}_{last}_{first}_summary".strip("_").replace(" ", "_")
        summary_path = os.path.join(summary_dir, f"{base_name}.txt")

        with open(summary_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

        compiled_lines.extend(lines)
        compiled_lines.append("")

    compiled_path = os.path.join(summary_dir, "ALL_SUMMARIES_COMPILED.txt")
    if compiled_lines:
        with open(compiled_path, "w", encoding="utf-8") as f:
            f.write("\n".join(compiled_lines))
        log("SUMMARY FILES UPDATED")
    else:
        with open(compiled_path, "w", encoding="utf-8") as f:
            f.write("NO DATA\n")
        log("SUMMARY FILES CREATED BUT EMPTY")
