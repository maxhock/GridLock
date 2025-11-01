FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Create config and data mount points
RUN mkdir -p /config /data
VOLUME ["/config", "/data"]

# Install HELICS CLI (includes helics_player and helics_runner)
RUN pip install --no-cache-dir helics[cli]

# Default command: run helics_runner with the house config
# The config file will be provided by docker-compose
CMD ["helics_runner", "/config/tmp/helics_runner_house.json"]
