import os
import zipfile
from datetime import datetime

from pypdf import PdfReader, PdfWriter
from app.config import PG13_TEMPLATE_PATH


def format_mmddyy(date_obj):
    return date_obj.strftime("%m/%d/%y")


def generate_pg13_zip(sailor, output_dir):
    last = sailor["name"].split()[0].upper()
    zip_path = os.path.join(output_dir, f"{last}.zip")

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for ship, start, end in sailor["events"]:
            pdf_path = make_pg13_pdf(sailor["name"], ship, start, end, output_dir)
            zf.write(pdf_path, os.path.basename(pdf_path))

    return zip_path


def make_pg13_pdf(name, ship, start, end, root_dir):
    output_path = os.path.join(root_dir, f"{ship}.pdf")

    reader = PdfReader(PG13_TEMPLATE_PATH)
    writer = PdfWriter()

    # Copy pages
    for page in reader.pages:
        writer.add_page(page)

    # Extract original fields
    fields = reader.get_fields()

    # Rebuild a clean AcroForm
    writer._root_object.update({
        "/AcroForm": {
            "/Fields": []
        }
    })

    # Add valid fields only
    for field_key, field_obj in fields.items():
        try:
            writer._root_object["/AcroForm"]["/Fields"].append(
                field_obj.indirect_reference
            )
        except:
            continue

    # FIELD CONTENT
    date_str = f"{format_mmddyy(start)} TO {format_mmddyy(end)}. REPORT CAREER SEA PAY FROM"
    ship_str = f"Member performed eight continuous hours per day on-board: {ship} Category A vessel"
    name_str = name

    writer.update_page_form_field_values(
        writer.pages[0],
        {
            "NAME": name_str,
            "Date": date_str,
            "SHIP": ship_str
        }
    )

    # Fix display
    if "/AcroForm" in writer._root_object:
        writer._root_object["/AcroForm"].update({"/NeedAppearances": True})

    # Save final PDF
    with open(output_path, "wb") as f:
        writer.write(f)

    return output_path
