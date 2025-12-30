import os
import re
from PyPDF2 import PdfWriter, PdfReader
from app.core.logger import log
from app.core.config import (
    SEA_PAY_PG13_FOLDER,
    TORIS_CERT_FOLDER,
    SUMMARY_PDF_FOLDER,
    PACKAGE_FOLDER,
)

# 🔹 --- START OF PATCH --- 🔹

def _get_file_prefixes_from_folder(folder):
    """
    Scans a folder and extracts a sorted list of unique filename prefixes.
    This is the source of truth for identifying member files.
    Example: 'STG1_NIVERA_RYAN_N_SUMMARY.pdf' -> 'STG1_NIVERA_RYAN_N'
    """
    if not os.path.exists(folder):
        return []
    
    prefixes = set()
    files = [f for f in os.listdir(folder) if f.lower().endswith(".pdf")]
    
    for f in files:
        # Reliably get the prefix by removing the known suffix
        if f.endswith("_SUMMARY.pdf"):
            prefixes.add(f[:-12]) # Length of "_SUMMARY.pdf" is 12
            
    return sorted(list(prefixes))

def _create_bookmark_name(safe_prefix):
    """
    Converts a filename-safe prefix back into a human-readable bookmark name.
    Example: 'STG1_NIVERA_RYAN_N' -> 'STG1 NIVERA,RYAN N'
    """
    parts = safe_prefix.split('_')
    if len(parts) >= 3:
        rate = parts[0]
        last = parts[1]
        first = " ".join(parts[2:])
        return f"{rate} {last},{first}"
    return safe_prefix.replace("_", " ") # Fallback

def _append_pdf(writer, file_path, bookmark_title, parent_bookmark=None):
    """
    Helper to append a PDF to the writer and add an optional bookmark.
    Returns the number of pages added.
    """
    if not os.path.exists(file_path):
        log(f"  - INFO: File not found for bookmark '{bookmark_title}'. Looked for: {os.path.basename(file_path)}")
        return 0
        
    try:
        reader = PdfReader(file_path)
        num_pages_added = len(reader.pages)
        if num_pages_added == 0:
            log(f"  - ⚠️ WARNING: PDF file '{os.path.basename(file_path)}' is empty (0 pages). Skipping.")
            return 0

        page_num_before_add = len(writer.pages)
        
        writer.add_outline_item(bookmark_title, page_num_before_add, parent=parent_bookmark)
        log(f"  - Adding bookmark '{bookmark_title}' at page {page_num_before_add + 1}")

        for page in reader.pages:
            writer.add_page(page)
            
        log(f"    ... Appended {os.path.basename(file_path)} ({num_pages_added} pages)")
        return num_pages_added
    except Exception as e:
        log(f"  - ❗️ CRITICAL ERROR appending PDF {os.path.basename(file_path)}: {e}")
        return 0

def merge_all_pdfs():
    """
    Merges all output PDFs into a single, bookmarked package.
    Creates a nested table of contents for easy navigation.
    """
    os.makedirs(PACKAGE_FOLDER, exist_ok=True)
    
    final_package_path = os.path.join(PACKAGE_FOLDER, "MERGED_SEA_PAY_PACKAGE.pdf")
    writer = PdfWriter()
    
    log("=== BOOKMARKED PACKAGE MERGE STARTED ===")
    
    # 1. Get a master list of all file prefixes from the summary files.
    all_prefixes = _get_file_prefixes_from_folder(SUMMARY_PDF_FOLDER)
    if not all_prefixes:
        log("MERGE FAILED → No member summary PDFs found in SUMMARY_PDF folder. Cannot determine which members to process.")
        writer.close()
        return

    log(f"Found {len(all_prefixes)} unique member file prefixes: {all_prefixes}")
    
    # 2. Loop through each prefix to build a member's section
    for safe_key_prefix in all_prefixes:
        member_bookmark_name = _create_bookmark_name(safe_key_prefix)
        log(f"Processing prefix: '{safe_key_prefix}' for member: '{member_bookmark_name}'")
        
        parent_page_num = len(writer.pages)
        parent_bookmark = writer.add_outline_item(member_bookmark_name, parent_page_num)
        log(f"  - Creating parent bookmark '{member_bookmark_name}' at page {parent_page_num + 1}")
        
        # 3. Find and append this member's files using the exact prefix
        
        summary_file = os.path.join(SUMMARY_PDF_FOLDER, f"{safe_key_prefix}_SUMMARY.pdf")
        _append_pdf(writer, summary_file, "Summary", parent_bookmark)
        
        try:
            # Use startswith for a robust match
            toris_files = [f for f in os.listdir(TORIS_CERT_FOLDER) if f.startswith(safe_key_prefix)]
            if toris_files:
                toris_file_path = os.path.join(TORIS_CERT_FOLDER, toris_files[0])
                _append_pdf(writer, toris_file_path, "TORIS Certification", parent_bookmark)
            else:
                log(f"  - INFO: No TORIS Cert file found for prefix '{safe_key_prefix}'")
        except FileNotFoundError:
            log(f"  - WARNING: TORIS Cert folder not found at {TORIS_CERT_FOLDER}")

        try:
            # Use startswith for a robust match
            pg13_files = [f for f in os.listdir(SEA_PAY_PG13_FOLDER) if f.startswith(safe_key_prefix)]
            if pg13_files:
                pg13_parent_bookmark = writer.add_outline_item("PG-13s", len(writer.pages), parent=parent_bookmark)
                for pg13_file in sorted(pg13_files):
                    match = re.search(r'PG13_(.+)\.pdf', pg13_file, re.IGNORECASE)
                    ship_name = match.group(1).replace("_", " ") if match else pg13_file
                    bookmark_title = f"{ship_name}"
                    pg13_file_path = os.path.join(SEA_PAY_PG13_FOLDER, pg13_file)
                    _append_pdf(writer, pg13_file_path, bookmark_title, pg13_parent_bookmark)
            else:
                log(f"  - INFO: No PG-13 files found for prefix '{safe_key_prefix}'")
        except FileNotFoundError:
            log(f"  - WARNING: PG-13 folder not found at {SEA_PAY_PG13_FOLDER}")

    log(f"Finalizing PDF. Total pages to write: {len(writer.pages)}")

    if len(writer.pages) > 0:
        try:
            with open(final_package_path, "wb") as f:
                writer.write(f)
            log(f"✅ BOOKMARKED PACKAGE CREATED → {os.path.basename(final_package_path)}")
        except Exception as e:
            log(f"❗️CRITICAL ERROR writing final PDF: {e}")
    else:
        log("MERGE FAILED → No pages were added to the final package. Check file paths and prefixes in the log.")
        
    writer.close()
    log("PACKAGE MERGE COMPLETE")

# 🔹 --- END OF PATCH --- 🔹
