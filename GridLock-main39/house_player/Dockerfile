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

# Default command: launch all house_player federates using helics run with config from mount
CMD ["helics", "run", "--path=/config/tmp/house_player_runner.json", "--no-log-files"]