"""
PDF Merger with Bookmarks for Sea Pay Sheets
Merges PDFs and adds bookmarks using standardized names from CSV roster
"""

import os
import re
from difflib import SequenceMatcher
from PyPDF2 import PdfReader, PdfWriter

from app.core.logger import log
from app.core.config import (
    SEA_PAY_PG13_FOLDER,
    TORIS_CERT_FOLDER,
    SUMMARY_PDF_FOLDER,
    PACKAGE_FOLDER,
    ROSTER_CSV_PATH,  # Path to your roster CSV
)


def load_roster_names(csv_path):
    """
    Load names from CSV roster file.
    
    Expected CSV format:
      rate,last,first
      E5,HUDSON,GINGER
      E6,SMITH,JOHN
    
    Returns dict: {"GINGER HUDSON": "HUDSON, GINGER", ...}
    """
    import csv
    
    roster = {}
    
    try:
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                last = row.get('last', '').strip().upper()
                first = row.get('first', '').strip().upper()
                
                if last and first:
                    # Store both name formats for matching
                    # Key: "FIRST LAST" format (as found in PDFs)
                    # Value: "LAST, FIRST" format (standardized)
                    key = f"{first} {last}"
                    value = f"{last}, {first}"
                    roster[key] = value
                    
                    # Also store reverse format as key
                    roster[f"{last} {first}"] = value
                    roster[f"{last}, {first}"] = value
        
        log(f"Loaded {len(roster)} name variations from roster CSV")
        return roster
        
    except FileNotFoundError:
        log(f"⚠️  Roster CSV not found: {csv_path}")
        return {}
    except Exception as e:
        log(f"⚠️  Error loading roster CSV: {e}")
        return {}


def fuzzy_match_name(extracted_name, roster_names, threshold=0.75):
    """
    Find the best matching name from roster using fuzzy matching.
    
    Args:
        extracted_name: Name extracted from PDF (e.g., "GINGER HUDSON")
        roster_names: Dict of roster names
        threshold: Minimum similarity score (0-1)
    
    Returns:
        Standardized name from roster or original if no good match
    """
    if not roster_names:
        return extracted_name
    
    # Clean the extracted name
    clean_name = re.sub(r'\s+', ' ', extracted_name.strip().upper())
    
    # Check for exact match first
    if clean_name in roster_names:
        return roster_names[clean_name]
    
    # Fuzzy matching
    best_match = None
    best_score = 0
    
    for roster_key, roster_value in roster_names.items():
        # Calculate similarity
        score = SequenceMatcher(None, clean_name, roster_key).ratio()
        
        if score > best_score and score >= threshold:
            best_score = score
            best_match = roster_value
    
    if best_match:
        log(f"MATCHED '{extracted_name}' → '{best_match}' (confidence: {best_score:.2%})")
        return best_match
    else:
        log(f"⚠️  No roster match for '{extracted_name}' (using as-is)")
        return extracted_name


def extract_name_from_pdf(pdf_path):
    """
    Extract the owner's name from the PDF content.
    
    Looks for patterns like:
      - "Name: GINGER HUDSON"
      - "Name:GINGER HUDSON"
      - All-caps name near the top of the page
    
    Falls back to filename if not found in PDF.
    """
    try:
        reader = PdfReader(pdf_path)
        
        if len(reader.pages) > 0:
            text = reader.pages[0].extract_text() or ""
            
            # Pattern 1: "Name: FIRSTNAME LASTNAME"
            m = re.search(r'Name:\s*([A-Z][A-Z\s]+?)(?:\s+SSN|\s+DOD|$)', text, re.MULTILINE)
            if m:
                name = m.group(1).strip()
                # Clean up extra spaces
                name = re.sub(r'\s+', ' ', name)
                if name and 3 < len(name) < 50:
                    return name
            
            # Pattern 2: Look for all-caps names in first 15 lines
            lines = text.split('\n')
            for line in lines[:15]:
                line = line.strip()
                # Match patterns like "HUDSON, GINGER" or "GINGER HUDSON"
                if re.match(r'^[A-Z]+(?:,\s*)?(?:\s+[A-Z]+)+$', line):
                    if 5 < len(line) < 50 and line.count(' ') <= 3:
                        return line
    
    except Exception as e:
        log(f"⚠️  Could not extract name from PDF: {e}")
    
    # Fallback: extract from filename
    return extract_name_from_filename(pdf_path)


