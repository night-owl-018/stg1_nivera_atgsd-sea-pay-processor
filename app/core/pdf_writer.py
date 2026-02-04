import os
import io
from datetime import datetime

from PyPDF2 import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.lib.colors import black

from app.core.logger import log
from app.core.config import TEMPLATE, FONT_NAME, FONT_SIZE, SEA_PAY_PG13_FOLDER, get_certifying_officer_name
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
# INTERNAL HELPER: Draw centered certifying officer name,
# slightly LOWER (closer to the signature line below)
# ------------------------------------------------
def _draw_centered_certifying_officer(c, sig_line_left_x, sig_line_y, name, y_above_line=6):
    """
    Centers `name` horizontally over the signature underline, and places it
    y_above_line points ABOVE the underline (lower than the old placement).
    """
    if not name:
        return

    sig_line = "_________________________"
    sig_line_w = c.stringWidth(sig_line, FONT_NAME, FONT_SIZE)
    sig_mid_x = sig_line_left_x + (sig_line_w / 2.0)

    # Place the text slightly above the underline, but lower than before
    c.drawCentredString(sig_mid_x, sig_line_y + y_above_line, name)


# ------------------------------------------------
# 🔹 NEW: CONSOLIDATED ALL MISSIONS (ALL SHIPS ON ONE FORM)
# ------------------------------------------------
def make_consolidated_all_missions_pdf(
    ship_groups,
    name,
    overall_start=None,
    overall_end=None,
    rate=None,
    last=None,
    first=None,
):
    """
    Creates a SINGLE PG-13 form with ALL missions across ALL ships for a member.
    Filename date range uses OVERALL SHEET reporting range when provided.
    """

    if not ship_groups:
        return

    # Prefer explicit identity from processing (prevents broken "_C_STG1..." prefixes)
    if not (rate and last and first):
        rate, last, first = resolve_identity(name)

    # Sort ships alphabetically for consistency
    sorted_ships = sorted(ship_groups.items())

    # Calculate total periods across all ships
    total_periods = sum(len(periods) for _, periods in sorted_ships)

    # Collect all periods (for content ordering)
    all_periods = []
    for _, periods in sorted_ships:
        all_periods.extend(periods)

    if not all_periods:
        return

    # Choose filename date range:
    # - If overall_start/overall_end are given -> use those (sheet range)
    # - Else -> fall back to first/last mission period
    if overall_start and overall_end:
        s_fn = overall_start.strftime("%m-%d-%Y")
        e_fn = overall_end.strftime("%m-%d-%Y")
    else:
        all_periods_sorted = sorted(all_periods, key=lambda g: g["start"])
        first_period = all_periods_sorted[0]
        last_period = all_periods_sorted[-1]
        s_fn = first_period["start"].strftime("%m-%d-%Y")
        e_fn = last_period["end"].strftime("%m-%d-%Y")

    # ✅ Desired prefix format: "STG1_HATTEN,FRANK__..."
    prefix = f"{rate}_{last},{first}" if rate else f"{last},{first}"
    filename = f"{prefix}__PG13__ALL_MISSIONS__{s_fn}_TO_{e_fn}.pdf"
    filename = filename.replace(" ", "_")

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

    # MAIN TEXT BLOCK - ALL SHIPS AND PERIODS
    y = 595
    line_spacing = 12
    current_line = 0

    for ship, periods in sorted_ships:
        periods_sorted = sorted(periods, key=lambda g: g["start"])

        for g in periods_sorted:
            s = g["start"].strftime("%m/%d/%Y")
            e = g["end"].strftime("%m/%d/%Y")

            c.drawString(
                38.8,
                y - (current_line * line_spacing),
                f"____. REPORT CAREER SEA PAY FROM {s} TO {e}."
            )
            current_line += 1

        c.drawString(
            64,
            y - (current_line * line_spacing),
            f"Member performed eight continuous hours per day on-board: "
            f"{ship.upper()} Category A vessel."
        )
        current_line += 1

        if ship != sorted_ships[-1][0]:
            current_line += 1

    # SIGNATURE AREAS
    content_height = current_line * line_spacing
    base_sig_y = 499.5
    sig_y = min(base_sig_y, 595 - content_height - 40)

    sig_left_x = 356.26

    c.drawString(sig_left_x, sig_y, "_________________________")
    c.drawString(363.8, sig_y - 12, "Certifying Official & Date")

    c.drawString(sig_left_x, sig_y - 72, "_________________________")

    # ✅ Certifying officer name: centered, no rate, no auto-periods, LOWER (closer to underline)
    certifying_officer_name = get_certifying_officer_name()
    _draw_centered_certifying_officer(c, sig_left_x, sig_y - 72, certifying_officer_name, y_above_line=6)

    # Always show the FI MI Last Name label below
    c.drawString(384.1, sig_y - 84.3, "FI MI Last Name")

    c.drawString(38.8, 83, "SEA PAY CERTIFIER")
    c.drawString(503.5, 40, "USN AD")

    c.save()
    buf.seek(0)

    # MERGE WITH TEMPLATE
    template = PdfReader(TEMPLATE)
    overlay = PdfReader(buf)
    base = template.pages[0]
    base.merge_page(overlay.pages[0])

    writer = PdfWriter()
    writer.add_page(base)

    with open(outpath, "wb") as f:
        writer.write(f)

    flatten_pdf(outpath)

    ship_count = len(sorted_ships)
    log(f"CREATED ALL MISSIONS PG-13 → {filename} ({ship_count} ships, {total_periods} periods on 1 form)")


# ------------------------------------------------
# 🔹 NEW: CONSOLIDATED PG-13 (MULTIPLE PERIODS ON ONE FORM)
# ------------------------------------------------
def make_consolidated_pdf_for_ship(ship, periods, name):
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
        c.drawString(38.8, y - (idx * line_spacing),
                    f"____. REPORT CAREER SEA PAY FROM {s} TO {e}.")

    ship_line_y = y - (len(periods_sorted) * line_spacing) - 12
    c.drawString(
        64,
        ship_line_y,
        f"Member performed eight continuous hours per day on-board: "
        f"{ship.upper()} Category A vessel."
    )

    sig_left_x = 356.26
    top_sig_y = 499.5
    bottom_line_y = 427.5

    c.drawString(sig_left_x, top_sig_y, "_________________________")
    c.drawString(363.8, 487.5, "Certifying Official & Date")
    c.drawString(sig_left_x, bottom_line_y, "_________________________")

    # ✅ Certifying officer name: centered + LOWER (closer to underline)
    certifying_officer_name = get_certifying_officer_name()
    _draw_centered_certifying_officer(c, sig_left_x, bottom_line_y, certifying_officer_name, y_above_line=6)

    # Always show the FI MI Last Name label below
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

        sig_left_x = 356.26
        top_sig_y = 499.5
        bottom_line_y = 427.5

        c.drawString(sig_left_x, top_sig_y, "_________________________")
        c.drawString(363.8, 487.5, "Certifying Official & Date")
        c.drawString(sig_left_x, bottom_line_y, "_________________________")

        # ✅ Certifying officer name: centered + LOWER (closer to underline)
        certifying_officer_name = get_certifying_officer_name()
        _draw_centered_certifying_officer(c, sig_left_x, bottom_line_y, certifying_officer_name, y_above_line=6)

        # Always show the FI MI Last Name label below
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
