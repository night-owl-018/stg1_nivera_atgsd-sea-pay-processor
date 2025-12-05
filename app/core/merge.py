import os
from PyPDF2 import PdfMerger

from app.core.logger import log
from app.core.config import (
    SEA_PAY_PG13_FOLDER,
    TORIS_CERT_FOLDER,
    SUMMARY_PDF_FOLDER,
    PACKAGE_FOLDER,
)


def _merge_folder_pdfs(source_folder, out_path, description):
    files = [
        f for f in sorted(os.listdir(source_folder))
        if f.lower().endswith(".pdf")
    ]
    if not files:
        log(f"No PDFs found to merge for {description}.")
        return False

    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    merger = PdfMerger()
    for fn in files:
        full_path = os.path.join(source_folder, fn)
        try:
            merger.append(full_path)
            log(f"ADDED TO {description} → {fn}")
        except Exception as e:
            log(f"⚠️ SKIPPED {fn} IN {description} → {e}")

    try:
        merger.write(out_path)
        merger.close()
        log(f"MERGED PDF CREATED → {os.path.basename(out_path)}")
        return True
    except Exception as e:
        log(f"❌ MERGE FAILED FOR {description} → {e}")
        return False


def merge_all_pdfs():
    """
    Build the final PACKAGE set:

    /output/PACKAGE/
        MERGED_SEA_PAY_PG13.pdf
        MERGED_TORIS_SEA_PAY_CERT_SHEETS.pdf
        MERGED_SUMMARY.pdf
    """
    os.makedirs(PACKAGE_FOLDER, exist_ok=True)

    merged_pg13 = os.path.join(PACKAGE_FOLDER, "MERGED_SEA_PAY_PG13.pdf")
    merged_toris = os.path.join(PACKAGE_FOLDER, "MERGED_TORIS_SEA_PAY_CERT_SHEETS.pdf")
    merged_summary = os.path.join(PACKAGE_FOLDER, "MERGED_SUMMARY.pdf")

    _merge_folder_pdfs(SEA_PAY_PG13_FOLDER, merged_pg13, "SEA PAY PG13")
    _merge_folder_pdfs(TORIS_CERT_FOLDER, merged_toris, "TORIS SEA PAY CERT SHEET")
    _merge_folder_pdfs(SUMMARY_PDF_FOLDER, merged_summary, "SUMMARY PDFs")

    log("PACKAGE MERGE COMPLETE")
