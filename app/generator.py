import os
import zipfile
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from app.config import PG13_TEMPLATE_PATH


def generate_pg13_zip(sailor, output_dir):
    last = sailor["name"].split()[0].upper()
    zip_path = os.path.join(output_dir, f"{last}.zip")

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for ship, start, end in sailor["events"]:
            pdf_path = make_pg13_pdf(sailor["name"], ship, start, end, output_dir)
            zf.write(pdf_path, os.path.basename(pdf_path))

    return zip_path


def make_pg13_pdf(name, ship, start, end, root):
    path = os.path.join(root, f"{ship}.pdf")
    c = canvas.Canvas(path, pagesize=letter)

    c.drawString(100, 700, f"Sailor: {name}")
    c.drawString(100, 680, f"Ship: {ship}")
    c.drawString(100, 660, f"Start: {start}")
    c.drawString(100, 640, f"End: {end}")

    c.save()
    return path
