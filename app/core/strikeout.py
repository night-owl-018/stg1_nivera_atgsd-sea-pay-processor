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
    variants.add(f"{dt.month}/{dt.day}/{dt.year % 100:02d}")
    variants.add(f"{dt.month:02d}/{dt.day:02d}/{dt.year % 100:02d}")

    return variants


# ------------------------------------------------
# STRIKEOUT ENGINE
# ------------------------------------------------

def mark_sheet_with_strikeouts(original_pdf, skipped_duplicates, skipped_unknown, output_path, total_days):
    try:
        log(f"MARKING SHEET START → {os.path.basename(original_pdf)}")

        targets_invalid = {(u["date"], u["occ_idx"]) for u in skipped_unknown}
        targets_dup = {(d["date"], d["occ_idx"]) for d in skipped_duplicates}
        all_targets = targets_invalid.union(targets_dup)

        pages = convert_from_path(original_pdf)
        row_list = []

        # ------------------------------------------------
        # BUILD ALL ROWS (OCR token grouping)
        # ------------------------------------------------
        all_dates = {d for (d, _) in all_targets}
        date_variants_map = {d: _build_date_variants(d) for d in all_dates}

        for page_index, img in enumerate(pages):
            log(f"  BUILDING ROWS FROM PAGE {page_index+1}/{len(pages)}")

            data = pytesseract.image_to_data(img, output_type=Output.DICT)
            img_w, img_h = img.size
            scale_y = letter[1] / float(img_h)

            tokens = []
            n = len(data["text"])
            for j in range(n):
                txt = data["text"][j].strip()
                if not txt:
                    continue

                top = data["top"][j]
                h = data["height"][j]
                center_y_img = top + h / 2.0
                center_from_bottom_px = img_h - center_y_img
                y = center_from_bottom_px * scale_y

                tokens.append({"text": txt.upper(), "y": y})

            tokens.sort(key=lambda t: -t["y"])

            # group tokens into rows
            visual_rows = []
            current_row = []
            last_y = None
            threshold = 5.5

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
                text = " ".join(t["text"] for t in row)
                tmp_rows.append({
                    "page": page_index,
                    "y": y_avg,
                    "text": text,
                    "date": None,
                    "occ_idx": None,
                })

            tmp_rows.sort(key=lambda r: -r["y"])

            # detect date occurrences
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
        # DETECT STANDARD STRIKEOUT TARGETS
        # ------------------------------------------------
        strike_targets = {}

        # invalid rows
        for row in row_list:
            if row["date"] and row["occ_idx"] and (row["date"], row["occ_idx"]) in targets_invalid:
                strike_targets.setdefault(row["page"], []).append(row["y"])
                log(f"    STRIKEOUT INVALID {row['date']} OCC#{row['occ_idx']} PAGE {row['page']+1} Y={row['y']:.1f}")

        # duplicate rows
        for row in row_list:
            if row["date"] and row["occ_idx"] and (row["date"], row["occ_idx"]) in targets_dup:
                ys = strike_targets.get(row["page"], [])
                if not any(abs(y - row["y"]) < 0.1 for y in ys):
                    strike_targets.setdefault(row["page"], []).append(row["y"])
                    log(f"    STRIKEOUT DUP {row['date']} OCC#{row['occ_idx']} PAGE {row['page']+1} Y={row['y']:.1f}")

        # ------------------------------------------------
        # VISUAL UNDERLINE DETECTION FOR TOTAL DAYS
        # ------------------------------------------------
        total_row = None
        for row in row_list:
            if ("TOTAL" in row["text"]
                and "SEA" in row["text"]
                and "PAY" in row["text"]
                and "DAYS" in row["text"]):
                total_row = row
                break

        total_overlay = None

        if total_row:
            page_idx = total_row["page"]
            target_y_pdf = total_row["y"]

            img = pages[page_idx]
            width_img, height_img = img.size

            data = pytesseract.image_to_data(img, output_type=Output.DICT)

            underline_candidates = []

            for i in range(len(data["text"])):
                txt = data["text"][i].strip()
                if not txt:
                    continue

                left = data["left"][i]
                top = data["top"][i]
                w = data["width"][i]
                h = data["height"][i]

                if h < 12 and w > 8:  # underline shape
                    center_y_img = top + h / 2.0
                    center_from_bottom_px = height_img - center_y_img
                    pdf_y = center_from_bottom_px * (letter[1] / float(height_img))

                    if abs(pdf_y - target_y_pdf) < 3:
                        underline_candidates.append((left, w))

            if underline_candidates:
                min_x_img = min(u[0] for u in underline_candidates)
                max_x_img = max(u[0] + u[1] for u in underline_candidates)

                scale_x = letter[0] / float(width_img)
                pdf_x1 = min_x_img * scale_x
                pdf_x2 = max_x_img * scale_x
            else:
                # fallback (rare)
                pdf_x1 = 260
                pdf_x2 = 330

            buf = io.BytesIO()
            c = canvas.Canvas(buf, pagesize=letter)
            c.setLineWidth(0.8)
            c.setStrokeColorRGB(0, 0, 0)

            # draw strike through actual underline span
            c.line(pdf_x1, target_y_pdf, pdf_x2, target_y_pdf)

            # correct number
            correct_x = pdf_x2 + c.stringWidth("   ", "Helvetica", 10)
            c.setFont("Helvetica", 10)
            c.drawString(correct_x, target_y_pdf, str(total_days))

            c.save()
            buf.seek(0)
            total_overlay = PdfReader(buf)

        # ------------------------------------------------
        # STANDARD ROW STRIKEOUT OVERLAYS
        # ------------------------------------------------
        overlays = []
        for p in range(len(pages)):
            ys = strike_targets.get(p)
            if not ys:
                overlays.append(None)
                continue

            buf = io.BytesIO()
            c = canvas.Canvas(buf, pagesize=letter)
            c.setStrokeColorRGB(0, 0, 0)
            c.setLineWidth(0.8)

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

            # total-days first (must be on top)
            if total_overlay and total_row and i == total_row["page"]:
                page.merge_page(total_overlay.pages[0])

            # row strikeouts
            if i < len(overlays) and overlays[i] is not None:
                page.merge_page(overlays[i].pages[0])

            try:
                page.compress_content_streams()
            except Exception:
                pass

            writer.add_page(page)

        # output file
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "wb") as f:
            writer.write(f)

        log(f"MARKED SHEET CREATED → {os.path.basename(output_path)}")

    except Exception as e:
        log(f"⚠️ MARKING FAILED → {e}")
        try:
            shutil.copy2(original_pdf, output_path)
            log(f"FALLBACK COPY CREATED → {os.path.basename(output_path)}")
        except Exception as e2:
            log(f"⚠️ FALLBACK COPY FAILED → {e2}")
