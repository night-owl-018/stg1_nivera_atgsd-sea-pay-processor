FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . /app

RUN pip install --no-cache-dir pytesseract pdf2image reportlab PyPDF2 pillow
RUN pip install --no-cache-dir -r requirements.txt

EXPOSE 8092

CMD ["uvicorn", "web.backend.main:app", "--host", "0.0.0.0", "--port", "8092"]
