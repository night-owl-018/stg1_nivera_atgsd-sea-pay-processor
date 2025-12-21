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
    """
    Merge all PDFs in source_folder into out_path.

    - If folder does not exist → log and skip (no error)
    - If no PDFs found       → log and skip (no error)
    """
    if not os.path.exists(source_folder):
        log(f"{description} merge skipped → folder missing: {source_folder}")
        return

    files = [
        f
        for f in sorted(os.listdir(source_folder))
        if f.lower().endswith(".pdf")
    ]

    if not files:
        log(f"{description} merge skipped → no PDF files found")
        return

    merger = PdfMerger()
    try:
        for f in files:
            full_path = os.path.join(source_folder, f)
            merger.append(full_path)
            log(f"ADDED TO {description} → {f}")

        merger.write(out_path)
        log(f"MERGED PDF CREATED → {os.path.basename(out_path)}")
    finally:
        merger.close()


def merge_all_pdfs():
    """
    Build merged packages for:
        MERGED_SEA_PAY_PG13.pdf
        MERGED_TORIS_SEA_PAY_CERT_SHEETS.pdf
        MERGED_SUMMARY.pdf
    """
    # Always ensure package folder exists
    os.makedirs(PACKAGE_FOLDER, exist_ok=True)

    # Also ensure SUMMARY_PDF folder exists so we never crash on listdir
    os.makedirs(SUMMARY_PDF_FOLDER, exist_ok=True)

    merged_pg13 = os.path.join(PACKAGE_FOLDER, "MERGED_SEA_PAY_PG13.pdf")
    merged_toris = os.path.join(PACKAGE_FOLDER, "MERGED_TORIS_SEA_PAY_CERT_SHEETS.pdf")
    merged_summary = os.path.join(PACKAGE_FOLDER, "MERGED_SUMMARY.pdf")

    _merge_folder_pdfs(SEA_PAY_PG13_FOLDER, merged_pg13, "SEA PAY PG13")
    _merge_folder_pdfs(TORIS_CERT_FOLDER, merged_toris, "TORIS SEA PAY CERT SHEET")
    _merge_folder_pdfs(SUMMARY_PDF_FOLDER, merged_summary, "SUMMARY PDFs")

    log("PACKAGE MERGE COMPLETE")
