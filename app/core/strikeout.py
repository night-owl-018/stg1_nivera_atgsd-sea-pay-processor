import os
import shutil
from datetime import datetime
import io
import re

import pytesseract
from pdf2image import convert_from_path
from PyPDF2 import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from pytesseract import Output

from app.core.logger import log


# ------------------------------------------------
# DATE VARIANT BUILDER
# ------------------------------------------------

def _build_date_variants(date_str: str):
    """
    Build common OCR variants for a given MM/DD/YYYY date string.
    This helps match slightly different renderings of the same date.
    """
    variants = set()
    if not date_str:
        return variants

    try:
        dt = datetime.strptime(date_str, "%m/%d/%Y")
    except Exception:
        # If parsing fails, just return the raw string
        return {date_str}

    # Original as given
    variants.add(date_str)

    # Non-padded month/day, 4-digit year
    variants.add(f"{dt.month}/{dt.day}/{dt.year}")

    # Non-padded month/day, 2-digit year
    variants.add(f"{dt.month}/{dt.day}/{dt.year % 100:02d}")

    # Zero-padded month/day, 2-digit year
    variants.add(f"{dt.month:02d}/{dt.day:02d}/{dt.year % 100:02d}")

    return variants


# ------------------------------------------------
# STRIKEOUT ENGINE
# ------------------------------------------------

