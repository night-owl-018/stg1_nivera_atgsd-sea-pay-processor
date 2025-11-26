import os
import tempfile
from flask import Flask, render_template, request, redirect, url_for, send_file, flash

from app.extractor import extract_sailors_and_events
from app.generator import generate_pg13_zip
from app.config import SECRET_KEY


def create_app():
    """
    PG-13 Sea Pay Processor Flask Application Factory
    """

    template_dir = os.path.join(os.path.dirname(__file__), "templates_web")
    app = Flask(__name__, template_folder=template_dir)

    app.config["SECRET_KEY"] = SECRET_KEY

    @app.route("/", methods=["GET", "POST"])
    def index():

        print("DEBUG: request.method =", request.method)

        if request.method == "POST":

            print("DEBUG: request.files keys =", list(request.files.keys()))

            if "pdf_file" not in request.files:
                print("ERROR: pdf_file not in request.files")
                flash("Upload failed: backend did not receive file.")
                return redirect(url_for("index"))

            file = request.files["pdf_file"]
            print("DEBUG: file object =", file)
            print("DEBUG: filename =", file.filename)

            if not file:
                print("ERROR: file is None")
                flash("File missing.")
                return redirect(url_for("index"))

            if file.filename == "":
                print("ERROR: Empty filename")
                flash("Please select a PDF file.")
                return redirect(url_for("index"))

            if not file.filename.lower().endswith(".pdf"):
                print("ERROR: Not a PDF")
                flash("Only PDF files are accepted.")
                return redirect(url_for("index"))

            # Save uploaded file
            temp_dir = tempfile.mkdtemp()
            pdf_path = os.path.join(temp_dir, file.filename)
            file.save(pdf_path)

            print("DEBUG: Saved PDF to", pdf_path)

            sailors = extract_sailors_and_events(pdf_path)
            print("DEBUG: Extracted sailors =", sailors)

            if not sailors:
                print("ERROR: No sailors found")
                flash("No valid sailors or events found in PDF.")
                return redirect(url_for("index"))

            sailor = sailors[0]
            print("DEBUG: Processing sailor:", sailor)

            zip_path = generate_pg13_zip(sailor, output_dir=temp_dir)
            print("DEBUG: Generated ZIP:", zip_path)

            return send_file(
                zip_path,
                as_attachment=True,
                download_name=os.path.basename(zip_path)
            )

        return render_template("index.html")

    @app.route("/health", methods=["GET"])
    def health():
        return {"status": "ok"}, 200

    return app


# Required for python -m app.web
app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
