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

# -----------------------------
# Font setup
# -----------------------------
FONT_PATH = "/app/app/fonts/times.ttf"
pdfmetrics.registerFont(TTFont("TimesNewRoman", FONT_PATH))


def format_mmddyy(date_obj: datetime.date) -> str:
    """Convert date object to MM/DD/YY."""
    return date_obj.strftime("%m/%d/%y")


def inches(val: float) -> float:
    """Convert inches to PDF points (72 points per inch)."""
    return val * 72.0


# -----------------------------------------------------------
# ZIP generator – unchanged logic
# -----------------------------------------------------------
def generate_pg13_zip(sailor, output_dir):
    """
    Create one PG-13 per ship range and package in a ZIP named by
    the first word in the sailor's name.
    """
    last = sailor["name"].split()[0].upper()
    zip_path = os.path.join(output_dir, f"{last}.zip")

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for ship_raw, start, end in sailor["events"]:
            pdf_path = make_pg13_pdf(sailor["name"], ship_raw, start, end, output_dir)
            zf.write(pdf_path, os.path.basename(pdf_path))

    return zip_path


# -----------------------------------------------------------
# Single PG-13 generator for one ship / date range
# -----------------------------------------------------------
def make_pg13_pdf(name, ship_raw, start, end, root_dir):
    """
    Use NAVPERS 1070/613 template and draw three text lines at
    fixed coordinates (from your grid overlay):

      1. REPORT CAREER SEA PAY FROM mm/dd/yy TO mm/dd/yy.
      2. Member performed eight continuous hours per day ...
      3. NAME (LAST, FIRST, MIDDLE) value at the bottom box.
    """
    output_path = os.path.join(root_dir, f"{ship_raw}.pdf")

    # Clean / match ship name to nearest official hull name
    ship = match_ship(ship_raw)

    # Text lines
    line1 = f"REPORT CAREER SEA PAY FROM {format_mmddyy(start)} TO {format_mmddyy(end)}."
    line2 = f"Member performed eight continuous hours per day on-board: {ship} Category A vessel."
    name_text = name

    # Load template page
    template_reader = PdfReader(PG13_TEMPLATE_PATH)
    template_page = template_reader.pages[0]

    # -------------------------------
    # Prepare overlay canvas
    # -------------------------------
    overlay_path = os.path.join(root_dir, "overlay.pdf")
    c = canvas.Canvas(overlay_path, pagesize=letter)
    c.setFont("TimesNewRoman", 10)

    page_width, page_height = letter  # 612 x 792 points

    # Your grid measurements are from the *top-left* in inches.
    # ReportLab's origin is bottom-left, so we flip Y:
    #
    #   y_from_bottom = page_height - inches(y_from_top)

    # 1) REPORT CAREER SEA PAY FROM ...
    x_report_top_in = 0.783
    y_report_top_in = 2.192
    c.drawString(
        inches(x_report_top_in),
        page_height - inches(y_report_top_in),
        line1,
    )

    # 2) Member performed eight continuous hours per day ...
    x_member_top_in = 0.533
    y_member_top_in = 2.526
    c.drawString(
        inches(x_member_top_in),
        page_height - inches(y_member_top_in),
        line2,
    )

    # 3) NAME (LAST, FIRST, MIDDLE) value box at bottom
    #    Label top was ~9.997 in from top. We drop text a bit
    #    lower so it sits inside the box, under the label.
    x_name_top_in = 0.218
    y_name_top_in = 10.20   # slightly below label line
    c.drawString(
        inches(x_name_top_in),
        page_height - inches(y_name_top_in),
        name_text,
    )

    c.save()

    # -------------------------------
    # Merge overlay with template
    # -------------------------------
    overlay_reader = PdfReader(overlay_path)
    overlay_page = overlay_reader.pages[0]

    merged_page = PageObject.create_blank_page(
        width=template_page.mediabox.width,
        height=template_page.mediabox.height,
    )
    # First the original form, then our text on top
    merged_page.merge_page(template_page)
    merged_page.merge_page(overlay_page)

    writer = PdfWriter()
    writer.add_page(merged_page)

    with open(output_path, "wb") as f:
        writer.write(f)

    return output_path
