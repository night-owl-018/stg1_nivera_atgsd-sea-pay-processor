"""
Module to add certifying officer information to TORIS certification sheets.
"""

import os
import io
import re
from PyPDF2 import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from app.core.logger import log
from app.core.config import get_certifying_officer_name

# 🔎 PATCH: prove what file is actually executing
log(f"TORIS CERT MODULE PATH → {__file__}")


def add_certifying_officer_to_toris(input_pdf_path, output_pdf_path):
    """
    Add the certifying officer's name to a TORIS Sea Duty Certification Sheet PDF.

    Dynamically finds "PRINTED NAME OF CERTIFYING OFFICER" and places the
    certifying officer name between the two signature lines above that label.

    Fixes:
      1) Supports drawn lines (vector lines) instead of relying on underscore text.
      5) Uses a tighter label anchor (PRINTED + NAME + CERTIFYING + OFFICER sequence on same line area),
         and chooses the lowest match on the page (most likely the signature block).
      6) Uses Times New Roman (Times_New_Roman.ttf) from repo root.
      7) Centers baseline within the two lines and clamps with padding so text never touches rules.
      8) Registers the font BEFORE any calls to pdfmetrics.getFont() or setFont() (fixes KeyError 'TimesNewRoman').
      9) DEBUG: prints baseline clamp math so we can prove if you’re clamping to the same Y each run.
     10) DEBUG: prints a patch fingerprint so you can confirm the container is running this exact version.
     11) PATCH: reduce pad + relax lower clamp slightly so baseline can sit lower (avoid touching top rule).

    Args:
        input_pdf_path: Path to the TORIS sheet PDF
        output_pdf_path: Path where the updated PDF should be saved
    """
    try:
        # 🔎 PATCH: fingerprint for runtime verification
        log("TORIS CERT PATCH CHECK → compute_baseline_between_rules DEBUG v2026-02-08-02")

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

                # Font choice
                font_name = "TimesNewRoman"
                font_size = 10

                # ----------------------------
                # Register Times New Roman from repo root (MUST happen before pdfmetrics.getFont())
                # ----------------------------
                if font_name not in pdfmetrics.getRegisteredFontNames():
                    font_path = os.path.abspath(
                        os.path.join(os.path.dirname(__file__), "..", "..", "Times_New_Roman.ttf")
                    )
                    if not os.path.exists(font_path):
                        raise FileNotFoundError(f"Times_New_Roman.ttf not found at: {font_path}")
                    pdfmetrics.registerFont(TTFont(font_name, font_path))
                    log(f"Registered font {font_name} from {font_path}")

                def compute_baseline_between_rules(y_a_from_bottom: float, y_b_from_bottom: float) -> float:
                    """
                    Place the baseline inside the two signature rules.

                    IMPORTANT:
                    On TORIS the band between rules is often very tight. If we use conservative
                    ascent/descent + padding, the clamp will force us to min_base every time.
                    This patch reduces padding and relaxes the lower clamp slightly so the
                    baseline can sit lower and stop kissing the upper rule.
                    """
                    f = pdfmetrics.getFont(font_name)
                    ascent = (float(getattr(f.face, "ascent", 700)) / 1000.0) * font_size
                    descent = (abs(float(getattr(f.face, "descent", -220))) / 1000.0) * font_size

                    # Identify band
                    lo = min(y_a_from_bottom, y_b_from_bottom)
                    hi = max(y_a_from_bottom, y_b_from_bottom)

                    # PATCH: smaller padding from rules (was 0.12)
                    pad = font_size * 0.06  # ~0.6pt @ 10pt

                    # Total text height
                    text_h = ascent + descent

                    band_h = hi - lo
                    free = band_h - (2 * pad) - text_h

                    # Safety fallback (extremely rare): keep it simple and safe
                    if free < 0:
                        min_base = lo + pad + descent
                        max_base = hi - pad - ascent

                        raw = (lo + hi) / 2.0
                        log(
                            "BASELINE DEBUG (free<0) → "
                            f"lo={lo:.2f} hi={hi:.2f} pad={pad:.2f} "
                            f"ascent={ascent:.2f} descent={descent:.2f} text_h={text_h:.2f} "
                            f"band_h={band_h:.2f} free={free:.2f} raw={raw:.2f} "
                            f"min_base={min_base:.2f} max_base={max_base:.2f}"
                        )
                        return max(min(raw, max_base), min_base)

                    # 🔑 Key control:
                    # smaller = lower placement, larger = higher placement
                    frac = 0.18

                    baseline = lo + pad + descent + (free * frac)

                    # extra small downward nudge (still scaled to font size, not a fixed Y)
                    baseline -= (font_size * 0.30)  # ~3.0pt? no, 0.30*10=3.0pt

                    # PATCH: relaxed clamp bounds (this is what changes the final result)
                    effective_descent = descent * 0.55   # allows a slightly lower baseline
                    effective_ascent = ascent * 0.95     # keep top safety mostly intact

                    min_base = lo + pad + effective_descent
                    max_base = hi - pad - effective_ascent

                    # tiny extra down nudge (still clamped)
                    baseline -= (font_size * 0.05)  # ~0.5pt @ 10pt

                    # 🔎 PATCH: full clamp debug
                    log(
                        "BASELINE DEBUG → "
                        f"lo={lo:.2f} hi={hi:.2f} pad={pad:.2f} "
                        f"ascent={ascent:.2f} descent={descent:.2f} text_h={text_h:.2f} "
                        f"band_h={band_h:.2f} free={free:.2f} frac={frac:.3f} "
                        f"raw={baseline:.2f} min_base={min_base:.2f} max_base={max_base:.2f}"
                    )

                    return max(min(baseline, max_base), min_base)

                # ----------------------------
                # 5) Tighter label anchor (same-line requirement)
                # ----------------------------
                candidates = []
                for i, w in enumerate(words):
                    if (w.get("text") or "").upper() != "PRINTED":
                        continue

                    printed_top = float(w.get("top", 0.0))

                    same_line = []
                    for j in range(1, 25):
                        if i + j >= len(words):
                            break
                        ww = words[i + j]
                        if abs(float(ww.get("top", 0.0)) - printed_top) <= 3.0:
                            same_line.append((ww.get("text") or "").upper())

                    if ("NAME" in same_line) and ("CERTIFYING" in same_line) and ("OFFICER" in same_line):
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
                def is_horizontal_line(ln: dict) -> bool:
                    y0 = float(ln.get("y0", ln.get("top", 0.0)))
                    y1 = float(ln.get("y1", ln.get("bottom", 0.0)))
                    return abs(y0 - y1) <= 1.5

                vertical_band_top = max(0.0, label_top - 120.0)
                vertical_band_bottom = label_top - 2.0

                line_candidates = []
                for ln in (getattr(page, "lines", None) or []):
                    if not is_horizontal_line(ln):
                        continue

                    x0 = float(ln.get("x0", 0.0))
                    x1 = float(ln.get("x1", 0.0))
                    y = float(ln.get("y0", ln.get("top", 0.0)))  # y from top in pdfplumber coords

                    if not (vertical_band_top <= y <= vertical_band_bottom):
                        continue

                    if x1 < (label_x0 - 30.0):
                        continue
                    if x0 > (label_x1 + 350.0):
                        continue

                    if (x1 - x0) < 150.0:
                        continue

                    line_candidates.append({"x0": x0, "x1": x1, "y": y})

                line_candidates.sort(key=lambda d: (label_top - d["y"]))

                picked_lines = []
                for d in line_candidates:
                    if not picked_lines or abs(d["y"] - picked_lines[-1]["y"]) > 2.0:
                        picked_lines.append(d)
                    if len(picked_lines) == 2:
                        break

                if len(picked_lines) == 2:
                    y1_from_bottom = page_height - picked_lines[0]["y"]
                    y2_from_bottom = page_height - picked_lines[1]["y"]

                    name_y = compute_baseline_between_rules(y1_from_bottom, y2_from_bottom)

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
                        u1_from_bottom = page_height - float(picked[0]["bottom"])
                        u2_from_bottom = page_height - float(picked[1]["top"])
                        
                        name_y = compute_baseline_between_rules(u1_from_bottom, u2_from_bottom)
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

                # 🔎 PATCH: prove what we are about to draw
                log(f"TORIS DRAW DEBUG → name_x={name_x:.2f} name_y={name_y:.2f} font={font_name} size={font_size}")

                # Build overlay on the ACTUAL TORIS page size, not letter
                buf = io.BytesIO()
                c = canvas.Canvas(buf, pagesize=(page_width, page_height))
                c.setFont(font_name, font_size)
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
