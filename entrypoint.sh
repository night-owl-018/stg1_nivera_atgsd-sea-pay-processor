#!/bin/sh
set -eu

# -------------------------------------------------
# Unraid mappings in your template point to:
#   /app/data, /app/output, /app/pdf_template, /app/config
# So we seed/verify USING THOSE PATHS.
# -------------------------------------------------
mkdir -p /app/pdf_template /app/config /app/data /app/output

# -------------------------------------------------
# Install default template if missing (seed into mounted /app/pdf_template)
# -------------------------------------------------
if [ ! -f /app/pdf_template/NAVPERS_1070_613_TEMPLATE.pdf ]; then
  echo "[INIT] Installing default template PDF -> /app/pdf_template"
  # In this image, the "default" template is shipped at the same path.
  # If the user hasn't mounted one in, this will exist and the check above would have passed.
  # If it's missing entirely, fail fast.
  if [ -f /app/pdf_template/NAVPERS_1070_613_TEMPLATE.pdf ]; then
    cp -f /app/pdf_template/NAVPERS_1070_613_TEMPLATE.pdf /app/pdf_template/
  else
    echo "[ERROR] Template PDF not found in image at /app/pdf_template/NAVPERS_1070_613_TEMPLATE.pdf" >&2
    exit 1
  fi
fi

# -------------------------------------------------
# Install default roster CSV if missing (seed into mounted /app/config)
# -------------------------------------------------
if [ ! -f /app/config/atgsd_n811.csv ]; then
  echo "[INIT] Installing default rate CSV -> /app/config"
  if [ -f /app/config/atgsd_n811.csv ]; then
    cp -f /app/config/atgsd_n811.csv /app/config/
  else
    echo "[ERROR] Rate CSV not found in image at /app/config/atgsd_n811.csv" >&2
    exit 1
  fi
fi

# -------------------------------------------------
# Optional: allow ship list override via mounted /app/config/ships.txt
# (the app reads /app/ships.txt)
# -------------------------------------------------
if [ -f /app/config/ships.txt ]; then
  echo "[INIT] Using ship list override from /app/config/ships.txt"
  cp -f /app/config/ships.txt /app/ships.txt
fi

# -------------------------------------------------
# Verify critical files exist
# -------------------------------------------------
echo "[INIT] Verifying environment..."
echo "  Template:   $(ls -lh /app/pdf_template/NAVPERS_1070_613_TEMPLATE.pdf 2>/dev/null || echo 'MISSING')"
echo "  Rate CSV:   $(ls -lh /app/config/atgsd_n811.csv 2>/dev/null || echo 'MISSING')"
echo "  Ships list: $(ls -lh /app/ships.txt 2>/dev/null || echo 'MISSING')"
echo "  Data dir:   $(ls -ld /app/data 2>/dev/null || echo 'MISSING')"
echo "  Output dir: $(ls -ld /app/output 2>/dev/null || echo 'MISSING')"

echo "[INIT] Startup complete - starting Flask app"
exec python /app/app.py
