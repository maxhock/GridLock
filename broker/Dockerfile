FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Create config mount point
RUN mkdir /config
VOLUME ["/config"]

# Create data mount point
RUN mkdir /data
VOLUME ["/data"]

# Install dependencies: HELICS CLI
RUN pip install --no-cache-dir helics[cli]

# Default command: launch the broker with required arguments
CMD ["helics_broker", "--federates=4", "--name=broker", "--ipv4"]