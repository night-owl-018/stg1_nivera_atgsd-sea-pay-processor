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

def _build_date_variants(date_str):
    """
    Build a small set of date variants to match the same calendar day
    written in different formats by the OCR.

    For example, '08/04/2025' may also appear as:
      - '8/4/2025'
      - '8/4/25'
      - '08/04/25'
    """
    variants = set()
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
    strike_color="black",
    override_valid_rows=None,  # PATCH
):

    """
    Draws strikeout lines on the TORIS Sea Pay sheet for invalid/duplicate rows
    and (optionally) corrects the 'Total Sea Pay Days' number.

    Args:
        original_pdf: Path to original TORIS sheet.
        skipped_duplicates: list of dicts with 'date' and 'occ_idx' for dupes.
        skipped_unknown: list of dicts with 'date' and 'occ_idx' for invalid rows.
        output_path: Where to write the marked PDF.
        extracted_total_days: The number parsed from the TORIS text (may be None).
        computed_total_days: The total valid sea pay days we computed from logic.
        strike_color: 'black' or 'red' for strike lines.
    """

    # ------------------------------------------------
    # COLOR MAP
    # ------------------------------------------------
    color_map = {
        "black": (0, 0, 0),
        "red": (1, 0, 0),
    }
    rgb = color_map.get(strike_color.lower(), (0, 0, 0))

    try:
        log(f"MARKING SHEET START → {os.path.basename(original_pdf)}")

        # Build sets of (date, occ_idx) to identify which rows are invalid/duplicate
        targets_invalid = {(u["date"], u["occ_idx"]) for u in skipped_unknown}
        targets_dup = {(d["date"], d["occ_idx"]) for d in skipped_duplicates}
        all_targets = targets_invalid.union(targets_dup)

        # PATCH — build override-valid lookup (DATE-ONLY)
        override_valid_dates = set()
        if override_valid_rows:
            for r in override_valid_rows:
                if r.get("date"):
                    override_valid_dates.add(r["date"])


        
        # Convert all pages to images for positional OCR
        pages = convert_from_path(original_pdf)
        row_list = []

        # ------------------------------------------------
        # BUILD ROWS & OCR tokens
        # ------------------------------------------------
        # Collect all unique dates we need to match
        all_dates = {d for (d, _) in all_targets}
        date_variants_map = {d: _build_date_variants(d) for d in all_dates}

        # ocr_tokens[page_index] = list of (text, left, top, w, h)
        ocr_tokens = {}

        for page_index, img in enumerate(pages):
            log(f"  BUILDING ROWS FROM PAGE {page_index + 1}/{len(pages)}")

            data = pytesseract.image_to_data(img, output_type=Output.DICT)
            img_w, img_h = img.size
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

                center_y_img = top + height / 2.0
                center_from_bottom_px = img_h - center_y_img
                y = center_from_bottom_px * scale_y

                tokens.append({"text": txt.upper(), "y": y})

            ocr_tokens[page_index] = page_token_list

            # Sort descending by Y (from top of PDF downwards)
            tokens.sort(key=lambda t: -t["y"])

            # Cluster tokens into visual rows
            visual_rows = []
            current_row = []
            last_y = None
            threshold = 5.5  # Vertical tolerance for grouping

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

            # Build row objects with average Y and concatenated text
            tmp_rows = []
            for row in visual_rows:
                y_avg = sum(t["y"] for t in row) / len(row)
                text = " ".join(t["text"] for t in row)
                tmp_rows.append({
                    "page": page_index,
                    "y": y_avg,
                    "text": text,
                    "date": None,
                    "occ_idx": None,
                })

            # Sort rows from top to bottom
            tmp_rows.sort(key=lambda r: -r["y"])

            # Assign date + occurrence index to rows
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
        # HELPER: FIND NEAREST DATE ROW ON A PAGE
        # ------------------------------------------------
        def _find_nearest_date_row(page_idx, y_target):
            """Return the row on this page that has a date and is closest in Y."""
            best = None
            best_delta = None
            for r in row_list:
                if r["page"] != page_idx:
                    continue
                if not r.get("date"):
                    continue
                delta = abs(r["y"] - y_target)
                if best is None or delta < best_delta:
                    best = r
                    best_delta = delta
            return best

        # ------------------------------------------------
        # STRIKEOUT TARGETS (DATE-BASED, ONE PER DATE/PAGE)
        # ------------------------------------------------
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
            date = row.get("date")
            occ_idx = row.get("occ_idx")
            if not date or not occ_idx:
                continue

            if (date, occ_idx) in targets_invalid:
                _register_strike(row["page"], date, row["y"])
                log(
                    f"    STRIKEOUT INVALID DATE {date} OCC#{occ_idx} "
                    f"PAGE {row['page'] + 1} Y={row['y']:.1f}"
                )

        for row in row_list:
            date = row.get("date")
            occ_idx = row.get("occ_idx")
            if not date or not occ_idx:
                continue

            if (date, occ_idx) in targets_dup:
                _register_strike(row["page"], date, row["y"])
                log(
                    f"    STRIKEOUT DUP DATE {date} OCC#{occ_idx} "
                    f"PAGE {row['page'] + 1} Y={row['y']:.1f}"
                )

        # ------------------------------------------------
        # AUTO-STRIKE INVALID TEXT MARKERS
        # ------------------------------------------------
        INVALID_MARKERS = [
            "SBTT",
            "MITE",
            "ASTAC MITE",
            "ASW MITE",
        ]

        for row in row_list:
            text = row["text"]
            if any(marker in text for marker in INVALID_MARKERS):
                # OVERRIDE-AWARE AUTO-STRIKE (FINAL FIX)
                if override_valid_rows:
                    # If ANY overridden-valid row exists for this page, skip auto-strike
                    if row.get("date"):
                        if row["date"] in override_valid_dates:
                            log(f"SKIP AUTO-STRIKE (OVERRIDDEN DATE) → {row['date']}")
                            continue
                    else:
                        # Date not detected, but overrides exist — safest action is NO STRIKE
                        log("SKIP AUTO-STRIKE (OVERRIDE PRESENT, DATE UNKNOWN)")
                        continue
                      
                if row.get("date"):
                    target_date = row["date"]
                    target_y = row["y"]
                else:
                    nearest = _find_nearest_date_row(row["page"], row["y"])
                    if nearest and nearest.get("date"):
                        target_date = nearest["date"]
                        target_y = nearest["y"]
                    else:
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

            for (txt, left, top, w, h) in tokens_page:
                if re.fullmatch(r"\d+", txt):
                    center_y_img = top + h / 2.0
                    center_from_bottom_px = height_img - center_y_img
                    y_pdf = center_from_bottom_px * (letter[1] / float(height_img))

                    if abs(y_pdf - target_y_pdf) < 3:
                        old_start_x_pdf = left * scale_x
                        old_end_x_pdf = (left + w) * scale_x
                        break

            if old_start_x_pdf is None:
                old_start_x_pdf = 260
                old_end_x_pdf = 300

            buf = io.BytesIO()
            c = canvas.Canvas(buf, pagesize=letter)
            c.setFont("Helvetica", 10)

            three_spaces_width = c.stringWidth("   ", "Helvetica", 10)
            correct_x_pdf = old_end_x_pdf + three_spaces_width
            strike_end_x = correct_x_pdf - three_spaces_width

            c.setLineWidth(0.8)
            c.setStrokeColorRGB(*rgb)

            # ---- CLEAN + COMPARE TOTALS ----
            clean_extracted = re.sub(
                r"\D",
                "",
                str(extracted_total_days or "")
            ).strip()

            computed_str = str(computed_total_days)

            # ------------------------------------------------
            # PATCHED SECTION — SAFE READ OF EXISTING PDF
            # ------------------------------------------------
            if not clean_extracted:
                try:
                    safe_pdf_path = output_path if os.path.exists(output_path) else original_pdf

                    pdf_reader = PdfReader(safe_pdf_path)
                    page_text = pdf_reader.pages[page_idx].extract_text() or ""

                    m = re.search(
                        r"Total\s+Sea\s+Pay\s+Days.*?(\d+)",
                        page_text,
                        re.IGNORECASE | re.DOTALL,
                    )
                    if m:
                        clean_extracted = m.group(1).strip()
                        log(f"PDF TEXT FALLBACK EXTRACTED TOTAL → {clean_extracted}")

                except Exception as e:
                    log(f"PDF TEXT FALLBACK ERROR → {e}")
            # ------------------------------------------------

            if clean_extracted and clean_extracted == computed_str:
                log(
                    f"TOTAL DAYS MATCH → extracted={clean_extracted} "
                    f"computed={computed_str} (NO STRIKE)"
                )
            else:
                log(
                    f"TOTAL DAYS MISMATCH/UNKNOWN → extracted={clean_extracted or 'None'} "
                    f"computed={computed_str} (STRIKE + CORRECT)"
                )
                c.line(old_start_x_pdf, target_y_pdf, strike_end_x, target_y_pdf)
                c.drawString(correct_x_pdf, target_y_pdf, computed_str)

            c.save()
            buf.seek(0)
            total_overlay = PdfReader(buf)

        # ------------------------------------------------
        # NORMAL STRIKEOUT LINES
        # ------------------------------------------------
        overlays = []
        for p in range(len(pages)):
            date_to_y = strike_targets_by_page.get(p)
            if not date_to_y:
                overlays.append(None)
                continue

            buf = io.BytesIO()
            c = canvas.Canvas(buf, pagesize=letter)
            c.setLineWidth(0.8)
            c.setStrokeColorRGB(*rgb)

            for date_str, y in date_to_y.items():
                c.line(40, y, 550, y)

            c.save()
            buf.seek(0)
            overlays.append(PdfReader(buf))

        # ------------------------------------------------
        # APPLY OVERLAYS
        # ------------------------------------------------
        reader = PdfReader(original_pdf)
        writer = PdfWriter()

        for i, page in enumerate(reader.pages):
            if total_overlay and total_row and i == total_row["page"]:
                page.merge_page(total_overlay.pages[0])

            if i < len(overlays) and overlays[i] is not None:
                page.merge_page(overlays[i].pages[0])

            try:
                page.compress_content_streams()
            except Exception:
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


