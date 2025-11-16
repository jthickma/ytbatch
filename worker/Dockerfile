FROM python:3.11-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg jq curl && \
    rm -rf /var/lib/apt/lists/*

RUN pip install yt-dlp watchdog requests

WORKDIR /app
COPY worker.py /app/worker.py
COPY download_batch.sh /app/download_batch.sh
RUN chmod +x /app/download_batch.sh

CMD ["python3", "/app/worker.py"]