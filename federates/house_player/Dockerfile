# house_player/Dockerfile
FROM python:3.11-slim

# 1. Python-specific optimizations
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Set working directory
WORKDIR /app

# Create mount points and mark them as volumes
RUN mkdir -p /data /app/meta_store

# Install dependencies
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# Copy application code
COPY main.py .
COPY src/ src/

# Entry point - scenario, federate_name and timeseries args provided by docker-compose command
CMD ["python", "main.py"]
