import os
import io
from datetime import datetime

from PyPDF2 import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.lib.colors import black

from app.core.logger import log
from app.core.config import TEMPLATE, FONT_NAME, FONT_SIZE, SEA_PAY_PG13_FOLDER
from app.core.rates import resolve_identity


# ------------------------------------------------
# FLATTEN PDF  (UNCHANGED ORIGINAL)
# ------------------------------------------------
def flatten_pdf(path):
    try:
        reader = PdfReader(path)
        writer = PdfWriter()

        for page in reader.pages:
            if "/Annots" in page:
                del page["/Annots"]

            contents = page.get("/Contents")
            if isinstance(contents, list):
                merged = b""
                for obj in contents:
                    merged += obj.get_data()
                page["/Contents"] = writer._add_object(merged)

            if "/Rotate" in page:
                del page["/Rotate"]

            writer.add_page(page)

        if "/AcroForm" in writer._root_object:
            del writer._root_object["/AcroForm"]

        tmp = path + ".flat"
        with open(tmp, "wb") as f:
            writer.write(f)

        os.replace(tmp, path)
        log(f"FLATTENED → {os.path.basename(path)}")

    except Exception as e:
        log(f"⚠️ FLATTEN FAILED → {e}")


# ------------------------------------------------
# 🔹 PATCH HELPER: Safe filename pieces
# ------------------------------------------------
def _safe_filename_piece(s: str) -> str:
    """
    Normalize filename parts so you don't get commas, double underscores, or leading underscores.
    """
    if s is None:
        return ""
    s = str(s).strip()
    # Replace separators that break readability
    s = s.replace(",", "_").replace(" ", "_")
    # Collapse multiple underscores
    while "__" in s:
        s = s.replace("__", "_")
    # Trim underscores
    s = s.strip("_")
    return s


