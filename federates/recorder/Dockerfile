FROM python:3.11-slim

# 1. Python-specific optimizations
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
    
# Set working directory
WORKDIR /app

# Create mount points and mark them as volumes
RUN mkdir -p /data /app/meta_store

# Install HELICS CLI only
RUN pip install --no-cache-dir helics[cli]

# Default command: launch the recorder using helics run with config from mount
CMD ["helics", "run", "--path=/config/tmp/recorder_runner.json", "--no-log-files"]