# Use Python 3.13 slim to match your dev environment and keep image small
FROM python:3.13-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8899

# Install system dependencies
# ffmpeg is required by yt-dlp for audio extraction and format merging
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create directory structure for persistence
# app.py expects 'data' and 'data/downloads'
RUN mkdir -p data/downloads

# Create a non-root user for security
RUN adduser --disabled-password --gecos "" appuser && \
    chown -R appuser:appuser /app
USER appuser

# Expose the port defined in app.py
EXPOSE 8899

# Run using Gunicorn with Eventlet worker for best Flask-SocketIO performance
# -w 1: SocketIO typically requires 1 worker unless using sticky sessions
CMD ["gunicorn", "--worker-class", "eventlet", "-w", "1", "--bind", "0.0.0.0:8899", "app:app"]