# ------------------------------------------------
# 🔹 NEW: CONSOLIDATED ALL MISSIONS (ALL SHIPS ON ONE FORM)
# ------------------------------------------------
def make_consolidated_all_missions_pdf(ship_groups, name):
    """
    Creates a SINGLE PG-13 form with ALL missions across ALL ships for a member.
    Each ship gets its own section with all its date ranges.
    """
    if not ship_groups:
        return

    rate, last, first = resolve_identity(name)

    # Sort ships alphabetically for consistency
    sorted_ships = sorted(ship_groups.items())

    # Calculate total periods across all ships
    total_periods = sum(len(periods) for _, periods in sorted_ships)

    # Find overall date range for filename
    all_periods = []
    for _, periods in sorted_ships:
        all_periods.extend(periods)

    if not all_periods:
        return

    all_periods_sorted = sorted(all_periods, key=lambda g: g["start"])
    first_period = all_periods_sorted[0]
    last_period = all_periods_sorted[-1]

    s_fn = first_period["start"].strftime("%m-%d-%Y")
    e_fn = last_period["end"].strftime("%m-%d-%Y")

    # 🔹 PATCH: Make the "who" part clean + consistent
    who_parts = [_safe_filename_piece(rate), _safe_filename_piece(last), _safe_filename_piece(first)]
    who_parts = [p for p in who_parts if p]
    who = "_".join(who_parts) if who_parts else "UNKNOWN_MEMBER"

    # 🔹 PATCH: Use the same token as legacy PG-13 so packaging/merging finds it
    filename = (
        f"{who}"
        f"__SEA_PAY_PG13__ALL_MISSIONS__{s_fn}_TO_{e_fn}.pdf"
    )

    outpath = os.path.join(SEA_PAY_PG13_FOLDER, filename)

    # Create overlay with all ships and their periods
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.setFont(FONT_NAME, FONT_SIZE)

    # HEADER BLOCK
    c.drawString(39, 689, "AFLOAT TRAINING GROUP SAN DIEGO (UIC. 49365)")
    c.drawString(373, 671, "X")
    c.setFont(FONT_NAME, 8)
    c.drawString(39, 650, "ENTITLEMENT")
    c.drawString(345, 641, "OPNAVINST 7220.14")

    # Member identity
    c.setFont(FONT_NAME, FONT_SIZE)
    identity = f"{rate} {last}, {first}" if rate else f"{last}, {first}"
    c.drawString(39, 41, identity)

    # 🔹 MAIN TEXT BLOCK - ALL SHIPS AND PERIODS
    y = 595
    line_spacing = 12
    current_line = 0

    for ship, periods in sorted_ships:
        periods_sorted = sorted(periods, key=lambda g: g["start"])

        # Add each period for this ship
        for g in periods_sorted:
            s = g["start"].strftime("%m/%d/%Y")
            e = g["end"].strftime("%m/%d/%Y")

            c.drawString(
                38.8,
                y - (current_line * line_spacing),
                f"____. REPORT CAREER SEA PAY FROM {s} TO {e}."
            )
            current_line += 1

        # Ship information line (after periods for this ship)
        c.drawString(
            64,
            y - (current_line * line_spacing),
            f"Member performed eight continuous hours per day on-board: "
            f"{ship.upper()} Category A vessel."
        )
        current_line += 1

        # Add blank line between ships (if not the last ship)
        if ship != sorted_ships[-1][0]:
            current_line += 1

    # 🔹 SIGNATURE AREAS - Adjust position based on content
    content_height = current_line * line_spacing
    base_sig_y = 499.5
    sig_y = min(base_sig_y, 595 - content_height - 40)

    c.drawString(356.26, sig_y, "_________________________")
    c.drawString(363.8, sig_y - 12, "Certifying Official & Date")
    c.drawString(356.26, sig_y - 72, "_________________________")
    c.drawString(384.1, sig_y - 84.3, "FI MI Last Name")

    c.drawString(38.8, 83, "SEA PAY CERTIFIER")
    c.drawString(503.5, 40, "USN AD")

    # Finish overlay
    c.save()
    buf.seek(0)

    # MERGE WITH TEMPLATE
    template = PdfReader(TEMPLATE)
    overlay = PdfReader(buf)
    base = template.pages[0]
    base.merge_page(overlay.pages[0])

    writer = PdfWriter()
    writer.add_page(base)

    # 🔹 PATCH: ensure output folder exists before writing
    os.makedirs(SEA_PAY_PG13_FOLDER, exist_ok=True)

    with open(outpath, "wb") as f:
        writer.write(f)

    flatten_pdf(outpath)

    ship_count = len(sorted_ships)
    log(f"CREATED ALL MISSIONS PG-13 → {filename} ({ship_count} ships, {total_periods} periods on 1 form)")


# ------------------------------------------------
# 🔹 NEW: CONSOLIDATED PG-13 (MULTIPLE PERIODS ON ONE FORM)
# ------------------------------------------------
def make_consolidated_pdf_for_ship(ship, periods, name):
    """
    Creates a SINGLE PG-13 form with multiple date ranges for the same ship.
    """
    if not periods:
        return

    rate, last, first = resolve_identity(name)
    periods_sorted = sorted(periods, key=lambda g: g["start"])

    first_period = periods_sorted[0]
    last_period = periods_sorted[-1]

    s_fn = first_period["start"].strftime("%m-%d-%Y")
    e_fn = last_period["end"].strftime("%m-%d-%Y")

    filename = (
        f"{rate}_{last}_{first}"
        f"__SEA_PAY_PG13__{ship.upper()}__CONSOLIDATED__{s_fn}_TO_{e_fn}.pdf"
    )
    filename = filename.replace(" ", "_")

    outpath = os.path.join(SEA_PAY_PG13_FOLDER, filename)

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.setFont(FONT_NAME, FONT_SIZE)

    c.drawString(39, 689, "AFLOAT TRAINING GROUP SAN DIEGO (UIC. 49365)")
    c.drawString(373, 671, "X")
    c.setFont(FONT_NAME, 8)
    c.drawString(39, 650, "ENTITLEMENT")
    c.drawString(345, 641, "OPNAVINST 7220.14")

    c.setFont(FONT_NAME, FONT_SIZE)
    identity = f"{rate} {last}, {first}" if rate else f"{last}, {first}"
    c.drawString(39, 41, identity)

    y = 595
    line_spacing = 12

    for idx, g in enumerate(periods_sorted):
        s = g["start"].strftime("%m/%d/%Y")
        e = g["end"].strftime("%m/%d/%Y")

        c.drawString(
            38.8,
            y - (idx * line_spacing),
            f"____. REPORT CAREER SEA PAY FROM {s} TO {e}."
        )

    ship_line_y = y - (len(periods_sorted) * line_spacing) - 12
    c.drawString(
        64,
        ship_line_y,
        f"Member performed eight continuous hours per day on-board: "
        f"{ship.upper()} Category A vessel."
    )

    c.drawString(356.26, 499.5, "_________________________")
    c.drawString(363.8, 487.5, "Certifying Official & Date")
    c.drawString(356.26, 427.5, "_________________________")
    c.drawString(384.1, 415.2, "FI MI Last Name")

    c.drawString(38.8, 83, "SEA PAY CERTIFIER")
    c.drawString(503.5, 40, "USN AD")

    c.save()
    buf.seek(0)

    template = PdfReader(TEMPLATE)
    overlay = PdfReader(buf)
    base = template.pages[0]
    base.merge_page(overlay.pages[0])

    writer = PdfWriter()
    writer.add_page(base)

    with open(outpath, "wb") as f:
        writer.write(f)

    flatten_pdf(outpath)

    total_periods = len(periods_sorted)
    log(f"CREATED CONSOLIDATED PG-13 → {filename} ({total_periods} periods on 1 form)")


