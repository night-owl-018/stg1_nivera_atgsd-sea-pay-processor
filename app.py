import os
import re
import io
import csv
from datetime import datetime, timedelta
from difflib import get_close_matches

from flask import Flask, render_template, request, redirect, send_from_directory
from PyPDF2 import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter

import pytesseract
from pdf2image import convert_from_path

# ------------------------------------------------
# APP INIT
# ------------------------------------------------
app = Flask(__name__)

# ------------------------------------------------
# DIRECTORIES INSIDE CONTAINER
# ------------------------------------------------
DATA_DIR     = "/data"
TEMPLATE_DIR = "/templates"
CONFIG_DIR   = "/config"
OUTPUT_DIR   = "/output"

DEFAULT_TEMPLATE  = os.path.join(TEMPLATE_DIR, "NAVPERS_1070_613_TEMPLATE.pdf")
DEFAULT_RATES_CSV = os.path.join(CONFIG_DIR, "atgsd_n811.csv")

for d in [DATA_DIR, TEMPLATE_DIR, CONFIG_DIR, OUTPUT_DIR]:
    os.makedirs(d, exist_ok=True)

# ------------------------------------------------
# OCR CONFIG
# ------------------------------------------------
pytesseract.pytesseract.tesseract_cmd = "tesseract"

# ------------------------------------------------
# PDF FONT
# ------------------------------------------------
FONT_NAME = "Times-Roman"
FONT_SIZE = 10

# ------------------------------------------------
# SHIP LIST
# ------------------------------------------------
SHIP_LIST = [
    "America","Anchorage","Arleigh Burke","Arlington","Ashland","Augusta",
    "Bainbridge","Barry","Bataan","Beloit","Benfold","Billings","Blue Ridge",
    "Boxer","Bulkeley","Canberra","Cape St. George","Carl M. Levin","Carney",
    "Carter Hall","Chafee","Charleston","Chief","Chosin","Chung-Hoon",
    "Cincinnati","Cole","Comstock","Cooperstown","Curtis Wilbur",
    "Daniel Inouye","Decatur","Delbert D. Black","Dewey","Donald Cook","Essex",
    "Farragut","Fitzgerald","Forrest Sherman","Fort Lauderdale","Fort Worth",
    "Frank E. Petersen Jr.","Gabrielle Giffords","Germantown","Gettysburg",
    "Gonzalez","Gravely","Green Bay","Gridley","Gunston Hall","Halsey",
    "Harpers Ferry","Higgins","Hopper","Howard","Indianapolis","Iwo Jima",
    "Jackson","Jack H. Lucas","James E. Williams","Jason Dunham",
    "John Basilone","John Finn","John P. Murtha","John Paul Jones",
    "John S. McCain","Kansas City","Kearsarge","Kidd","Kingsville",
    "Laboon","Lake Erie","Lassen","Lenah Sutcliffe Higbee",
    "Mahan","Makin Island","Manchester","Marinette","Mason","McCampbell",
    "McFaul","Mesa Verde","Michael Monsoor","Michael Murphy","Milius",
    "Minneapolis-Saint Paul","Mitscher","Mobile","Momsen","Montgomery",
    "Mount Whitney","Mustin","Nantucket","New Orleans","New York","Nitze",
    "O'Kane","Oak Hill","Oakland","Omaha","Oscar Austin","Patriot",
    "Paul Hamilton","Paul Ignatius","Pearl Harbor","Pinckney","Pioneer",
    "Porter","Portland","Preble","Princeton","Rafael Peralta","Ralph Johnson",
    "Ramage","Richard M. McCool Jr.","Robert Smalls","Roosevelt","Ross",
    "Rushmore","Russell","Sampson","San Antonio","San Diego","Santa Barbara",
    "Savannah","Shiloh","Shoup","Somerset","Spruance","St. Louis","Sterett",
    "Stethem","Stockdale","Stout","The Sullivans","Tortuga","Tripoli",
    "Truxtun","Tulsa","Warrior","Wasp","Wayne E. Meyer",
    "William P. Lawrence","Winston S. Churchill","Wichita","Zumwalt"
]

def normalize(text):
    import re
    text = re.sub(r"\(.*?\)", "", text)
    text = re.sub(r"[^A-Z ]", "", text.upper())
    return " ".join(text.split())

