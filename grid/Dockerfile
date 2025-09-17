FROM python:3.11-slim
WORKDIR /app
# Install HELICS CLI and pandapower
RUN pip install --no-cache-dir helics[cli] pandapower
# Create config mount point
RUN mkdir /config
VOLUME ["/config"]
# Copy the grid simulation script into the container
COPY GridSimulation.py .
# Default command: run the grid simulation Python script
CMD ["python", "GridSimulation.py"]