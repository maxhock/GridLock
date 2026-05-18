# grid/Dockerfile
FROM python:3.11-slim AS base

WORKDIR /app

RUN mkdir -p /config /config/tmp /data


# Install dependencies from grid/requirements.txt (root context!)
COPY grid/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# Copy grid code (root context!)
COPY grid/main.py /app/main.py

# --- Test stage ---
FROM base AS test
RUN pip install --no-cache-dir pytest pytest-mock
COPY grid/test_main.py /app/test_main.py
RUN pytest --maxfail=1 --disable-warnings -v

# --- Final image ---
FROM base AS final
CMD ["helics", "run", "--path=/config/tmp/grid_runner.json", "--no-log-files"]