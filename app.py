import os
import re
import io
import csv
import zipfile
from datetime import datetime, timedelta
from difflib import get_close_matches

from flask import Flask, render_template, request, redirect, send_from_directory
from PyPDF2 import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter

import pytesseract
from pdf2image import convert_from_path

# ------------------------------------------------
# APP
# ------------------------------------------------
app = Flask(__name__)

DATA_DIR = "/data"
TEMPLATE_DIR = "/templates"
CONFIG_DIR = "/config"
OUTPUT_DIR = "/output"

for d in [DATA_DIR, TEMPLATE_DIR, CONFIG_DIR, OUTPUT_DIR]:
    os.makedirs(d, exist_ok=True)

DEFAULT_TEMPLATE = os.path.join(TEMPLATE_DIR, "NAVPERS_1070_613_TEMPLATE.pdf")
DEFAULT_RATE = os.path.join(CONFIG_DIR, "atgsd_n811.csv")

pytesseract.pytesseract.tesseract_cmd = "tesseract"

FONT_NAME = "Times-Roman"
FONT_SIZE = 10

# ------------------------------------------------
# SHIP LIST (UNCHANGED)
# ------------------------------------------------
SHIP_LIST = [ "America","Anchorage","Arleigh Burke","Arlington","Ashland","Augusta",
"Bainbridge","Barry","Bataan","Beloit","Benfold","Billings","Blue Ridge","Boxer","Bulkeley",
"Canberra","Cape St. George","Carl M. Levin","Carney","Carter Hall","Chafee","Charleston",
"Chief","Chosin","Chung-Hoon","Cincinnati","Cole","Comstock","Cooperstown","Curtis Wilbur",
"Daniel Inouye","Decatur","Delbert D. Black","Dewey","Donald Cook","Essex","Farragut",
"Fitzgerald","Forrest Sherman","Fort Lauderdale","Fort Worth","Frank E. Petersen Jr.",
"Gabrielle Giffords","Germantown","Gettysburg","Gonzalez","Gravely","Green Bay","Gridley",
"Gunston Hall","Halsey","Harpers Ferry","Higgins","Hopper","Howard","Indianapolis",
"Iwo Jima","Jackson","Jack H. Lucas","James E. Williams","Jason Dunham","John Basilone",
"John Finn","John P. Murtha","John Paul Jones","John S. McCain","Kansas City","Kearsarge",
"Kidd","Kingsville","Laboon","Lake Erie","Lassen","Lenah Sutcliffe Higbee","Mahan",
"Makin Island","Manchester","Marinette","Mason","McCampbell","McFaul","Mesa Verde",
"Michael Monsoor","Michael Murphy","Milius","Minneapolis-Saint Paul","Mitscher","Mobile",
"Momsen","Montgomery","Mount Whitney","Mustin","Nantucket","New Orleans","New York",
"Nitze","O'Kane","Oak Hill","Oakland","Omaha","Oscar Austin","Patriot","Paul Hamilton",
"Paul Ignatius","Pearl Harbor","Pinckney","Pioneer","Porter","Portland","Preble","Princeton",
"Rafael Peralta","Ralph Johnson","Ramage","Richard M. McCool Jr.","Robert Smalls","Roosevelt",
"Ross","Rushmore","Russell","Sampson","San Antonio","San Diego","Santa Barbara","Savannah",
"Shiloh","Shoup","Somerset","Spruance","St. Louis","Sterett","Stethem","Stockdale","Stout",
"The Sullivans","Tortuga","Tripoli","Truxtun","Tulsa","Warrior","Wasp","Wayne E. Meyer",
"William P. Lawrence","Winston S. Churchill","Wichita","Zumwalt" ]

def normalize(t):
    t = re.sub(r"\(.*?\)", "", t)
    t = re.sub(r"[^A-Z ]", "", t.upper())
    return " ".join(t.split())

NORMALIZED = {normalize(s): s.upper() for s in SHIP_LIST}
NORMAL_KEYS = list(NORMALIZED.keys())

def strip_times(text):
    return re.sub(r"\b[0-2]?\d[0-5]\d\b", "", text)

def extract_member_name(text):
    m = re.search(r"NAME:\s*([A-Z\s]+?)\s+SSN", text)
    if not m:
        raise RuntimeError("NAME NOT FOUND")
    return " ".join(m.group(1).split())

def match_ship(text):
    words = normalize(text).split()
    for size in range(len(words), 0, -1):
        for i in range(len(words) - size + 1):
            chunk = " ".join(words[i:i+size])
            match = get_close_matches(chunk, NORMAL_KEYS, n=1, cutoff=0.75)
            if match:
                return NORMALIZED[match[0]]
    return None

def extract_year(path):
    m = re.search(r"(20\d{2})", path)
    return m.group(1) if m else str(datetime.now().year)

