FROM python:3.11-slim
WORKDIR /app
# Install HELICS CLI only
RUN pip install --no-cache-dir helics[cli]
# Create data mount point
RUN mkdir -p /data
VOLUME ["/data"]
# Default command: run a placeholder (will be overridden by compose)
CMD ["helics_recorder"]