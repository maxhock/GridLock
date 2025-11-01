FROM python:3.11-slim

# Set working directory
WORKDIR /workspace

# Create config and data mount points
RUN mkdir -p /config /data
VOLUME ["/config", "/data"]

# Install HELICS with CLI tools (includes helics_runner, helics_player, helics_recorder)
# Also install dependencies for grid federate (pandapower, openpyxl)
RUN pip install --no-cache-dir helics[cli] pandapower openpyxl

# Create directory structure for federates
RUN mkdir -p /workspace/grid

# Copy grid federate script
COPY grid_main.py /workspace/grid/main.py

# Note: helics_player and helics_recorder are provided by helics[cli]
# House federate uses helics_player (no custom script needed)
# Recorder federate uses helics_recorder (no custom script needed)

# Default command: run helics_runner with generated config
# The config file path will be mounted from the host
CMD ["helics_runner", "/config/tmp/helics_runner.json"]
