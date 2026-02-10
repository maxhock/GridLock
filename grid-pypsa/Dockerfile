FROM python:3.11-slim AS base

# Set working directory
WORKDIR /app

# Create config mount point
RUN mkdir /config
VOLUME ["/config"]

# Create data mount point
RUN mkdir /data
VOLUME ["/data"]

# Install dependencies from requirements.txt
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the grid simulation script into the container
COPY main.py .
COPY utils.py .

# --- Final image ---
FROM base AS final
# Default command: run via HELICS runner with config from mount
CMD ["helics", "run", "--path=/config/tmp/grid_runner.json", "--no-log-files"]
