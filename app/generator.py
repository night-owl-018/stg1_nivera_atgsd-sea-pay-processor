import os
import zipfile
from datetime import datetime

from reportlab.pdfgen import canvas
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase import pdfmetrics
from reportlab.lib.pagesizes import letter

from PyPDF2 import PdfReader, PdfWriter, PageObject

from app.config import PG13_TEMPLATE_PATH
from app.ship_matcher import match_ship


# Times New Roman for all overlaid text
FONT_PATH = "/app/app/fonts/times.ttf"
pdfmetrics.registerFont(TTFont("TimesNewRoman", FONT_PATH))


def format_mmddyy(date_obj: datetime.date) -> str:
    """Return date in MM/DD/YY format."""
    return date_obj.strftime("%m/%d/%y")


def generate_pg13_zip(sailor, output_dir):
    """Create a ZIP of PG-13 PDFs for one sailor."""
    last = sailor["name"].split()[0].upper()
    zip_path = os.path.join(output_dir, f"{last}.zip")

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for ship_raw, start, end in sailor["events"]:
            pdf_path = make_pg13_pdf(sailor["name"], ship_raw, start, end, output_dir)
            zf.write(pdf_path, os.path.basename(pdf_path))

    return zip_path


def make_pg13_pdf(name, ship_raw, start, end, root_dir):
    """
    Overlay sea-pay text on the NAVPERS 1070/613 template using page coordinates.

    Anchors (inches from bottom-left):
      - 'R' in REPORT:  X=0.783, Y=2.192
      - 'M' in Member:  X=0.533, Y=2.526
      - 'R' in RYAN (name box): X=0.218, Y=9.997
    """
    def inches(v: float) -> float:
        return v * 72.0

    # Clean & match ship name against your approved list
    ship = match_ship(ship_raw)

    # Build lines
    line1 = f"REPORT CAREER SEA PAY FROM {format_mmddyy(start)} TO {format_mmddyy(end)}."
    line2 = f"Member performed eight continuous hours per day on-board: {ship} Category A vessel."

    # Anchor coordinates in inches
    X1, Y1 = 0.783, 2.192      # 'R' in REPORT
    X2, Y2 = 0.533, 2.526      # 'M' in Member
    X_NAME, Y_NAME = 0.218, 9.997  # 'R' in RYAN in NAME box

    output_path = os.path.join(root_dir, f"{ship}.pdf")

    # 1. Read template
    template_reader = PdfReader(PG13_TEMPLATE_PATH)
    template_page = template_reader.pages[0]

    # 2. Create overlay PDF with just our three text lines
    overlay_path = os.path.join(root_dir, "overlay.pdf")
    c = canvas.Canvas(overlay_path, pagesize=letter)
    c.setFont("TimesNewRoman", 10)

    # REPORT line
    c.drawString(inches(X1), inches(Y1), line1)
    # Member line
    c.drawString(inches(X2), inches(Y2), line2)
    # Sailor name in NAME (LAST, FIRST, MIDDLE) box
    c.drawString(inches(X_NAME), inches(Y_NAME), name)

    c.save()

    # 3. Merge overlay onto template
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
