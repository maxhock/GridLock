# house/Dockerfile
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app

WORKDIR /app

RUN mkdir -p /config /config/tmp /data


# Needed because requirements.txt installs git+https://...
RUN apt-get update && apt-get install -y --no-install-recommends \
      git \
      ca-certificates \
      build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY federates/house/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
    && pip install --no-cache-dir "helics[cli]"

COPY federates/house/ /app/house/
COPY tools/ /app/tools/

# --- Test stage ---
FROM base AS test
RUN pip install --no-cache-dir pytest pytest-mock
RUN pytest house/test_main.py --maxfail=1 --disable-warnings -v

# --- Final image ---
FROM base AS final
# base's directory-wide `COPY federates/house/ /app/house/` (needed so the
# test stage can see test_main.py) also picks it up here; drop it so it
# doesn't ship in the production image.
RUN rm -f house/test_main.py
CMD ["helics", "run", "--path=/config/tmp/house_runner.json", "--no-log-files"]

# --- Usage ---
# To run tests during build:
# docker build --target test -f federates/house/Dockerfile -t house-test .
# To build production image:
# docker build --target final -f federates/house/Dockerfile -t house-prod .
