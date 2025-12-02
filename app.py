import os
import zipfile
from flask import Flask, render_template, request, send_file
from datetime import datetime
from PyPDF2 import PdfMerger

app = Flask(__name__)

UPLOAD_DIR = "/app/uploads"
OUTPUT_DIR = "/app/output"
MERGED_FILE = os.path.join(OUTPUT_DIR, "MERGED_OUTPUT.pdf")

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

print("================================================================")
print("[START] ATGSD SEA PAY PROCESSOR")
print("[PATH] UPLOAD_DIR:", UPLOAD_DIR)
print("[PATH] OUTPUT_DIR:", OUTPUT_DIR)
print("================================================================")

# ===========================
# MAIN PAGE
# ===========================
@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        files = request.files.getlist("files")
        template = request.files.get("template_file")

        if template:
            template_path = os.path.join(UPLOAD_DIR, "template.pdf")
            template.save(template_path)

        for f in files:
            fname = f.filename.replace(" ", "_")
            fpath = os.path.join(UPLOAD_DIR, fname)
            f.save(fpath)

            # Placeholder output creation
            # Your real processing is handled elsewhere
            output_name = os.path.splitext(fname)[0] + "_GENERATED.pdf"
            output_path = os.path.join(OUTPUT_DIR, output_name)

            with open(output_path, "wb") as out:
                out.write(b"%PDF-1.4\n% AUTO GENERATED FILE\n")

        return render_template("index.html", message="Processing complete!")

    return render_template("index.html", message=None)


# ===========================
# ✅ MERGED DOWNLOAD
# ===========================
@app.route("/download_merged")
def download_merged():
    pdfs = sorted([
        os.path.join(OUTPUT_DIR, f)
        for f in os.listdir(OUTPUT_DIR)
        if f.lower().endswith(".pdf")
        and f != "MERGED_OUTPUT.pdf"
    ])

    if not pdfs:
        return "No PDFs found to merge.", 404

    merger = PdfMerger()

    print("[MERGE] Files selected:")
    for pdf in pdfs:
        print("  -", pdf)
        merger.append(pdf)

    merger.write(MERGED_FILE)
    merger.close()

    print("[MERGE] SUCCESS →", MERGED_FILE)

    return send_file(MERGED_FILE, as_attachment=True)


# ===========================
# ✅ ZIP DOWNLOAD
# ===========================
@app.route("/download_all")
def download_all():
    zip_file = os.path.join(OUTPUT_DIR, "ALL_OUTPUTS.zip")

    with zipfile.ZipFile(zip_file, "w", zipfile.ZIP_DEFLATED) as z:
        for f in os.listdir(OUTPUT_DIR):
            if f.lower().endswith(".pdf") and f != "MERGED_OUTPUT.pdf":
                z.write(os.path.join(OUTPUT_DIR, f), f)

    return send_file(zip_file, as_attachment=True)


# ===========================
# START SERVER
# ===========================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
