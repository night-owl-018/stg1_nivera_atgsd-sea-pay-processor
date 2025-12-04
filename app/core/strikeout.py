import os
import shutil
from datetime import datetime
import io

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

def mark_sheet_with_strikeouts(original_pdf, skipped_duplicates, skipped_unknown, output_path):
    try:
        log(f"MARKING SHEET START → {os.path.basename(original_pdf)}")

        targets_invalid = {(u["date"], u["occ_idx"]) for u in skipped_unknown}
        targets_dup = {(d["date"], d["occ_idx"]) for d in skipped_duplicates}
        all_targets = targets_invalid.union(targets_dup)

        if not all_targets:
            shutil.copy2(original_pdf, output_path)
            log(f"NO STRIKEOUTS NEEDED, COPIED → {os.path.basename(output_path)}")
            return

        all_dates = {d for (d, _) in all_targets}
        date_variants_map = {d: _build_date_variants(d) for d in all_dates}

        pages = convert_from_path(original_pdf)
        row_list = []

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

        strike_targets = {}

        # FIRST invalid
        for row in row_list:
            if row["date"] is None or row["occ_idx"] is None:
                continue
            if (row["date"], row["occ_idx"]) in targets_invalid:
                strike_targets.setdefault(row["page"], []).append(row["y"])
                log(f"    STRIKEOUT INVALID {row['date']} OCC#{row['occ_idx']} PAGE {row['page']+1} Y={row['y']:.1f}")

        # THEN duplicates
        for row in row_list:
            if row["date"] is None or row["occ_idx"] is None:
                continue
            if (row["date"], row["occ_idx"]) in targets_dup:
                ys = strike_targets.get(row["page"], [])
                if not any(abs(y - row["y"]) < 0.1 for y in ys):
                    strike_targets.setdefault(row["page"], []).append(row["y"])
                    log(f"    STRIKEOUT DUP {row['date']} OCC#{row['occ_idx']} PAGE {row['page']+1} Y={row['y']:.1f}")

        if not strike_targets:
            shutil.copy2(original_pdf, output_path)
            log(f"NO STRIKEOUT POSITIONS FOUND, COPIED → {os.path.basename(output_path)}")
            return

        overlays = []
        for page_index in range(len(pages)):
            ys = strike_targets.get(page_index)
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

        reader = PdfReader(original_pdf)
        writer = PdfWriter()

        for i, page in enumerate(reader.pages):
            if i < len(overlays) and overlays[i] is not None:
                page.merge_page(overlays[i].pages[0])
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

