import os
import zipfile
from datetime import datetime

from PyPDF2 import PdfReader, PdfWriter
from app.config import PG13_TEMPLATE_PATH


def format_mmddyy(date_obj):
    """Convert date object to MM/DD/YY format."""
    return date_obj.strftime("%m/%d/%y")


def generate_pg13_zip(sailor, output_dir):
    """
    Create a ZIP of PG-13 PDFs for a single sailor.
    Each event tuple: (ship, start_date, end_date)
    """
    # Use LAST name (first token) for zip name
    last = sailor["name"].split()[0].upper()
    zip_path = os.path.join(output_dir, f"{last}.zip")

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for ship, start, end in sailor["events"]:
            pdf_path = make_pg13_pdf(sailor["name"], ship, start, end, output_dir)
            zf.write(pdf_path, os.path.basename(pdf_path))

    return zip_path


def make_pg13_pdf(name, ship, start, end, root_dir):
    """
    Fill the NAVPERS 1070/613 PG-13 template with:
      - NAME  -> NAME (LAST, FIRST, MIDDLE) field
      - Date  -> 'MM/DD/YY TO MM/DD/YY. REPORT CAREER SEA PAY FROM'
      - SHIP  -> 'Member performed eight continuous hours per day
                  on-board: <SHIP> Category A vessel'
    """

    output_path = os.path.join(root_dir, f"{ship}.pdf")

    # 1) Load the PG-13 template
    reader = PdfReader(PG13_TEMPLATE_PATH)

    # 2) Clone the full document into a writer
    #    This is the safe way: it carries over AcroForm and fields
    writer = PdfWriter(clone_from=reader)

    # 3) Build the exact strings you wanted
    date_value = f"{format_mmddyy(start)} TO {format_mmddyy(end)}. REPORT CAREER SEA PAY FROM"
    ship_value = f"Member performed eight continuous hours per day on-board: {ship} Category A vessel"
    name_value = name

    # 4) Update form fields on the first page
    #    We do NOT call writer.get_fields() – only the reader has that.
    writer.update_page_form_field_values(
        writer.pages[0],
        {
            "NAME": name_value,
            "Date": date_value,
            "SHIP": ship_value,
        },
    )

    # 5) Write out the finished PG-13 PDF
    with open(output_path, "wb") as f:
        writer.write(f)

    return output_path
