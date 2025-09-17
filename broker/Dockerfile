FROM python:3.11-slim
WORKDIR /app
# Install HELICS CLI
RUN pip install --no-cache-dir helics[cli]
# Create config mount point
RUN mkdir /config
VOLUME ["/config"]
# Default command: launch the broker with required arguments
CMD ["helics_broker", "--federates=4", "--name=broker", "--ipv4"]