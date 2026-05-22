FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Create config mount point
RUN mkdir /config


# Create data mount point
RUN mkdir /data


# Install HELICS CLI only
RUN pip install --no-cache-dir helics[cli]

# Default command: launch the recorder using helics run with config from mount
CMD ["helics", "run", "--path=/config/tmp/recorder_runner.json", "--no-log-files"]