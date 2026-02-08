"""
Module to add certifying officer information to TORIS certification sheets.
"""

import os
import io
from PyPDF2 import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter

from app.core.logger import log
from app.core.config import get_certifying_officer_name


def add_certifying_officer_to_toris(input_pdf_path, output_pdf_path):
    """
    Add the certifying officer's name to a TORIS Sea Duty Certification Sheet PDF.
    
    The name is placed BETWEEN two underscore lines, ABOVE the 
    "PRINTED NAME OF CERTIFYING OFFICER" label.
    
    Layout:
    ___________________________________  (first underscore)
    STG1 NIVERA, R. N.                   (name - placed here)
    ___________________________________  (second underscore)
    PRINTED NAME OF CERTIFYING OFFICER   (label)
    
    Args:
        input_pdf_path: Path to the TORIS sheet PDF
        output_pdf_path: Path where the updated PDF should be saved
    """
    try:
        # Get the certifying officer name
        certifying_officer_name = get_certifying_officer_name()
        
        if not certifying_officer_name:
            # If no certifying officer is set, just copy the file as-is
            log(f"NO CERTIFYING OFFICER SET → Copying TORIS as-is: {os.path.basename(input_pdf_path)}")
            if input_pdf_path != output_pdf_path:
                import shutil
                shutil.copy2(input_pdf_path, output_pdf_path)
            return
        
        # COORDINATES for certifying officer name:
        # Based on TORIS form structure where:
        # - "PRINTED NAME OF CERTIFYING OFFICER" label is around Y=361
        # - Second underscore line is about 12 points above label (Y=373)
        # - Name goes about 6 points above the second underscore (Y=379)
        # This places the name in the space between the two underscore lines
        
        x_position = 63   # Left margin alignment
        y_position = 379  # Between the two underscore lines
        
        log(f"Placing '{certifying_officer_name}' at (X={x_position}, Y={y_position})")
        
        # Create an overlay with the certifying officer name
        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=letter)
        c.setFont("Helvetica-Bold", 10)
        
        c.drawString(x_position, y_position, certifying_officer_name)
        c.save()
        buf.seek(0)
        
        # Read the existing PDF
        reader = PdfReader(input_pdf_path)
        overlay = PdfReader(buf)
        writer = PdfWriter()
        
        # Merge the overlay onto the last page (where signature section is)
        for i, page in enumerate(reader.pages):
            if i == len(reader.pages) - 1:  # Last page
                page.merge_page(overlay.pages[0])
            writer.add_page(page)
        
        # Write the output
        with open(output_pdf_path, "wb") as f:
            writer.write(f)
        
        log(f"✅ ADDED CERTIFYING OFFICER TO TORIS → {certifying_officer_name}")
        
    except Exception as e:
        log(f"⚠️ ERROR ADDING CERTIFYING OFFICER TO TORIS → {e}")
        # On error, copy the original file
        if input_pdf_path != output_pdf_path:
            import shutil
            try:
                shutil.copy2(input_pdf_path, output_pdf_path)
                log(f"FALLBACK COPY CREATED → {os.path.basename(input_pdf_path)}")
            except Exception as e2:
                log(f"⚠️ FALLBACK COPY FAILED → {e2}")
