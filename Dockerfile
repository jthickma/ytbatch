FROM python:3.13-slim

# Set env vars
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8899

# Install ffmpeg (required for yt-dlp audio processing)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app code
COPY . .

# Create data directories explicitly and set permissions
# This allows the non-root user to write to 'data' inside the container
RUN mkdir -p data/downloads && \
    adduser --disabled-password --gecos "" appuser && \
    chown -R appuser:appuser /app

USER appuser

EXPOSE 8899

# Start with Gunicorn + Eventlet
# We use shell form here to allow variable expansion of $PORT
CMD gunicorn --worker-class eventlet -w 1 --bind 0.0.0.0:$PORT app:app