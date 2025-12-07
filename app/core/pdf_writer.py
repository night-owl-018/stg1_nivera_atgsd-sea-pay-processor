import os
import io
from datetime import datetime

from PyPDF2 import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter

from app.core.logger import log
from app.core.config import TEMPLATE, FONT_NAME, FONT_SIZE, SEA_PAY_PG13_FOLDER
from app.core.rates import resolve_identity


# ------------------------------------------------
# FLATTEN PDF  (UNCHANGED)
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
# MAKE PG13 — ONE PDF PER SHIP, MULTIPLE PERIODS
# ------------------------------------------------
def make_pdf_for_ship(ship, periods, name):
    """
    Generate ONE PG13 per ship containing ALL valid continuous sea pay periods.

    - periods: list of dicts with keys "start", "end"
    - Single-day events display as DATE TO DATE.
    """

    if not periods:
        return

    rate, last, first = resolve_identity(name)
    ship_upper = ship.upper()

    # Sort periods by start date
    periods_sorted = sorted(periods, key=lambda g: g["start"])

    # Overall span for filename (min start, max end)
    first_start = periods_sorted[0]["start"]
    last_end = periods_sorted[-1]["end"]

    s_overall = first_start.strftime("%m/%d/%Y")
    e_overall = last_end.strftime("%m/%d/%Y")
    s_overall_fn = s_overall.replace("/", "-")
    e_overall_fn = e_overall.replace("/", "-")

    # Filename: one per ship per run
    filename = (
        f"{rate}_{last}_{first}"
        f"__SEA_PAY_PG13__{ship_upper}__{s_overall_fn}_TO_{e_overall_fn}.pdf"
    )
    filename = filename.replace(" ", "_")

    outpath = os.path.join(SEA_PAY_PG13_FOLDER, filename)

    # Build overlay
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.setFont(FONT_NAME, FONT_SIZE)

    # HEADER BLOCK (as before)
    c.drawString(39, 689, "AFLOAT TRAINING GROUP SAN DIEGO (UIC. 49365)")
    c.drawString(373, 671, "X")
    c.setFont(FONT_NAME, 8)
    c.drawString(39, 650, "ENTITLEMENT")
    c.drawString(345, 641, "OPNAVINST 7220.14")

    # Member identity
    c.setFont(FONT_NAME, FONT_SIZE)
    identity = f"{rate} {last}, {first}" if rate else f"{last}, {first}"
    c.drawString(39, 41, identity)

    # DESCRIPTION TEXT BLOCK
    y = 595
    c.drawString(
        38.8,
        y,
        "____. REPORT CAREER SEA PAY FOR THE FOLLOWING PERIODS:"
    )

    # List each valid period, including single-day events
    y -= 16
    c.setFont(FONT_NAME, 10)

    for g in periods_sorted:
        s = g["start"].strftime("%m/%d/%Y")
        e = g["end"].strftime("%m/%d/%Y")
        line = f"{s} TO {e}"
        c.drawString(64, y, line)
        y -= 12

    # Member performed 8 hours per day onboard line
    y -= 8
    c.setFont(FONT_NAME, FONT_SIZE)
    c.drawString(
        64,
        y,
        f"Member performed eight continuous hours per day on-board: "
        f"{ship_upper} Category A vessel."
    )

    # SIGNATURE AREA (unchanged)
    c.drawString(356.26, 499.5, "_________________________")
    c.drawString(363.8, 487.5, "Certifying Official & Date")
    c.drawString(356.26, 427.5, "_________________________")
    c.drawString(384.1, 415.2, "FI MI Last Name")

    c.drawString(38.8, 83, "SEA PAY CERTIFIER")
    c.drawString(503.5, 40, "USN AD")

    # Finish overlay
    c.save()
    buf.seek(0)

    # Merge overlay with template
    template = PdfReader(TEMPLATE)
    overlay = PdfReader(buf)
    base = template.pages[0]
    base.merge_page(overlay.pages[0])

    writer = PdfWriter()
    writer.add_page(base)

    # Save final PG13
    with open(outpath, "wb") as f:
        writer.write(f)

    flatten_pdf(outpath)
    log(f"CREATED → {filename}")
