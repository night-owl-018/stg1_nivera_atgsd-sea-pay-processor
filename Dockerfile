# HARD RESET: clean Python, no layers cached
FROM python:3.12-slim AS base

# Force logs to flush instantly (your logs were being buffered!)
ENV PYTHONUNBUFFERED=1

# Set working directory
WORKDIR /app

# Copy requirements
COPY requirements.txt /app/

# Install Python libs
RUN pip install --no-cache-dir -r requirements.txt

# Copy entire project
COPY . .

# Expose port
EXPOSE 8080

# RUN THIS EXACT FILE
CMD ["python", "-u", "app/web.py"]
