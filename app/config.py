import os

SECRET_KEY = os.environ.get("SECRET_KEY", "supersecretkey123456")
NAME_PREFIX = "Name:"
SIGNATURE_MARKER = "SIGNATURE"
SKIP_KEYWORD = "MITE"

PG13_TEMPLATE_PATH = "app/templates_pdf/NAVPERS_1070_613_TEMPLATE.pdf"
