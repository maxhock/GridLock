FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Create config mount point
RUN mkdir /config
VOLUME ["/config"]

# Create data mount point
RUN mkdir /data
VOLUME ["/data"]

# Install HELICS CLI
RUN pip install --no-cache-dir helics[cli]

# Default command: run helics_app source (configured via runner.json)
CMD ["helics", "run", "--path=/config/tmp/transformer_runner.json", "--no-log-files"]
