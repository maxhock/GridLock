# ochre-house/Dockerfile
FROM python:3.11-slim AS base

# 1. Python-specific optimizations
ENV MPLBACKEND=Agg \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Set working directory
WORKDIR /app

# Create mount points and mark them as volumes
RUN mkdir -p /data /app/meta_store

# OCHRE's package __init__ imports its own tkinter GUI module unconditionally, so
# the shared library has to exist even though nothing here opens a window.
RUN apt-get update && apt-get install -y --no-install-recommends libtk8.6 \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# Copy application code
COPY main.py .

# --- Test stage ---
FROM base AS test
RUN pip install --no-cache-dir pytest pytest-mock
COPY test_main.py .
RUN pytest --maxfail=1 --disable-warnings -v

# --- Final image ---
FROM base AS final
# Entry point - scenario and federate_name are appended by the generated
# compose file, which overrides nothing else about this command.
CMD ["python", "main.py"]

# --- Usage ---
# To run tests during build:
# docker build --target test -t ochre-house-test .
# To build production image:
# docker build --target final -t ochre-house-prod .
