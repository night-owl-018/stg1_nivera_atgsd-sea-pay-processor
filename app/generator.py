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


# Register Times New Roman (same as before)
FONT_PATH = "/app/app/fonts/times.ttf"
pdfmetrics.registerFont(TTFont("TimesNewRoman", FONT_PATH))


def format_mmddyy(date_obj):
    """Convert date object to MM/DD/YY."""
    return date_obj.strftime("%m/%d/%y")


def get_field_rect(page, field_name: str):
    """
    Look through /Annots on the page and return the Rect for a given field name (/T).
    Returns (x0, y0, x1, y1) as floats, or None if not found.
    """
    annots = page.get("/Annots", [])
    if annots is None:
        return None

    for a_ref in annots:
        annot = a_ref.get_object()
        t = annot.get("/T")
        if t and t == field_name:
            rect = annot.get("/Rect")
            if rect and len(rect) == 4:
                x0, y0, x1, y1 = [float(v) for v in rect]
                return x0, y0, x1, y1

    return None


def draw_in_rect(c: canvas.Canvas, rect, text: str, font_name="TimesNewRoman", font_size=10, padding_x=2, padding_y=2):
    """
    Draw a single-line string inside a field rectangle, aligned near the top-left.
    PDF coordinates: origin bottom-left, same as ReportLab.
    """
    x0, y0, x1, y1 = rect
    width = x1 - x0
    height = y1 - y0

    c.setFont(font_name, font_size)

    # Top-left-ish inside the rect
    x = x0 + padding_x
    y = y1 - font_size - padding_y

    c.drawString(x, y, text)


def generate_pg13_zip(sailor, output_dir):
    """
    Create one PG-13 per ship range and package in a ZIP named by Sailor's first word of name.
    """
    last = sailor["name"].split()[0].upper()
    zip_path = os.path.join(output_dir, f"{last}.zip")

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for ship_raw, start, end in sailor["events"]:
            pdf_path = make_pg13_pdf(sailor["name"], ship_raw, start, end, output_dir)
            zf.write(pdf_path, os.path.basename(pdf_path))

    return zip_path


def make_pg13_pdf(name, ship_raw, start, end, root_dir):
    """
    Use the NAVPERS 1070/613 form template and write:
      - REPORT CAREER SEA PAY FROM mm/dd/yy TO mm/dd/yy.   into Date field
      - Member performed eight continuous hours per day...  into SHIP field
      - NAME (LAST, FIRST, MIDDLE)                         into NAME field (below label)
    Positions come from AcroForm field rectangles, not guessed coordinates.
    """
    output_path = os.path.join(root_dir, f"{ship_raw}.pdf")

    # Clean / match ship name to official list (CHAFEE, CHOSIN, etc.)
    ship = match_ship(ship_raw)

    # Build text lines
    line1 = f"REPORT CAREER SEA PAY FROM {format_mmddyy(start)} TO {format_mmddyy(end)}."
    line2 = f"Member performed eight continuous hours per day on-board: {ship} Category A vessel."
    name_text = name

    # Load template
    template_reader = PdfReader(PG13_TEMPLATE_PATH)
    template_page = template_reader.pages[0]

    # Get rects for the form fields
    rect_date = get_field_rect(template_page, "Date")
    rect_ship = get_field_rect(template_page, "SHIP")
    rect_name = get_field_rect(template_page, "NAME")

    if not rect_date or not rect_ship or not rect_name:
        # Debug info in logs so we can see what failed
        raise Exception(
            f"Failed to locate one or more field rects in template. "
            f"Date={rect_date}, SHIP={rect_ship}, NAME={rect_name}"
        )

    # Create overlay PDF with our text
    overlay_path = os.path.join(root_dir, "overlay.pdf")
    c = canvas.Canvas(overlay_path, pagesize=letter)

    # All fonts Times New Roman 10 per your instructions
    draw_in_rect(c, rect_date, line1, font_size=10)
    draw_in_rect(c, rect_ship, line2, font_size=10)
    draw_in_rect(c, rect_name, name_text, font_size=10)

    c.save()

    # Read overlay and merge with template
    overlay_reader = PdfReader(overlay_path)
    overlay_page = overlay_reader.pages[0]

    merged_page = PageObject.create_blank_page(
        width=template_page.mediabox.width,
        height=template_page.mediabox.height,
    )
    merged_page.merge_page(template_page)
    merged_page.merge_page(overlay_page)

    writer = PdfWriter()
    writer.add_page(merged_page)

    with open(output_path, "wb") as f:
        writer.write(f)

    return output_path