# ------------------------------------------------
# ORIGINAL FORMAT — ONE PG-13 PER PERIOD
# ------------------------------------------------
def make_pdf_for_ship(ship, periods, name, consolidate=False):
    """
    Creates PG-13 forms for ship periods.
    """
    if not periods:
        return

    if consolidate and len(periods) > 1:
        make_consolidated_pdf_for_ship(ship, periods, name)
        return

    rate, last, first = resolve_identity(name)
    periods_sorted = sorted(periods, key=lambda g: g["start"])

    for g in periods_sorted:
        s = g["start"].strftime("%m/%d/%Y")
        e = g["end"].strftime("%m/%d/%Y")

        s_fn = s.replace("/", "-")
        e_fn = e.replace("/", "-")

        filename = (
            f"{rate}_{last}_{first}"
            f"__SEA_PAY_PG13__{ship.upper()}__{s_fn}_TO_{e_fn}.pdf"
        )
        filename = filename.replace(" ", "_")

        outpath = os.path.join(SEA_PAY_PG13_FOLDER, filename)

        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=letter)
        c.setFont(FONT_NAME, FONT_SIZE)

        c.drawString(39, 689, "AFLOAT TRAINING GROUP SAN DIEGO (UIC. 49365)")
        c.drawString(373, 671, "X")
        c.setFont(FONT_NAME, 8)
        c.drawString(39, 650, "ENTITLEMENT")
        c.drawString(345, 641, "OPNAVINST 7220.14")

        c.setFont(FONT_NAME, FONT_SIZE)
        identity = f"{rate} {last}, {first}" if rate else f"{last}, {first}"
        c.drawString(39, 41, identity)

        y = 595
        c.drawString(38.8, y, f"____. REPORT CAREER SEA PAY FROM {s} TO {e}.")

        c.drawString(
            64,
            y - 24,
            f"Member performed eight continuous hours per day on-board: "
            f"{ship.upper()} Category A vessel."
        )

        c.drawString(356.26, 499.5, "_________________________")
        c.drawString(363.8, 487.5, "Certifying Official & Date")
        c.drawString(356.26, 427.5, "_________________________")
        c.drawString(384.1, 415.2, "FI MI Last Name")

        c.drawString(38.8, 83, "SEA PAY CERTIFIER")
        c.drawString(503.5, 40, "USN AD")

        c.save()
        buf.seek(0)

        template = PdfReader(TEMPLATE)
        overlay = PdfReader(buf)
        base = template.pages[0]
        base.merge_page(overlay.pages[0])

        writer = PdfWriter()
        writer.add_page(base)

        with open(outpath, "wb") as f:
            writer.write(f)

        flatten_pdf(outpath)
        log(f"CREATED → {filename}")
