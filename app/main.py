import os
import tempfile
import zipfile

from flask import Flask, request, send_file, render_template

from app.extractor import extract_sailors_and_events
from app.generator import generate_pg13_for_sailor

app = Flask(__name__, template_folder="templates_web")

@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")

@app.route("/generate", methods=["POST"])
def generate():
    if "sea_file" not in request.files:
        return "Missing SEA DUTY CERT PDF.", 400

    sea_file = request.files["sea_file"]
    if sea_file.filename == "":
        return "No file selected.", 400

    tmp_dir = tempfile.mkdtemp()
    try:
        sea_path = os.path.join(tmp_dir, sea_file.filename)
        sea_file.save(sea_path)

        sailors = extract_sailors_and_events(sea_path)
        if not sailors:
            return "No valid sailors/events found. Check the PDF format.", 400

        output_root = os.path.join(tmp_dir, "output")
        os.makedirs(output_root, exist_ok=True)

        # One folder per sailor (using last name)
        for sailor in sailors:
            last_name = sailor["name"].split()[-1].upper()
            sailor_dir = os.path.join(output_root, last_name)
            generate_pg13_for_sailor(sailor, sailor_dir)

        # Zip everything
        zip_path = os.path.join(tmp_dir, "pg13_output.zip")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, dirs, files in os.walk(output_root):
                for f in files:
                    full = os.path.join(root, f)
                    rel = os.path.relpath(full, output_root)
                    zf.write(full, rel)

        return send_file(
            zip_path,
            mimetype="application/zip",
            as_attachment=True,
            download_name="pg13_output.zip",
        )

    finally:
        # You can add cleanup here if desired
        pass

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
