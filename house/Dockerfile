# house/Dockerfile
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN mkdir -p /config /config/tmp /data
VOLUME ["/config", "/data"]

# Needed because requirements.txt installs git+https://...
RUN apt-get update && apt-get install -y --no-install-recommends \
      git \
      ca-certificates \
      build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY house/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
    && pip install --no-cache-dir "helics[cli]"

COPY house/ /app/house/

CMD ["helics", "run", "--path=/config/tmp/house_runner.json", "--no-log-files"]
