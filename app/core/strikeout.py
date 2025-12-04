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
        # BUILD ALL ROWS
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
        # DETECT STRIKEOUT TARGETS
        # ------------------------------------------------
        strike_targets = {}

        # invalid entries
        for row in row_list:
            if row["date"] is None or row["occ_idx"] is None:
                continue
            if (row["date"], row["occ_idx"]) in targets_invalid:
                strike_targets.setdefault(row["page"], []).append(row["y"])
                log(f"    STRIKEOUT INVALID {row['date']} OCC#{row['occ_idx']} PAGE {row['page']+1} Y={row['y']:.1f}")

        # duplicate entries
        for row in row_list:
            if row["date"] is None or row["occ_idx"] is None:
                continue
            if (row["date"], row["occ_idx"]) in targets_dup:
                ys = strike_targets.get(row["page"], [])
                if not any(abs(y - row["y"]) < 0.1 for y in ys):
                    strike_targets.setdefault(row["page"], []).append(row["y"])
                    log(f"    STRIKEOUT DUP {row['date']} OCC#{row['occ_idx']} PAGE {row['page']+1} Y={row['y']:.1f}")

        # ------------------------------------------------
        # LOCATE AND STRIKEOUT TOTAL DAYS ROW
        # ------------------------------------------------
        total_row = None
        for row in row_list:
            if ("TOTAL" in row["text"] and "SEA" in row["text"]
                and "PAY" in row["text"] and "DAYS" in row["text"]):
                total_row = row
                break

        # Build total_days overlay
        total_overlay = None

        if total_row:
            page_index = total_row["page"]
            y = total_row["y"]

            # find old number
            m = re.search(r"(\d+)$", total_row["text"])
            old_value = m.group(1) if m else ""

            buf = io.BytesIO()
            c = canvas.Canvas(buf, pagesize=letter)
            c.setFont("Helvetica", 10)

            base_x = 40
            label = "TOTAL SEA PAY DAYS FOR THIS REPORTING PERIOD: "
            label_width = c.stringWidth(label, "Helvetica", 10)
            num_x = base_x + label_width

            # strike old number
            if old_value:
                width_old = c.stringWidth(old_value, "Helvetica", 10)
                c.setLineWidth(0.8)
                c.line(num_x, y, num_x + width_old, y)

            # print correct number 3 spaces after
            correct_x = num_x + c.stringWidth(old_value + "   ", "Helvetica", 10)
            c.drawString(correct_x, y, str(total_days))

            c.save()
            buf.seek(0)
            total_overlay = PdfReader(buf)

        # ------------------------------------------------
        # NORMAL ROW STRIKEOUT OVERLAYS
        # ------------------------------------------------
        overlays = []
        for page_index2 in range(len(pages)):
            ys = strike_targets.get(page_index2)
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
        # APPLY ALL OVERLAYS (FIXED ORDER)
        # ------------------------------------------------
        reader = PdfReader(original_pdf)
        writer = PdfWriter()

        for i, page in enumerate(reader.pages):

            # FIRST: apply total-days overlay (so it appears ON TOP)
            if total_overlay and total_row and i == total_row["page"]:
                page.merge_page(total_overlay.pages[0])

            # SECOND: apply row strikeouts
            if i < len(overlays) and overlays[i] is not None:
                page.merge_page(overlays[i].pages[0])

            # ensure overlays float above everything
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
            log(f"FALLBACK COPY CREATED → {os.path.basename(output_path)}")
        except Exception as e2:
            log(f"⚠️ FALLBACK COPY FAILED → {e2}")
