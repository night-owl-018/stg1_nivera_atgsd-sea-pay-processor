import os
from flask import Flask, render_template, request, redirect, send_from_directory

DATA_DIR = "/data"
OUTPUT_DIR = "/output"
TEMPLATE_PATH = "/templates/NAVPERS_1070_613_TEMPLATE.pdf"
RATE_PATH = "/config/atgsd_n811.csv"

# Ensure mount points exist inside container
for p in [DATA_DIR, OUTPUT_DIR, "/templates", "/config"]:
    os.makedirs(p, exist_ok=True)

# ✅ IMPORTANT FIX — Point to the correct folder
app = Flask(__name__, template_folder="web/frontend")

@app.route("/", methods=["GET", "POST"])
def index():
    logs = ""

    if request.method == "POST":

        # Upload PDFs
        for f in request.files.getlist("files"):
            if f.filename:
                f.save(os.path.join(DATA_DIR, f.filename))

        # Replace template if uploaded
        tpl = request.files.get("template_file")
        if tpl and tpl.filename:
            tpl.save(TEMPLATE_PATH)

        # Replace CSV if uploaded
        csv = request.files.get("rate_file")
        if csv and csv.filename:
            csv.save(RATE_PATH)

        logs = process_all()

    return render_template(
        "index.html",
        data_files=os.listdir(DATA_DIR),
        outputs=os.listdir(OUTPUT_DIR),
        logs=logs,
        template_path=TEMPLATE_PATH,
        rate_path=RATE_PATH,
        output_path=OUTPUT_DIR
    )

@app.route("/delete/<folder>/<name>")
def delete_file(folder, name):
    base = DATA_DIR if folder == "data" else OUTPUT_DIR if folder == "output" else None
    if base:
        path = os.path.join(base, name)
        if os.path.exists(path):
            os.remove(path)
    return redirect("/")

@app.route("/download/<name>")
def download(name):
    return send_from_directory(OUTPUT_DIR, name, as_attachment=True)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
