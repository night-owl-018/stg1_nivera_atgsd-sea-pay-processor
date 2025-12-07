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
# RESTORED ORIGINAL FORMAT — ONLY FILENAMES UPDATED
# ------------------------------------------------
def make_pdf_for_ship(ship, periods, name):
    """
    EXACT restoration of your original PG13 formatting.
    ONLY changes:
        • Output folder: SEA_PAY_PG13_FOLDER
        • FILENAMES updated to new format:
              RATE_LAST_FIRST__SEA_PAY_PG13__SHIP__START_TO_END.pdf
        • Ship forced uppercase
    """

    if not periods:
        return

    rate, last, first = resolve_identity(name)
    periods_sorted = sorted(periods, key=lambda g: g["start"])

    for g in periods_sorted:
        # Dates for filename OR print
        s = g["start"].strftime("%m/%d/%Y")
        e = g["end"].strftime("%m/%d/%Y")

        s_fn = s.replace("/", "-")
        e_fn = e.replace("/", "-")

        # ⭐ NEW FILENAME FORMAT
        filename = (
            f"{rate}_{last}_{first}"
            f"__SEA_PAY_PG13__{ship.upper()}__{s_fn}_TO_{e_fn}.pdf"
        )
        filename = filename.replace(" ", "_")

        outpath = os.path.join(SEA_PAY_PG13_FOLDER, filename)

        # ⭐ ORIGINAL PG13 OVERLAY CODE (UNTOUCHED)
        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=letter)
        c.setFont(FONT_NAME, FONT_SIZE)

        # HEADER BLOCK (original coordinates)
        c.drawString(39, 689, "AFLOAT TRAINING GROUP SAN DIEGO (UIC. 49365)")
        c.drawString(373, 671, "X")
        c.setFont(FONT_NAME, 8)
        c.drawString(39, 650, "ENTITLEMENT")
        c.drawString(345, 641, "OPNAVINST 7220.14")

        # Member identity
        c.setFont(FONT_NAME, FONT_SIZE)
        identity = f"{rate} {last}, {first}" if rate else f"{last}, {first}"
        c.drawString(39, 41, identity)

        # MAIN TEXT BLOCK — EXACT ORIGINAL FORMAT
        y = 595
        c.drawString(38.8, y, f"____. REPORT CAREER SEA PAY FROM {s} TO {e}.")

        c.drawString(
            64,
            y - 24,
            f"Member performed eight continuous hours per day on-board: "
            f"{ship.upper()} Category A vessel."
        )

        # SIGNATURE AREAS — EXACT COORDS
        c.drawString(356.26, 499.5, "_________________________")
        c.drawString(363.8, 487.5, "Certifying Official & Date")
        c.drawString(356.26, 427.5, "_________________________")
        c.drawString(384.1, 415.2, "FI MI Last Name")

        c.drawString(38.8, 83, "SEA PAY CERTIFIER")
        c.drawString(503.5, 40, "USN AD")

        # Finish overlay
        c.save()
        buf.seek(0)

        # MERGE WITH TEMPLATE (unchanged original behavior)
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