def parse_rows(text, year):
    rows = []
    seen = set()
    lines = text.splitlines()

    for i, line in enumerate(lines):
        m = re.match(r"\s*(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?", line)
        if not m:
            continue

        mm,dd,yy = m.groups()
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
    groups = {}
    for r in rows:
        dt = datetime.strptime(r["date"], "%m/%d/%Y")
        groups.setdefault(r["ship"],[]).append(dt)

    out=[]
    for ship,dates in groups.items():
        dates=sorted(set(dates))
        start=prev=dates[0]
        for d in dates[1:]:
            if d==prev+timedelta(days=1):
                prev=d
            else:
                out.append({"ship":ship,"start":start,"end":prev})
                start=prev=d
        out.append({"ship":ship,"start":start,"end":prev})
    return out

def load_rates():
    rates={}
    if not os.path.exists(DEFAULT_RATE):
        return rates
    with open(DEFAULT_RATE, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for r in reader:
            last=r.get("last","").upper()
            first=r.get("first","").upper()
            rate=r.get("rate","")
            if last and rate:
                rates[f"{last},{first}"]=rate
    return rates

def get_rate(name, rates):
    parts = normalize(name).split()
    first,last = parts[0],parts[-1]
    return rates.get(f"{last},{first}","")

def ocr_pdf(path):
    images = convert_from_path(path)
    out=""
    for img in images:
        out+=pytesseract.image_to_string(img)
    return out.upper()

def make_pdf(group, name, rate):
    start = group["start"].strftime("%m-%d-%Y")
    end = group["end"].strftime("%m-%d-%Y")
    ship = group["ship"]

    parts=name.split()
    last=parts[-1]
    first=" ".join(parts[:-1])

    prefix = f"{rate}_" if rate else ""
    filename = f"{prefix}{last}_{first}_{ship}_{start}_TO_{end}.pdf".replace(" ","_")
    path = os.path.join(OUTPUT_DIR, filename)

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.setFont(FONT_NAME,10)

    c.drawString(39,689,"AFLOAT TRAINING GROUP SAN DIEGO (UIC. 49365)")
    c.drawString(373,671,"X")
    c.setFont(FONT_NAME,8)
    c.drawString(39,650,"ENTITLEMENT")
    c.setFont(FONT_NAME,10)
    c.drawString(345,641,"OPNAVINST 7220.14")
    c.drawString(39,41,f"{rate} {last}, {first}" if rate else f"{last}, {first}")
    c.drawString(38,595,f"____. REPORT CAREER SEA PAY FROM {start} TO {end}.")
    c.drawString(64,571,f"Member performed eight continuous hours per day on-board: {ship} Category A vessel.")

    c.save()
    buf.seek(0)

    overlay = PdfReader(buf)
    template = PdfReader(DEFAULT_TEMPLATE)

    template.pages[0].merge_page(overlay.pages[0])
    writer = PdfWriter()
    for p in template.pages:
        writer.add_page(p)

    with open(path,"wb") as f:
        writer.write(f)

def run_processor():
    logs=[]
    rates = load_rates()

    for file in os.listdir(DATA_DIR):
        if not file.lower().endswith(".pdf"): continue
        path = os.path.join(DATA_DIR, file)
        logs.append(f"[OCR] {file}")

        raw = strip_times(ocr_pdf(path))

        try:
            name = extract_member_name(raw)
            logs.append(f"[NAME] {name}")
        except:
            logs.append("[ERROR] NAME NOT FOUND")
            continue

        year = extract_year(file)
        rows = parse_rows(raw, year)
        groups = group_by_ship(rows)
        rate = get_rate(name, rates)

        for g in groups:
            make_pdf(g,name,rate)
            logs.append(f"[PDF] Created for {g['ship']}")

    return logs

@app.route("/", methods=["GET","POST"])
def index():
    logs=[]
    if request.method == "POST":
        logs = run_processor()

    return render_template("index.html",
        data=os.listdir(DATA_DIR),
        templates=os.listdir(TEMPLATE_DIR),
        config=os.listdir(CONFIG_DIR),
        output=os.listdir(OUTPUT_DIR),
        logs="\n".join(logs)
    )

@app.route("/upload/<folder>", methods=["POST"])
def upload(folder):
    dest = f"/{folder}"
    for f in request.files.getlist("files"):
        f.save(os.path.join(dest,f.filename))
    return redirect("/")

@app.route("/download/<name>")
def download(name):
    return send_from_directory(OUTPUT_DIR,name,as_attachment=True)

@app.route("/delete/<folder>/<name>")
def delete_file(folder,name):
    path = f"/{folder}/{name}"
    if os.path.exists(path):
        os.remove(path)
    return redirect("/")

if __name__ == "__main__":
    app.run(host="0.0.0.0",port=8080)