NORMALIZED_SHIPS = {normalize(s): s.upper() for s in SHIP_LIST}
NORMAL_KEYS = list(NORMALIZED_SHIPS.keys())

# ------------------------------------------------
# CORE PDF + OCR FUNCTIONS
# ------------------------------------------------
def strip_times(text):
    return re.sub(r"\b[0-2]?\d[0-5]\d\b", "", text)

def extract_member_name(text):
    m = re.search(r"NAME:\s*([A-Z\s]+?)\s+SSN", text)
    if not m:
        raise RuntimeError("NAME not found")
    return " ".join(m.group(1).split())

def match_ship(text):
    candidate = normalize(text)
    words = candidate.split()
    for size in range(len(words), 0, -1):
        for i in range(len(words) - size + 1):
            chunk = " ".join(words[i:i+size])
            match = get_close_matches(chunk, NORMAL_KEYS, n=1, cutoff=0.75)
            if match:
                return NORMALIZED_SHIPS[match[0]]
    return None

def extract_year_from_filename(path):
    import re
    m = re.search(r"(20\d{2})", os.path.basename(path))
    return m.group(1) if m else str(datetime.now().year)

def parse_rows(text, year):
    rows, seen = [], set()
    lines = text.splitlines()

    for i, line in enumerate(lines):
        m = re.match(r"\s*(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?", line)
        if not m:
            continue

        mm, dd, yy = m.groups()
        y = ("20"+yy) if yy and len(yy)==2 else yy if yy else year
        date = f"{mm.zfill(2)}/{dd.zfill(2)}/{y}"

        raw = line[m.end():]
        if i+1 < len(lines):
            raw += " " + lines[i+1]

        ship = match_ship(raw)
        if ship and (date,ship) not in seen:
            rows.append({"date":date,"ship":ship})
            seen.add((date,ship))
    return rows

def group_by_ship(rows):
    from collections import defaultdict
    groups = defaultdict(list)
    for r in rows:
        groups[r["ship"]].append(datetime.strptime(r["date"],"%m/%d/%Y"))

    out=[]
    for ship,dates in groups.items():
        dates=sorted(set(dates))
        start=prev=dates[0]
        for d in dates[1:]:
            if d==prev+timedelta(days=1):
                prev=d
            else:
                out.append({"ship":ship,"start":start.strftime("%m/%d/%Y"),"end":prev.strftime("%m/%d/%Y")})
                start=prev=d
        out.append({"ship":ship,"start":start.strftime("%m/%d/%Y"),"end":prev.strftime("%m/%d/%Y")})
    return out

