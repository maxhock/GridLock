FROM python:3.11-slim

# 1. Python-specific optimizations
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Set working directory
WORKDIR /app

# Create mount points and mark them as volumes
RUN mkdir -p /data /app/meta_store

# Install dependencies from requirements.txt
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY main.py .

# Entry point - scenario and federate_name args provided by docker-compose command
CMD ["python", "main.py"]
