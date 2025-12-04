import os
import io
from datetime import datetime

from PyPDF2 import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.lib.colors import black

from app.core.logger import log
from app.core.config import OUTPUT_DIR, TEMPLATE, FONT_NAME, FONT_SIZE
from app.core.rates import resolve_identity


# ------------------------------------------------
# FLATTEN PDF
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
# ONE PDF PER PERIOD  (REPLACEMENT FOR OLD make_pdf_for_ship)
# ------------------------------------------------
def make_pdf_for_ship(ship, periods, name):
    """
    Updated behavior:
    - Create ONE PDF PER PERIOD instead of one PDF per ship.
    - No other logic or formatting was changed.
    """
    if not periods:
        return

    rate, last, first = resolve_identity(name)
    periods_sorted = sorted(periods, key=lambda g: g["start"])

    for g in periods_sorted:
        # Format single period dates
        s = g["start"].strftime("%m/%d/%Y")
        e = g["end"].strftime("%m/%d/%Y")

        prefix = f"{rate}_" if rate else ""
        filename = (
            f"{prefix}{last}_{first}_{ship}_"
            f"{s.replace('/', '-')}_TO_{e.replace('/', '-')}.pdf"
        )
        filename = filename.replace(" ", "_")

        outpath = os.path.join(OUTPUT_DIR, filename)

        # Build overlay PDF with 1 period
        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=letter)
        c.setFont(FONT_NAME, FONT_SIZE)

        # Header (unchanged)
        c.drawString(39, 689, "AFLOAT TRAINING GROUP SAN DIEGO (UIC. 49365)")
        c.drawString(373, 671, "X")
        c.setFont(FONT_NAME, 8)
        c.drawString(39, 650, "ENTITLEMENT")
        c.drawString(345, 641, "OPNAVINST 7220.14")

        c.setFont(FONT_NAME, FONT_SIZE)
        c.drawString(
            39,
            41,
            f"{rate} {last}, {first}" if rate else f"{last}, {first}"
        )

        # ONE PERIOD ONLY
        y = 595
        c.drawString(38.8, y, f"____. REPORT CAREER SEA PAY FROM {s} TO {e}.")
        c.drawString(
            64,
            y - 24,
            f"Member performed eight continuous hours per day on-board: {ship} Category A vessel."
        )

        # Signature area (unchanged)
        c.drawString(356.26, 499.5, "_________________________")
        c.drawString(363.8, 487.5, "Certifying Official & Date")
        c.drawString(356.26, 427.5, "_________________________")
        c.drawString(384.1, 415.2, "FI MI Last Name")

        c.drawString(38.8, 83, "SEA PAY CERTIFIER")
        c.drawString(503.5, 40, "USN AD")

        c.save()
        buf.seek(0)

        # Merge with NAVPERS template
        template = PdfReader(TEMPLATE)
        overlay = PdfReader(buf)
        base = template.pages[0]
        base.merge_page(overlay.pages[0])

        writer = PdfWriter()
        writer.add_page(base)

        # Write final PDF
        with open(outpath, "wb") as f:
            writer.write(f)

        flatten_pdf(outpath)

        log(f"CREATED → {filename}")
