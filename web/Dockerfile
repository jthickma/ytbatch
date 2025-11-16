FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends jq && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY app.py /app/app.py

RUN pip install flask flask-socketio python-socketio eventlet

CMD ["python3", "app.py"]