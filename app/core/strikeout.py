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
    variants = set()
    try:
        dt = datetime.strptime(date_str, "%m/%d/%Y")
    except Exception:
        return {date_str}

    variants.add(date_str)
    variants.add(f"{dt.month}/{dt.day}/{dt.year}")
    variants.add(f"{dt.month}/{dt.day}/{dt.year % 100:02d}")  # fixed
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
):

    # COLOR MAP
    color_map = {
        "black": (0, 0, 0),
        "red": (1, 0, 0),
    }
    rgb = color_map.get(strike_color.lower(), (0, 0, 0))

    try:
        log(f"MARKING SHEET START → {os.path.basename(original_pdf)}")

        # Targets based on parser decisions
        targets_invalid = {(u["date"], u["occ_idx"]) for u in skipped_unknown}
        targets_dup = {(d["date"], d["occ_idx"]) for d in skipped_duplicates}
        all_targets = targets_invalid.union(targets_dup)

        pages = convert_from_path(original_pdf)
        row_list = []

        # ------------------------------------------------
        # BUILD ROWS & OCR TOKENS
        # ------------------------------------------------
        all_dates = {d for (d, _) in all_targets}
        date_variants_map = {d: _build_date_variants(d) for d in all_dates}

        ocr_tokens = {}

        for page_index, img in enumerate(pages):
            log(f"  BUILDING ROWS FROM PAGE {page_index+1}/{len(pages)}")

            data = pytesseract.image_to_data(img, output_type=Output.DICT)
            img_w, img_h = img.size
            scale_y = letter[1] / float(img_h)

            tokens = []
            page_token_list = []

            for j in range(len(data["text"])):
                txt = data["text"][j].strip()
                if not txt:
                # skip empty tokens
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

            # Sort by Y (top = larger Y in PDF coords here)
            tokens.sort(key=lambda t: -t["y"])

            visual_rows = []
            current_row = []
            last_y = None
            threshold = 5.5  # row merge tolerance

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

            tmp_rows = []
            for row in visual_rows:
                y_avg = sum(t["y"] for t in row) / len(row)

                # A-1 INTERNAL NORMALIZATION ONLY:
                #  - join tokens
                #  - remove 'þ'
                #  - join time pairs 0800 1600 → 0800-1600 (T-1 rule)
                raw_text = " ".join(t["text"] for t in row)

                # remove checkbox symbol
                text = raw_text.replace("Þ", "").replace("þ", "")

                # collapse multiple spaces
                text = re.sub(r"\s+", " ", text).strip()

                # join time blocks: T-1 (always join two 3–4 digit groups)
                text = re.sub(r"\b(\d{3,4})\s+(\d{3,4})\b", r"\1-\2", text)

                tmp_rows.append({
                    "page": page_index,
                    "y": y_avg,
                    "text": text,
                    "date": None,
                    "occ_idx": None,
                })

            # sort rows top to bottom by y (PDF coords)
            tmp_rows.sort(key=lambda r: -r["y"])

            # assign date + occ_idx per date using variants
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
        # STRIKEOUT TARGETS (ONE PER PAGE+DATE)
        # ------------------------------------------------
        strike_targets = {}

        # Anchor Y per (page, date) based on the first row that contains that date
        date_anchor_y = {}
        for row in row_list:
            if row.get("date"):
                key = (row["page"], row["date"])
                if key not in date_anchor_y:
                    date_anchor_y[key] = row["y"]

        def add_strike(page_idx, date_val, fallback_y):
            """
            Always strike at the DATE row Y when possible.
            Only one strike per (page, date) — no doubles.
            """
            if date_val is not None:
                key = (page_idx, date_val)
                y = date_anchor_y.get(key, fallback_y)
            else:
                y = fallback_y

            ys = strike_targets.setdefault(page_idx, [])
            if not any(abs(existing_y - y) < 0.1 for existing_y in ys):
                ys.append(y)

        # INVALID / UNKNOWN
        for row in row_list:
            if row["date"] and row["occ_idx"] and (row["date"], row["occ_idx"]) in targets_invalid:
                add_strike(row["page"], row["date"], row["y"])
                log(f"    STRIKEOUT INVALID {row['date']} OCC#{row['occ_idx']} PAGE {row['page']+1}")

        # DUPLICATES
        for row in row_list:
            if row["date"] and row["occ_idx"] and (row["date"], row["occ_idx"]) in targets_dup:
                add_strike(row["page"], row["date"], row["y"])
                log(f"    STRIKEOUT DUP {row['date']} OCC#{row['occ_idx']} PAGE {row['page']+1}")

        # SBTT — ALWAYS STRIKE, ANCHORED TO DATE IF AVAILABLE
        for row in row_list:
            if "SBTT" in row["text"]:
                add_strike(row["page"], row.get("date"), row["y"])
                log(f"    STRIKEOUT SBTT EVENT PAGE {row['page']+1} TEXT={row['text']}")

        # ------------------------------------------------
        # FIND TOTAL ROW
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

            # find existing total number on that row to anchor X region
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

            # ------------------------------------------------
            # CLEAN OCR EXTRACTED VALUE (STRONG VERSION)
            # ------------------------------------------------
            clean_extracted = "".join(re.findall(r"\d+", str(extracted_total_days)))
            computed_str = str(computed_total_days)

            # PDF TEXT FALLBACK IF NEEDED
            if not clean_extracted:
                try:
                    page_text = PdfReader(original_pdf).pages[page_idx].extract_text() or ""
                    m = re.search(r"Total Sea Pay Days.*?(\d+)", page_text)
                    if m:
                        clean_extracted = m.group(1).strip()
                        log(f"PDF TEXT FALLBACK EXTRACTED → {clean_extracted}")
                except Exception as e:
                    log(f"PDF TEXT FALLBACK ERROR → {e}")

            # ONLY STRIKE AND REWRITE IF DIFFERENT
            if clean_extracted != computed_str:
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
            ys = strike_targets.get(p)
            if not ys:
                overlays.append(None)
                continue

            buf = io.BytesIO()
            c = canvas.Canvas(buf, pagesize=letter)
            c.setLineWidth(0.8)
            c.setStrokeColorRGB(*rgb)

            for y in ys:
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

        log(f"MARKED SHEET CREATED → {os.path.basename(original_pdf)}")

    except Exception as e:
        log(f"⚠️ MARKING FAILED → {e}")
        try:
            shutil.copy2(original_pdf, output_path)
            log(f"FALLBACK COPY CREATED → {os.path.basename(original_pdf)}")
        except Exception as e2:
            log(f"⚠️ FALLBACK COPY FAILED → {e2}")
