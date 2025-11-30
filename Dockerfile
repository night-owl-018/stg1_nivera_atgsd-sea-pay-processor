FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    poppler-utils \
    libgl1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt \
 && pip install --no-cache-dir pycryptodome

COPY . .

# ✅ ADD THIS PART — DO NOT REMOVE ANYTHING ABOVE
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8080

# ✅ REPLACE CMD LINE ONLY
CMD ["/entrypoint.sh"]

