FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Create config mount point
RUN mkdir /config
VOLUME ["/config"]

# Create data mount point
RUN mkdir /data
VOLUME ["/data"]

# Install HELICS CLI and pandapower
RUN pip install --no-cache-dir helics[cli] pandapower

# Copy the grid simulation script into the container
COPY GridSimulation.py .
# Default command: run the grid simulation Python script
CMD ["python", "GridSimulation.py"]