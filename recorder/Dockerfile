FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Create config mount point
RUN mkdir /config
VOLUME ["/config"]

# Create data mount point
RUN mkdir /data
VOLUME ["/data"]

# Install HELICS CLI only
RUN pip install --no-cache-dir helics[cli]
# Default command: run a placeholder (will be overridden by compose)
CMD ["helics_recorder"]