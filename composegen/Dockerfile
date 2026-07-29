FROM python:3.11-slim AS base

# Set working directory
WORKDIR /app

# Create mount points and mark them as volumes
RUN mkdir -p /config /data /app/generated

# Install omegaconf, pyyaml, pandas, and openpyxl for config parsing and Excel reading
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy all files from build context (respects .dockerignore)
COPY . .

# --- Final image ---
FROM base AS final
# Inform users: /config must be mounted at runtime
ENTRYPOINT ["python", "main.py"]
