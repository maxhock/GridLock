FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Create config mount point
RUN mkdir /config
VOLUME ["/config"]

# Create data mount point
RUN mkdir /data
VOLUME ["/data"]

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the processing script
COPY process_log.py .

# Make script executable
RUN chmod +x process_log.py

# Default command: run via HELICS runner
CMD ["helics", "run", "--path=/config/tmp/logger_runner.json", "--no-log-files"]
