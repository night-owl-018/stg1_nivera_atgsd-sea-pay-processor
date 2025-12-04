import os
from datetime import datetime
from PyPDF2 import PdfMerger

from app.core.logger import log
from app.core.config import OUTPUT_DIR


def merge_all_pdfs():
    pdf_files = sorted([
        f for f in os.listdir(OUTPUT_DIR)
        if f.lower().endswith(".pdf")
        and not f.startswith("MERGED_SeaPay_Forms_")
        and not f.startswith("MARKED_")
    ])

    if not pdf_files:
        log("NO PDFs TO MERGE")
        return None

    log(f"MERGING {len(pdf_files)} PDFs...")
    merger = PdfMerger()

    for pdf_file in pdf_files:
        pdf_path = os.path.join(OUTPUT_DIR, pdf_file)
        bookmark = os.path.splitext(pdf_file)[0]
        merger.append(pdf_path, outline_item=bookmark)
        log(f"ADDED BOOKMARK → {bookmark}")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    merged_filename = f"MERGED_SeaPay_Forms_{ts}.pdf"
    merged_path = os.path.join(OUTPUT_DIR, merged_filename)

    try:
        merger.write(merged_path)
        merger.close()
        log(f"MERGED PDF CREATED → {merged_filename}")
        return merged_filename
    except Exception as e:
        log(f"❌ MERGE FAILED → {e}")
        return None
