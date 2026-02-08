"""
Module to add certifying officer information to TORIS certification sheets.
"""

import os
import io
import re
from PyPDF2 import PdfReader, PdfWriter
from reportlab.pdfgen import canvas

from app.core.logger import log
from app.core.config import get_certifying_officer_name


def add_certifying_officer_to_toris(input_pdf_path, output_pdf_path):
    """
    Add the certifying officer's name to a TORIS Sea Duty Certification Sheet PDF.

    Dynamically finds "PRINTED NAME OF CERTIFYING OFFICER" and places the
    certifying officer name between the two signature lines above that label.

    Fixes:
      1) Supports drawn lines (vector lines) instead of relying on underscore text.
      5) Uses a tighter label anchor (PRINTED + NAME + CERTIFYING + OFFICER sequence on same line area),
         and chooses the lowest match on the page (most likely the signature block).

    Args:
        input_pdf_path: Path to the TORIS sheet PDF
        output_pdf_path: Path where the updated PDF should be saved
    """
    try:
        certifying_officer_name = get_certifying_officer_name()

        if not certifying_officer_name:
            log(f"NO CERTIFYING OFFICER SET → Copying TORIS as-is: {os.path.basename(input_pdf_path)}")
            if input_pdf_path != output_pdf_path:
                import shutil
                shutil.copy2(input_pdf_path, output_pdf_path)
            return

        try:
            import pdfplumber

            with pdfplumber.open(input_pdf_path) as pdf:
                # Last page contains the certifying block
                page_index = len(pdf.pages) - 1
                page = pdf.pages[page_index]

                page_width = float(page.width)
                page_height = float(page.height)

                words = page.extract_words() or []

                # ----------------------------
                # 5) Tighter label anchor
                # ----------------------------
                # Find candidates where "PRINTED" is followed nearby by "NAME", "CERTIFYING", "OFFICER".
                # Choose the LOWEST on the page (largest 'top'), because the certifying block is near the bottom.
                candidates = []
                for i, w in enumerate(words):
                    if (w.get("text") or "").upper() != "PRINTED":
                        continue

                    printed_top = float(w.get("top", 0.0))
                    printed_x0 = float(w.get("x0", 0.0))

                    # Look ahead a bit for the expected keywords
                    lookahead = [
                        (words[i + j].get("text") or "").upper()
                        for j in range(1, 18)
                        if (i + j) < len(words)
                    ]

                    if "NAME" in lookahead and "CERTIFYING" in lookahead and "OFFICER" in lookahead:
                        candidates.append(w)

                if not candidates:
                    log("Could not find 'PRINTED NAME OF CERTIFYING OFFICER' label - using fallback copy")
                    raise Exception("Label not found")

                # Pick the lowest (signature block) match
                label_word = max(candidates, key=lambda x: float(x.get("top", 0.0)))
                label_top = float(label_word.get("top", 0.0))
                label_x0 = float(label_word.get("x0", 0.0))
                label_x1 = float(label_word.get("x1", label_x0))

                log(f"Found certifying label anchor at top={label_top:.1f}, x0={label_x0:.1f}")

                # -----------------------------------------
                # 1) Prefer drawn lines (vector) above label
                # -----------------------------------------
                # pdfplumber exposes vector lines in page.lines.
                # We locate two horizontal lines above the label in the same general block,
                # then place the name centered between them.
                def is_horizontal_line(ln: dict) -> bool:
                    y0 = float(ln.get("y0", ln.get("top", 0.0)))
                    y1 = float(ln.get("y1", ln.get("bottom", 0.0)))
                    return abs(y0 - y1) <= 1.5

                # Pull horizontal lines above label, within a reasonable vertical band
                # (no hard-coded final Y, just a search window to avoid grabbing random lines)
                vertical_band_top = max(0.0, label_top - 120.0)
                vertical_band_bottom = label_top - 2.0

                line_candidates = []
                for ln in (getattr(page, "lines", None) or []):
                    if not is_horizontal_line(ln):
                        continue

                    x0 = float(ln.get("x0", 0.0))
                    x1 = float(ln.get("x1", 0.0))
                    y = float(ln.get("y0", ln.get("top", 0.0)))  # y from top in pdfplumber coords

                    # Must be above label and within band
                    if not (vertical_band_top <= y <= vertical_band_bottom):
                        continue

                    # Must overlap the left signature block region (loosely based on label position)
                    # Expand right side generously to catch long lines.
                    if x1 < (label_x0 - 30.0):
                        continue
                    if x0 > (label_x1 + 350.0):
                        continue

                    # Must be a "real" signature line length (avoid tiny rules)
                    if (x1 - x0) < 150.0:
                        continue

                    line_candidates.append({"x0": x0, "x1": x1, "y": y})

                # Sort closest to label first (largest y under label_top)
                line_candidates.sort(key=lambda d: (label_top - d["y"]))

                # Pick two distinct line Y values (closest two)
                picked_lines = []
                for d in line_candidates:
                    if not picked_lines or abs(d["y"] - picked_lines[-1]["y"]) > 2.0:
                        picked_lines.append(d)
                    if len(picked_lines) == 2:
                        break

                if len(picked_lines) == 2:
                    # Convert line y (from top) to reportlab y (from bottom)
                    y1_from_bottom = page_height - picked_lines[0]["y"]
                    y2_from_bottom = page_height - picked_lines[1]["y"]
                    mid_y = (y1_from_bottom + y2_from_bottom) / 2.0

                    font_size = 10
                    name_y = mid_y - (font_size * 0.35)

                    # Align left edge to the line start
                    name_x = min(picked_lines[0]["x0"], picked_lines[1]["x0"]) + 2.0

                    log(
                        f"Vector lines found above label at y(top)={picked_lines[0]['y']:.1f} and "
                        f"{picked_lines[1]['y']:.1f}. Placing name at x={name_x:.1f}, y(bottom)={name_y:.1f}"
                    )
                else:
                    # If vector lines not found, fall back to underscore text detection (best-effort)
                    underscore_words = []
                    for w in words:
                        t = (w.get("text") or "")
                        if re.fullmatch(r"_+", t) and len(t) >= 10:
                            top = float(w.get("top", 0.0))
                            if top < label_top:
                                x0 = float(w.get("x0", 0.0))
                                x1 = float(w.get("x1", 0.0))
                                if not (x1 < (label_x0 - 30.0) or x0 > (label_x1 + 350.0)):
                                    underscore_words.append(w)

                    underscore_words.sort(key=lambda w: (label_top - float(w.get("top", 0.0))))

                    picked = []
                    for w in underscore_words:
                        w_top = float(w.get("top", 0.0))
                        if not picked or abs(w_top - float(picked[-1].get("top", 0.0))) > 2.0:
                            picked.append(w)
                        if len(picked) == 2:
                            break

                    if len(picked) == 2:
                        u1 = page_height - ((float(picked[0]["top"]) + float(picked[0]["bottom"])) / 2.0)
                        u2 = page_height - ((float(picked[1]["top"]) + float(picked[1]["bottom"])) / 2.0)
                        mid_y = (u1 + u2) / 2.0

                        font_size = 10
                        name_y = mid_y - (font_size * 0.35)
                        name_x = float(picked[0]["x0"]) + 2.0

                        log(
                            f"Underscore lines found. Placing '{certifying_officer_name}' at "
                            f"(X={name_x:.1f}, Y={name_y:.1f})"
                        )
                    else:
                        # Final fallback: relative to label (still dynamic, but not ideal)
                        label_y_from_bottom = page_height - label_top
                        name_y = label_y_from_bottom + 13
                        name_x = 63
                        log("No vector/underscore lines found reliably; using label-based fallback")
                        log(f"Placing '{certifying_officer_name}' at (X={name_x}, Y={name_y:.1f})")

                # Build overlay on the ACTUAL TORIS page size, not letter
                buf = io.BytesIO()
                c = canvas.Canvas(buf, pagesize=(page_width, page_height))
                c.setFont("Helvetica-Bold", 10)
                c.drawString(name_x, name_y, certifying_officer_name)
                c.save()
                buf.seek(0)

        except ImportError:
            log("⚠️ pdfplumber not installed - cannot dynamically position name")
            log("Install with: pip install pdfplumber")
            if input_pdf_path != output_pdf_path:
                import shutil
                shutil.copy2(input_pdf_path, output_pdf_path)
            return

        except Exception as e:
            log(f"⚠️ Error positioning certifying officer name: {e}")
            if input_pdf_path != output_pdf_path:
                import shutil
                shutil.copy2(input_pdf_path, output_pdf_path)
            return

        # Merge overlay into PDF (last page only)
        reader = PdfReader(input_pdf_path)
        overlay = PdfReader(buf)
        writer = PdfWriter()

        for i, pg in enumerate(reader.pages):
            if i == len(reader.pages) - 1:
                pg.merge_page(overlay.pages[0])
            writer.add_page(pg)

        with open(output_pdf_path, "wb") as f:
            writer.write(f)

        log(f"✅ ADDED CERTIFYING OFFICER TO TORIS → {certifying_officer_name}")

    except Exception as e:
        log(f"⚠️ ERROR ADDING CERTIFYING OFFICER TO TORIS → {e}")
        if input_pdf_path != output_pdf_path:
            import shutil
            try:
                shutil.copy2(input_pdf_path, output_pdf_path)
                log(f"FALLBACK COPY CREATED → {os.path.basename(input_pdf_path)}")
            except Exception as e2:
                log(f"⚠️ FALLBACK COPY FAILED → {e2}")
