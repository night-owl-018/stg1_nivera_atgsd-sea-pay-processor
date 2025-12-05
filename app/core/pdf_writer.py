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
# FLATTEN PDF
# ------------------------------------------------
def flatten_pdf(path):
    """
    Simple 'flatten' pass:
    - Remove annotations
    - Normalize /Contents into a single stream
    """
    try:
        reader = PdfReader(path)
        writer = PdfWriter()

        for page in reader.pages:
            # Strip annotations
            if "/Annots" in page:
                del page["/Annots"]

            contents = page.get("/Contents")
            if isinstance(contents, list):
                merged = b""
                for obj in contents:
                    merged += obj.get_data()
                page["/Contents"] = writer._add_object(  # type: ignore[attr-defined]
                    writer._add_stream(merged)           # type: ignore[attr-defined]
                )

            writer.add_page(page)

        with open(path, "wb") as f:
            writer.write(f)

        log(f"FLATTENED → {os.path.basename(path)}")
    except Exception as e:
        log(f"⚠️ FLATTEN FAILED → {e}")


# ------------------------------------------------
# NAVPERS 1070/613 PER SHIP (NOW: PER PERIOD)
# ------------------------------------------------
def make_pdf_for_ship(ship, periods, name):
    """
    Updated behavior:
    - Create ONE PDF PER PERIOD instead of one PDF per ship.
    - Filenames follow SEA PAY PG13 pattern.
    - Output goes to /output/SEA_PAY_PG13/.
    """
    if not periods:
        return

    rate, last, first = resolve_identity(name)
    periods_sorted = sorted(periods, key=lambda g: g["start"])

    for g in periods_sorted:
        # Format single period dates
        s = g["start"].strftime("%m/%d/%Y")
        e = g["end"].strftime("%m/%d/%Y")

        # Build SEA PAY PG13 filename per period
        # <RATE>_<LAST>_<FIRST>__SEA_PAY_PG13__<SHIP>__<START>_TO_<END>.pdf
        filename = (
            f"{rate}_{last}_{first}"
            f"__SEA_PAY_PG13__{ship.upper()}__"
            f"{s.replace('/', '-')}_TO_{e.replace('/', '-')}.pdf"
        )
        filename = filename.replace(" ", "_")

        outpath = os.path.join(SEA_PAY_PG13_FOLDER, filename)

        # Build overlay PDF with 1 period
        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=letter)
        c.setFont(FONT_NAME, FONT_SIZE)

        # Header (kept same as your previous layout)
        c.drawString(39, 689, "AFLOAT TRAINING GROUP SAN DIEGO (UIC. 49365)")
        c.drawString(373, 671, "X")
        c.setFont(FONT_NAME, 8)
        c.drawString(39, 650, "ENTITLEMENT")

        # Identity block (same pattern as before)
        identity_line = f"{rate} {last}, {first}".strip()
        c.setFont(FONT_NAME, 10)
        c.drawString(72, 622, identity_line[:40])
        c.drawString(72, 606, identity_line[40:80])

        # Ship and dates
        c.drawString(72, 574, ship.upper())
        c.drawString(360, 574, s)
        c.drawString(460, 574, e)

        c.showPage()
        c.save()
        buf.seek(0)

        # Merge overlay with NAVPERS template
        template = PdfReader(TEMPLATE)
        overlay = PdfReader(buf)
        base = template.pages[0]
        base.merge_page(overlay.pages[0])

        writer = PdfWriter()
        writer.add_page(base)

        # Write final PDF
        os.makedirs(os.path.dirname(outpath), exist_ok=True)
        with open(outpath, "wb") as f:
            writer.write(f)

        flatten_pdf(outpath)
        log(f"CREATED → {filename}")
