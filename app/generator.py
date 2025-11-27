import os
import zipfile
from datetime import datetime

from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.pagesizes import letter

from PyPDF2 import PdfReader, PdfWriter, PageObject

from app.config import PG13_TEMPLATE_PATH
from app.ship_matcher import match_ship

# Register Times New Roman
FONT_PATH = "/app/app/fonts/times.ttf"
pdfmetrics.registerFont(TTFont("TimesNewRoman", FONT_PATH))

# Convenience
def inches(x): 
    return x * 72


def safe_filename(name):
    return "".join(c for c in name if c.isalnum() or c in ("_", "-", ".")).replace(" ", "_")


def format_mmddyy(d):
    return d.strftime("%m/%d/%y")


def generate_pg13_zip(sailor, output_dir):
    last = sailor["name"].split()[0].upper()
    zip_path = os.path.join(output_dir, f"{last}.zip")

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for ship_raw, start, end in sailor["events"]:
            pdf_path = make_pg13_pdf(sailor["name"], ship_raw, start, end, output_dir)
            z.write(pdf_path, os.path.basename(pdf_path))

    return zip_path


def make_pg13_pdf(name, ship_raw, start, end, root_dir):

    # Clean ship name properly
    ship = match_ship(ship_raw)

    line1 = f"REPORT CAREER SEA PAY FROM {format_mmddyy(start)} TO {format_mmddyy(end)}."
    line2 = f"Member performed eight continuous hours per day on-board: {ship.replace('USS ', '')} Category A vessel."

    # Coordinates from top-left reference (your requirement)
    PAGE_HEIGHT_INCHES = letter[1] / 72.0  # = 11

    X1, Y1 = 0.91, 8.43
    X2, Y2 = 0.91, 8.08
    X_NAME, Y_NAME = 0.26, 0.63

    # Convert to ReportLab coordinates (bottom-left)
    def to_pdf_y(top_inches):
        return inches(PAGE_HEIGHT_INCHES - top_inches)

    output_filename = safe_filename(ship + "_PG13.pdf")
    output_path = os.path.join(root_dir, output_filename)

    # LOAD TEMPLATE
    template_reader = PdfReader(PG13_TEMPLATE_PATH)
    template_page = template_reader.pages[0]

    # BUILD OVERLAY
    overlay_path = os.path.join(root_dir, "overlay.pdf")
    c = canvas.Canvas(overlay_path, pagesize=letter)
    c.setFont("TimesNewRoman", 10)

    # Draw fields exactly at your coordinates
    c.drawString(inches(X1), to_pdf_y(Y1), line1)
    c.drawString(inches(X2), to_pdf_y(Y2), line2)
    c.drawString(inches(X_NAME), to_pdf_y(Y_NAME), name)

    c.save()

    # MERGE TEMPLATE + OVERLAY
    overlay_reader = PdfReader(overlay_path)
    overlay_page = overlay_reader.pages[0]

    merged_page = PageObject.create_blank_page(
        width=template_page.mediabox.width,
        height=template_page.mediabox.height
    )

    # Correct merge order:
    merged_page.merge_page(template_page)   # background first
    merged_page.merge_page(overlay_page)    # text on top

    writer = PdfWriter()
    writer.add_page(merged_page)

    with open(output_path, "wb") as f:
        writer.write(f)

    return output_path
