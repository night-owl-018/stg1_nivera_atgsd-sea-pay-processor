import os
from datetime import date
from PyPDF2 import PdfReader, PdfWriter
from PyPDF2.generic import NameObject, BooleanObject
from app.config import PG13_TEMPLATE_PATH, NAME_FIELD, DATE_FIELD, SHIP_FIELD

def fmt_mdy_short(d: date) -> str:
    """Format date as M/D/YY with no leading zero. Example: 8/4/25"""
    return f"{d.month}/{d.day}/{str(d.year)[2:]}"

def build_date_line(start: date, end: date) -> str:
    """____. REPORT CAREER SEA PAY FROM 8/4/25 TO 8/8/25."""
    return (
        f"____. REPORT CAREER SEA PAY FROM {fmt_mdy_short(start)} "
        f"TO {fmt_mdy_short(end)}."
    )

def build_ship_line(ship: str) -> str:
    """Member performed eight continuous hours per day on-board: CHOSIN Category A vessel."""
    return (
        f"Member performed eight continuous hours per day on-board: "
        f"{ship.upper()} Category A vessel."
    )

def normalize_name_for_pg13(full_name: str) -> str:
    """Convert 'BRANDON ANDERSEN' -> 'ANDERSEN, BRANDON'"""
    parts = full_name.split()
    if len(parts) == 1:
        return parts[0].upper()
    last = parts[-1].upper()
    first = parts[0].upper()
    middle = " ".join(p.upper() for p in parts[1:-1])
    if middle:
        return f"{last}, {first} {middle}"
    return f"{last}, {first}"

def generate_pg13_for_sailor(sailor, output_dir):
    """
    sailor: { "name": "BRANDON ANDERSEN", "events": [(ship, start, end), ...] }
    """
    os.makedirs(output_dir, exist_ok=True)
    name_pg13 = normalize_name_for_pg13(sailor["name"])
    idx = 1

    for ship, start, end in sailor["events"]:
        reader = PdfReader(PG13_TEMPLATE_PATH)
        writer = PdfWriter()
        writer.add_page(reader.pages[0])

        fields = {
            NAME_FIELD: name_pg13,
            DATE_FIELD: build_date_line(start, end),
            SHIP_FIELD: build_ship_line(ship),
        }

        writer.update_page_form_field_values(writer.pages[0], fields)

        # Force viewers to regenerate appearance
        if "/AcroForm" in writer._root_object:
            writer._root_object["/AcroForm"].update(
                {NameObject("/NeedAppearances"): BooleanObject(True)}
            )

        safe_ship = ship.replace(" ", "_").upper()
        out_name = f"PG13_{name_pg13.split(',')[0]}_{idx:02d}_{safe_ship}.pdf"
        out_path = os.path.join(output_dir, out_name)

        with open(out_path, "wb") as f:
            writer.write(f)

        idx += 1
