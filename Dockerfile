FROM python:3.11-slim

# System deps
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    poppler-utils \
    libgl1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements first for layer caching
COPY requirements.txt .

# Install Python deps (single source of truth)
RUN pip install --no-cache-dir -r requirements.txt

# Safety net: enforce AES support
RUN pip install --no-cache-dir pycryptodome

# Copy project files
COPY . .

EXPOSE 8080

CMD ["python", "app.py"]


