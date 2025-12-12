#!/bin/sh

# -------------------------------------------------
# Ensure required directories exist FIRST
# -------------------------------------------------
mkdir -p /templates
mkdir -p /config
mkdir -p /data
mkdir -p /output

# -------------------------------------------------
# Install default template if missing
# -------------------------------------------------
if [ ! -f /templates/NAVPERS_1070_613_TEMPLATE.pdf ]; then
  echo "[INIT] Installing default template PDF"
  cp /app/pdf_template/NAVPERS_1070_613_TEMPLATE.pdf /templates/
fi

# -------------------------------------------------
# Install default CSV if missing
# -------------------------------------------------
if [ ! -f /config/atgsd_n811.csv ]; then
  echo "[INIT] Installing default rate CSV"
  cp /app/config/atgsd_n811.csv /config/
fi

echo "[INIT] Startup complete"

exec python /app/app.py
