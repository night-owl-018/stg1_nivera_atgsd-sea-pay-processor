import os
import zipfile
from datetime import datetime

from reportlab.pdfgen import canvas
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase import pdfmetrics
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen.canvas import Canvas

from PyPDF2 import PdfReader, PdfWriter, PageObject

from app.config import PG13_TEMPLATE_PATH
from app.ship_matcher import match_ship


# Register Times New Roman
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



def make_pg13_pdf(name, ship_raw, start, end, root_dir):
    """Overlay text on top of PG-13 template using PyPDF2 merge."""
    output_path = os.path.join(root_dir, f"{ship_raw}.pdf")

    # Clean & match ship name
    ship = match_ship(ship_raw)

    # Build strings
    line1 = f"REPORT CAREER SEA PAY FROM {format_mmddyy(start)} TO {format_mmddyy(end)}."
    line2 = f"Member performed eight continuous hours per day on-board: {ship} Category A vessel."

    # Coordinates converted to points
    def inches(x): return x * 72

    # Exact coordinate pairs (inches)
    X1, Y1 = 0.91, 8.43
    X2, Y2 = 0.91, 8.08
    X_NAME, Y_NAME = 0.26, 0.63

    # 1. Load template page
    template_reader = PdfReader(PG13_TEMPLATE_PATH)
    template_page = template_reader.pages[0]

    # 2. Create overlay (transparent canvas)
    overlay_path = os.path.join(root_dir, "overlay.pdf")
    c = canvas.Canvas(overlay_path, pagesize=letter)

    c.setFont("TimesNewRoman", 10)

    # Draw all fields
    c.drawString(inches(X1), inches(11 - Y1), line1)
    c.drawString(inches(X2), inches(11 - Y2), line2)
    c.drawString(inches(X_NAME), inches(11 - Y_NAME), name)

    c.save()

    # 3. Merge template + overlay
    overlay_reader = PdfReader(overlay_path)
    overlay_page = overlay_reader.pages[0]

    merged_page = PageObject.create_blank_page(
        width=template_page.mediabox.width,
        height=template_page.mediabox.height
    )

    merged_page.merge_page(template_page)
    merged_page.merge_page(overlay_page)

    # 4. Write final PDF
    writer = PdfWriter()
    writer.add_page(merged_page)

    with open(output_path, "wb") as f:
        writer.write(f)

    return output_path