def extract_name_from_filename(filename):
    """
    Extract name from filename as fallback.
    
    Examples:
      - "HUDSON Sea Pay 8_4_2025.pdf" → "HUDSON"
      - "Smith_John_Sea_Pay.pdf" → "SMITH JOHN"
    """
    basename = os.path.basename(filename)
    name_part = basename.replace('.pdf', '').replace('.PDF', '')
    
    # Pattern 1: "LASTNAME Sea Pay ..."
    m = re.match(r'^([A-Z]+)\s+Sea\s+Pay', name_part, re.IGNORECASE)
    if m:
        return m.group(1).upper()
    
    # Pattern 2: "Last_First_..." → "LAST FIRST"
    if '_' in name_part:
        parts = name_part.split('_')
        keywords = ['sea', 'pay', 'sheet', 'toris', 'pg13', 'summary']
        name_parts = []
        for part in parts:
            if part.lower() in keywords or re.match(r'\d', part):
                break
            name_parts.append(part.upper())
        if name_parts:
            return ' '.join(name_parts[:2])
    
    # Fallback: first 30 chars
    return name_part[:30].upper()


def _merge_folder_pdfs_with_bookmarks(source_folder, out_path, description, roster_names):
    """
    Merge all PDFs in source_folder into out_path WITH BOOKMARKS.
    
    Each PDF gets a bookmark with the person's standardized name from roster CSV.
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

    writer = PdfWriter()
    page_count = 0
    
    try:
        for f in files:
            full_path = os.path.join(source_folder, f)
            
            try:
                reader = PdfReader(full_path)
                num_pages = len(reader.pages)
                
                if num_pages == 0:
                    log(f"⚠️  Skipping {f} (0 pages)")
                    continue
                
                # Extract the person's name from PDF content
                extracted_name = extract_name_from_pdf(full_path)
                
                # Match against roster CSV and get standardized name
                standardized_name = fuzzy_match_name(extracted_name, roster_names)
                
                # Add bookmark at the first page of this person's document
                writer.add_bookmark(standardized_name, page_count)
                
                # Append all pages from this PDF
                for page in reader.pages:
                    writer.add_page(page)
                
                page_count += num_pages
                
                log(f"ADDED TO {description} → {f} (Bookmark: {standardized_name})")
                
            except Exception as e:
                log(f"⚠️  Error adding {f}: {e}")
                continue
        
        if page_count > 0:
            with open(out_path, "wb") as output_file:
                writer.write(output_file)
            log(f"MERGED PDF CREATED → {os.path.basename(out_path)} ({page_count} pages)")
        else:
            log(f"⚠️  No pages to merge for {description}")
            
    except Exception as e:
        log(f"⚠️  Merge failed for {description}: {e}")


def merge_all_pdfs():
    """
    Build merged packages with bookmarks for:
        MERGED_SEA_PAY_PG13.pdf
        MERGED_TORIS_SEA_PAY_CERT_SHEETS.pdf
        MERGED_SUMMARY.pdf
    
    Each person's sheet gets a bookmark with their standardized name from roster CSV.
    Names are matched using fuzzy matching for flexibility.
    """
    # Always ensure package folder exists
    os.makedirs(PACKAGE_FOLDER, exist_ok=True)

    # Also ensure SUMMARY_PDF folder exists
    os.makedirs(SUMMARY_PDF_FOLDER, exist_ok=True)

    # Load roster names from CSV
    roster_names = load_roster_names(ROSTER_CSV_PATH)

    merged_pg13 = os.path.join(PACKAGE_FOLDER, "MERGED_SEA_PAY_PG13.pdf")
    merged_toris = os.path.join(PACKAGE_FOLDER, "MERGED_TORIS_SEA_PAY_CERT_SHEETS.pdf")
    merged_summary = os.path.join(PACKAGE_FOLDER, "MERGED_SUMMARY.pdf")

    _merge_folder_pdfs_with_bookmarks(SEA_PAY_PG13_FOLDER, merged_pg13, "SEA PAY PG13", roster_names)
    _merge_folder_pdfs_with_bookmarks(TORIS_CERT_FOLDER, merged_toris, "TORIS SEA PAY CERT SHEET", roster_names)
    _merge_folder_pdfs_with_bookmarks(SUMMARY_PDF_FOLDER, merged_summary, "SUMMARY PDFs", roster_names)

    log("PACKAGE MERGE COMPLETE")
