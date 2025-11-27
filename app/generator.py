import os
import zipfile
from datetime import datetime

from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from PyPDF2 import PdfReader, PdfWriter, PageObject

from app.config import PG13_TEMPLATE_PATH
from app.ship_matcher import match_ship

# Register Times New Roman font
FONT_PATH = "/app/app/fonts/times.ttf"
pdfmetrics.registerFont(TTFont("TimesNewRoman", FONT_PATH))


def format_mmddyy(date_obj):
    return date_obj.strftime("%m/%d/%y")


def generate_pg13_zip(sailor, output_dir):
    last = sailor["name"].split()[0].upper()
    zip_path = os.path.join(output_dir, f"{last}.zip")

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for ship_raw, start, end in sailor["events"]:
            pdf_path = make_pg13_pdf(sailor["name"], ship_raw, start, end, output_dir)
            zf.write(pdf_path, os.path.basename(pdf_path))

    return zip_path


# ---------------------------------------------
#   FIXED COORDINATES (CONFIRMED ACCURATE)
# ---------------------------------------------

# Points = exact positions on NAVPERS 1070/613
X_REPORT = 65   # REPORT CAREER SEA PAY FROM...
Y_REPORT = 645

X_MEMBER = 65   # Member performed eight continuous hours...
Y_MEMBER = 620

X_NAME = 19     # NAME (LAST, FIRST, MIDDLE)
Y_NAME = 55


def make_pg13_pdf(name, ship_raw, start, end, root_dir):
    """
    Overlay values at exactly mapped coordinates (no form fields).
    """

    output_path = os.path.join(root_dir, f"{ship_raw}.pdf")

    ship = match_ship(ship_raw)

    line1 = f"REPORT CAREER SEA PAY FROM {format_mmddyy(start)} TO {format_mmddyy(end)}."
    line2 = f"Member performed eight continuous hours per day on-board: {ship} Category A vessel."

    # Load template
    template_reader = PdfReader(PG13_TEMPLATE_PATH)
    template_page = template_reader.pages[0]

    # Create overlay
    overlay_path = os.path.join(root_dir, "overlay.pdf")
    c = canvas.Canvas(overlay_path, pagesize=letter)
    c.setFont("TimesNewRoman", 10)

    # Draw three lines in correct mapped positions
    c.drawString(X_REPORT, Y_REPORT, line1)
    c.drawString(X_MEMBER, Y_MEMBER, line2)
    c.drawString(X_NAME, Y_NAME, name)

    c.save()

    # Merge with template
    overlay_reader = PdfReader(overlay_path)
    overlay_page = overlay_reader.pages[0]

    merged_page = PageObject.create_blank_page(
        width=template_page.mediabox.width,
        height=template_page.mediabox.height
    )
    merged_page.merge_page(template_page)
    merged_page.merge_page(overlay_page)

    writer = PdfWriter()
    writer.add_page(merged_page)

    with open(output_path, "wb") as f:
        writer.write(f)

    return output_path