def load_rates(csv_path, log):
    rates={}
    if not os.path.exists(csv_path):
        log(f"[RATES] Missing: {csv_path}")
        return rates

    with open(csv_path, newline='', encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            last=row.get("last","").upper()
            first=row.get("first","").upper()
            rate=row.get("rate","").upper()
            if last and rate:
                rates[f"{last},{first}"]=rate

    log(f"[RATES] Loaded {len(rates)}")
    return rates

def get_rate(name, rates):
    parts = normalize(name).split()
    if len(parts)<2: return ""
    first,last=parts[0],parts[-1]
    if f"{last},{first}" in rates:
        return rates[f"{last},{first}"]
    for k in rates:
        if k.startswith(last+","):
            return rates[k]
    return ""

def ocr_pdf(path, log):
    log(f"[OCR] {path}")
    images=convert_from_path(path)
    text=""
    for img in images:
        text+=pytesseract.image_to_string(img)
    return text.upper()

def make_pdf(group, name, rate, log):
    start,end,ship=group.values()
    parts=name.split()
    last=parts[-1]
    first=" ".join(parts[:-1])
    prefix=f"{rate}_" if rate else ""
    filename=f"{prefix}{last}_{first}_{ship}_{start.replace('/','-')}_TO_{end.replace('/','-')}.pdf".replace(" ","_")
    outpath=os.path.join(OUTPUT_DIR,filename)

    buf=io.BytesIO()
    c=canvas.Canvas(buf,pagesize=letter)
    c.setFont(FONT_NAME,10)
    c.drawString(39,689,"AFLOAT TRAINING GROUP SAN DIEGO (UIC. 49365)")
    c.drawString(373,671,"X")
    c.setFont(FONT_NAME,8)
    c.drawString(39,650,"ENTITLEMENT")
    c.drawString(345,641,"OPNAVINST 7220.14")

    c.setFont(FONT_NAME,10)
    c.drawString(39,41,f"{rate} {last}, {first}" if rate else f"{last}, {first}")
    c.drawString(38.8,595,f"____. REPORT CAREER SEA PAY FROM {start} TO {end}.")
    c.drawString(64,571,f"Member performed eight continuous hours per day on-board: {ship} Category A vessel.")

    c.drawString(356.2,499.5,"_________________________")
    c.drawString(363.8,487.5,"Certifying Official & Date")
    c.drawString(356.2,427.5,"_________________________")
    c.drawString(384.1,415.2,"FI MI Last Name")
    c.drawString(38.8,83,"SEA PAY CERTIFIER")
    c.drawString(503.5,41,"USN AD")

    c.save()
    buf.seek(0)

    overlay=PdfReader(buf)
    template=PdfReader(DEFAULT_TEMPLATE)
    page0=template.pages[0]
    page0.merge_page(overlay.pages[0])
    writer=PdfWriter()
    writer.add_page(page0)
    for i in range(1,len(template.pages)):
        writer.add_page(template.pages[i])

    with open(outpath,"wb") as f:
        writer.write(f)

    log(f"[PDF] {outpath}")

def merge_with_bookmarks(log):
    pdfs=[f for f in os.listdir(OUTPUT_DIR) if f.endswith(".pdf") and not f.startswith("MASTER")]
    if not pdfs:
        log("[MERGE] None")
        return
    writer=PdfWriter()
    page=0
    for f in sorted(pdfs):
        reader=PdfReader(os.path.join(OUTPUT_DIR,f))
        writer.add_outline_item(f.replace(".pdf",""),page)
        for p in reader.pages:
            writer.add_page(p)
            page+=1
    out=os.path.join(OUTPUT_DIR,"MASTER_SEA_PAY_PACKET.pdf")
    with open(out,"wb") as f:
        writer.write(f)
    log(f"[MERGE] {out}")

def run():
    logs=[]
    log=lambda m: logs.append(m)
    rates=load_rates(DEFAULT_RATES_CSV, log)

    for file in os.listdir(DATA_DIR):
        if not file.lower().endswith(".pdf"): continue
        path=os.path.join(DATA_DIR,file)
        raw=strip_times(ocr_pdf(path,log))
        try:
            name=extract_member_name(raw)
            log(f"[NAME] {name}")
        except:
            log("[ERROR] Name not found")
            continue
        year=extract_year_from_filename(path)
        groups=group_by_ship(parse_rows(raw,year))
        rate=get_rate(name,rates)
        for g in groups:
            make_pdf(g,name,rate,log)

    merge_with_bookmarks(log)
    return logs

# ------------------------------------------------
# ROUTES
# ------------------------------------------------
@app.route("/", methods=["GET","POST"])
def index():
    logs=[]
    if request.method=="POST":
        logs=run()

    return render_template("index.html",
        template_files=os.listdir(TEMPLATE_DIR),
        rate_files=os.listdir(CONFIG_DIR),
        data_files=os.listdir(DATA_DIR),
        output_files=os.listdir(OUTPUT_DIR),
        logs="\n".join(logs)
    )

@app.route("/upload-template",methods=["POST"])
def upload_template():
    f=request.files["file"]
    if f: f.save(os.path.join(TEMPLATE_DIR,f.filename))
    return redirect("/")

@app.route("/upload-rate",methods=["POST"])
def upload_rate():
    f=request.files["file"]
    if f: f.save(os.path.join(CONFIG_DIR,f.filename))
    return redirect("/")

@app.route("/upload-data",methods=["POST"])
def upload_data():
    for f in request.files.getlist("files"):
        if f.filename:
            f.save(os.path.join(DATA_DIR,f.filename))
    return redirect("/")

@app.route("/download-output/<name>")
def download_output(name):
    return send_from_directory(OUTPUT_DIR,name,as_attachment=True)

@app.route("/delete/<folder>/<name>")
def delete_file(folder,name):
    path=f"/{folder}/{name}"
    if os.path.exists(path):
        os.remove(path)
    return redirect("/")

# ------------------------------------------------
# START
# ------------------------------------------------
if __name__=="__main__":
    app.run(host="0.0.0.0", port=8080)
