import os
import zipfile
from datetime import datetime
from PyPDF2 import PdfReader, PdfWriter
from app.config import PG13_TEMPLATE_PATH


def generate_pg13_zip(sailor, output_dir):
    last = sailor["name"].split()[-1].upper()
    zip_path = os.path.join(output_dir, f"{last}_PG13.zip")

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for ship, start, end in sailor["events"]:
            pdf_path = generate_single_pg13(
                sailor["name"],
                ship,
                start,
                end,
                output_dir
            )
            zf.write(pdf_path, os.path.basename(pdf_path))

    return zip_path


def generate_single_pg13(name, ship, start_date, end_date, outdir):
    reader = PdfReader(PG13_TEMPLATE_PATH)
    writer = PdfWriter()

    page = reader.pages[0]
    writer.add_page(page)

    # Prepare clean ship name
    clean_ship = ship.replace("(", "").replace(")", "").strip()

    # Form fields from your template
    fields = {
        "NAME": name.upper(),
        "SHIP": clean_ship.upper(),
        "Date": f"{start_date} to {end_date}",
        "Subject": "ENTITLEMENT"
    }

    writer.update_page_form_field_values(writer.pages[0], fields)

    out_path = os.path.join(outdir, f"{clean_ship}.pdf")
    with open(out_path, "wb") as f:
        writer.write(f)

    return out_path
