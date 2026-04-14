# controller/Dockerfile
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN mkdir -p /config /config/tmp /data
VOLUME ["/config", "/data"]

RUN apt-get update && apt-get install -y --no-install-recommends \
      git \
      ca-certificates \
      build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY house/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
    && pip install --no-cache-dir "helics[cli]"

COPY controller/ /app/controller/
COPY house/ /app/house/
COPY tools/ /app/tools/

CMD ["helics", "run", "--path=/config/tmp/controller_runner.json", "--no-log-files"]
