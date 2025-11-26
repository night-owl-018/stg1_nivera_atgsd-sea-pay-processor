FROM python:3.12-slim

# Ensure that Python runs from /app
WORKDIR /app

# Install system libs required by pdfplumber + reportlab
RUN apt-get update && apt-get install -y \
    libglib2.0-0 \
    libsm6 \
    libxrender1 \
    libxext6 \
    libcairo2 \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy everything into the /app directory
COPY . .

# Add /app into Python path (CRITICAL FIX)
ENV PYTHONPATH="/app"

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3)" || exit 1

CMD ["python", "-m", "app.web"]
