FROM python:3.11-slim AS base

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

# Copy the forecasting script into the container
COPY main.py forecasting.py ./



# --- Final image ---
FROM base AS final
# Default command: run the grid simulation Python script
#CMD ["python", "main.py"]
CMD ["helics", "run", "--path=/config/tmp/forecasting_runner.json", "--no-log-files"]