def mark_sheet_with_strikeouts(
    original_pdf,
    skipped_duplicates,
    skipped_unknown,
    output_path,
    extracted_total_days,
    computed_total_days,
    strike_color: str = "black",
):
    """
    Draw strikeout lines on the TORIS Sea Duty Certification Sheet.

    Behaviour:
      • Strike rows for skipped_unknown and skipped_duplicates
      • Auto-strike invalid events (SBTT / MITE / ASTAC MITE / ASW MITE)
      • Strike at the DATE row Y (single full-width line per date per page)
      • Adjust 'Total Sea Pay Days for this reporting period' only if the
        extracted total does NOT match the computed_total_days.

    This function does NOT change any PG13 formatting. It only produces a
    marked-up copy of the original TORIS sheet.
    """

    # ------------------------------------------------
    # COLOR MAP
    # ------------------------------------------------
    color_map = {
        "black": (0.0, 0.0, 0.0),
        "red": (1.0, 0.0, 0.0),
    }
    rgb = color_map.get(strike_color.lower(), (0.0, 0.0, 0.0))

    try:
        log(f"MARKING SHEET START → {os.path.basename(original_pdf)}")

        # Build quick lookup sets for invalid / duplicate row keys
        targets_invalid = {(u["date"], u["occ_idx"]) for u in skipped_unknown}
        targets_dup = {(d["date"], d["occ_idx"]) for d in skipped_duplicates}
        all_targets = targets_invalid.union(targets_dup)

        # Load pages as images for coordinate work
        pages = convert_from_path(original_pdf)
        row_list = []          # logical OCR "rows"
        ocr_tokens = {}        # per-page tokens for later use (totals)

        # ------------------------------------------------
        # BUILD ROWS & OCR TOKENS
        # ------------------------------------------------
        # Collect all dates we care about from the target lists
        all_dates = {d for (d, _) in all_targets if d}
        date_variants_map = {d: _build_date_variants(d) for d in all_dates}

        for page_index, img in enumerate(pages):
            log(f"  BUILDING ROWS FROM PAGE {page_index + 1}/{len(pages)}")

            data = pytesseract.image_to_data(img, output_type=Output.DICT)
            img_w, img_h = img.size
            # Map image Y → PDF Y
            scale_y = letter[1] / float(img_h)

            tokens = []
            page_token_list = []

            for j in range(len(data["text"])):
                txt = data["text"][j].strip()
                if not txt:
                    continue

                left = data["left"][j]
                top = data["top"][j]
                width = data["width"][j]
                height = data["height"][j]

                page_token_list.append((txt, left, top, width, height))

                # Center of token in image coords
                center_y_img = top + height / 2.0
                center_from_bottom_px = img_h - center_y_img
                y_pdf = center_from_bottom_px * scale_y

                tokens.append({"text": txt.upper(), "y": y_pdf})

            ocr_tokens[page_index] = page_token_list

            # Sort tokens top→bottom in PDF coordinates (higher y = lower on page)
            tokens.sort(key=lambda t: -t["y"])

            # Group into "visual rows" by Y closeness
            visual_rows = []
            current_row = []
            last_y = None
            threshold = 5.5  # Y-distance tolerance for same row

            for tok in tokens:
                if last_y is None:
                    current_row = [tok]
                    last_y = tok["y"]
                    continue

                if abs(tok["y"] - last_y) <= threshold:
                    current_row.append(tok)
                    last_y = tok["y"]
                else:
                    visual_rows.append(current_row)
                    current_row = [tok]
                    last_y = tok["y"]

            if current_row:
                visual_rows.append(current_row)

            # Build row objects
            tmp_rows = []
            for row in visual_rows:
                y_avg = sum(t["y"] for t in row) / len(row)
                text = " ".join(t["text"] for t in row)
                tmp_rows.append(
                    {
                        "page": page_index,
                        "y": y_avg,
                        "text": text,
                        "date": None,
                        "occ_idx": None,
                    }
                )

            # Sort rows again top→bottom (highest y first)
            tmp_rows.sort(key=lambda r: -r["y"])

            # Assign date / occ_idx based on known dates
            date_counters = {d: 0 for d in all_dates}
            for row in tmp_rows:
                for d in all_dates:
                    variants = date_variants_map[d]
                    if any(v in row["text"] for v in variants):
                        date_counters[d] += 1
                        row["date"] = d
                        row["occ_idx"] = date_counters[d]
                        break

            row_list.extend(tmp_rows)

        # ------------------------------------------------
        # STRIKE TARGET COLLECTION
        # ------------------------------------------------
        # We want:
        #   • One strike per DATE per PAGE
        #   • At the DATE row Y position
        strike_targets_by_page = {}   # page_index -> {date: y}
        already_struck_date = set()   # global set of (page, date) to avoid duplicates

        def _register_strike(page_idx: int, date_str: str, y_val: float):
            """Internal helper to register a strike at (page, date)."""
            key = (page_idx, date_str)
            if key in already_struck_date:
                return
            already_struck_date.add(key)
            strike_targets_by_page.setdefault(page_idx, {})[date_str] = y_val

        # 1) Strike rows from skipped_unknown / skipped_duplicates
        for row in row_list:
            if not row.get("date") or not row.get("occ_idx"):
                continue
            key = (row["date"], row["occ_idx"])
            if key in all_targets:
                _register_strike(row["page"], row["date"], row["y"])
                log(
                    f"    STRIKEOUT TARGET {row['date']} OCC#{row['occ_idx']} "
                    f"PAGE {row['page'] + 1} Y={row['y']:.1f}"
                )

        # 2) Auto-strike invalid MITE / SBTT-type events at DATE-Y
        INVALID_MARKERS = ("SBTT", "MITE", "ASTAC MITE", "ASW MITE")

        # Build quick map of date rows per page to help align SBTT/MITE rows
        date_rows_by_page = {}
        for row in row_list:
            if row.get("date"):
                date_rows_by_page.setdefault(row["page"], []).append(row)

        for page_idx, date_rows in date_rows_by_page.items():
            # sort date rows by Y for stable nearest-row search
            date_rows.sort(key=lambda r: -r["y"])
            date_rows_by_page[page_idx] = date_rows

        def _find_nearest_date_row(page_idx: int, y_val: float):
            """Find the nearest row on the same page which has a date."""
            rows = date_rows_by_page.get(page_idx, [])
            if not rows:
                return None
            best = None
            best_dy = None
            for r in rows:
                dy = abs(r["y"] - y_val)
                if best_dy is None or dy < best_dy:
                    best = r
                    best_dy = dy
            return best

        for row in row_list:
            text = row["text"]
            if any(marker in text for marker in INVALID_MARKERS):
                # If row already has a date, use that
                if row.get("date"):
                    target_date = row["date"]
                    target_y = row["y"]
                else:
                    # Otherwise snap to nearest date row on this page
                    nearest = _find_nearest_date_row(row["page"], row["y"])
                    if nearest and nearest.get("date"):
                        target_date = nearest["date"]
                        target_y = nearest["y"]
                    else:
                        # As a last resort, strike exactly at this row's Y
                        target_date = f"SBTT_MITE_ROW_{row['page']}_{row['y']:.1f}"
                        target_y = row["y"]

                _register_strike(row["page"], target_date, target_y)
                log(
                    f"    STRIKEOUT INVALID TEXT '{text[:40]}' "
                    f"PAGE {row['page'] + 1} Y={target_y:.1f}"
                )

        # ------------------------------------------------
        # TOTAL SEA PAY DAYS PATCH
        # ------------------------------------------------
        total_row = None
        for row in row_list:
            if (
                "TOTAL" in row["text"]
                and "SEA" in row["text"]
                and "PAY" in row["text"]
                and "DAYS" in row["text"]
            ):
                total_row = row
                break

        total_overlay = None

        if total_row:
            page_idx = total_row["page"]
            target_y_pdf = total_row["y"]

            page_img = pages[page_idx]
            width_img, height_img = page_img.size
            scale_x = letter[0] / float(width_img)

            tokens_page = ocr_tokens[page_idx]

            old_start_x_pdf = None
            old_end_x_pdf = None

            # Try to locate the existing numeric total on the same Y row
            for (txt, left, top, w, h) in tokens_page:
                token_txt = txt.strip()
                if not re.fullmatch(r"\d+", token_txt):
                    continue

                center_y_img = top + h / 2.0
                center_from_bottom_px = height_img - center_y_img
                y_pdf = center_from_bottom_px * (letter[1] / float(height_img))

                if abs(y_pdf - target_y_pdf) < 3.0:
                    old_start_x_pdf = left * scale_x
                    old_end_x_pdf = (left + w) * scale_x
                    break

            # Fallback coordinates if we cannot detect the number
            if old_start_x_pdf is None:
                old_start_x_pdf = 260
                old_end_x_pdf = 300

            # Prepare overlay canvas
            buf = io.BytesIO()
            c = canvas.Canvas(buf, pagesize=letter)
            c.setFont("Helvetica", 10)

            three_spaces_width = c.stringWidth("   ", "Helvetica", 10)
            correct_x_pdf = old_end_x_pdf + three_spaces_width
            strike_end_x = correct_x_pdf - three_spaces_width

            c.setLineWidth(0.8)
            c.setStrokeColorRGB(*rgb)

            # ---- CLEAN + COMPARE TOTALS ----
            # 1) Clean extracted_total_days (OCR or parser supplied)
            clean_extracted = re.sub(r"\D", "", str(extracted_total_days or "")).strip()
            computed_str = str(computed_total_days)

            # 2) If we still don't have an extracted value, fallback to PDF text
            if not clean_extracted:
                try:
                    pdf_reader = PdfReader(original_pdf)
                    page_text = pdf_reader.pages[page_idx].extract_text() or ""
                    m = re.search(r"Total Sea Pay Days.*?(\d+)", page_text)
                    if m:
                        clean_extracted = m.group(1).strip()
                        log(f"PDF TEXT FALLBACK EXTRACTED TOTAL → {clean_extracted}")
                except Exception as e:
                    log(f"PDF TEXT FALLBACK ERROR → {e}")

            # 3) Only strike/override when numbers DIFFER
            if clean_extracted and clean_extracted == computed_str:
                # Numbers match → do nothing
                log(
                    f"TOTAL DAYS MATCH → extracted={clean_extracted} "
                    f"computed={computed_str} (NO STRIKE)"
                )
            else:
                # Mismatch or no extracted value → strike old, draw new
                c.line(old_start_x_pdf, target_y_pdf, strike_end_x, target_y_pdf)
                c.drawString(correct_x_pdf, target_y_pdf, computed_str)
                log(
                    f"TOTAL DAYS MISMATCH → extracted={clean_extracted or 'N/A'} "
                    f"computed={computed_str} (STRIKE + OVERRIDE)"
                )

            c.save()
            buf.seek(0)
            total_overlay = PdfReader(buf)

        # ------------------------------------------------
        # BUILD PER-PAGE STRIKEOVERLAYS
        # ------------------------------------------------
        overlays = []
        num_pages = len(pages)
        for p in range(num_pages):
            # Get all Y's for this page (unique per date)
            date_to_y = strike_targets_by_page.get(p)
            if not date_to_y:
                overlays.append(None)
                continue

            buf = io.BytesIO()
            c = canvas.Canvas(buf, pagesize=letter)
            c.setLineWidth(0.8)
            c.setStrokeColorRGB(*rgb)

            for y in date_to_y.values():
                # Full-width line similar to your original implementation
                c.line(40, y, 550, y)

            c.save()
            buf.seek(0)
            overlays.append(PdfReader(buf))

        # ------------------------------------------------
        # APPLY OVERLAYS TO ORIGINAL PDF
        # ------------------------------------------------
        reader = PdfReader(original_pdf)
        writer = PdfWriter()

        for i, page in enumerate(reader.pages):
            # Apply total-days overlay on the page where the total row lives
            if total_overlay and total_row and i == total_row["page"]:
                page.merge_page(total_overlay.pages[0])

            # Apply normal strikeout overlays
            if i < len(overlays) and overlays[i] is not None:
                page.merge_page(overlays[i].pages[0])

            try:
                page.compress_content_streams()
            except Exception:
                # Some PDFs cannot be compressed safely; ignore
                pass

            writer.add_page(page)

        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "wb") as f:
            writer.write(f)

        log(f"MARKED SHEET CREATED → {os.path.basename(output_path)}")

    except Exception as e:
        log(f"⚠️ MARKING FAILED → {e}")
        try:
            shutil.copy2(original_pdf, output_path)
            log(f"FALLBACK COPY CREATED → {os.path.basename(original_pdf)}")
        except Exception as e2:
            log(f"⚠️ FALLBACK COPY FAILED → {e2}")
