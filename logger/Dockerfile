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
COPY process_helics_tsv.py .

# Make script executable
RUN chmod +x process_helics_tsv.py

# Run helics recorder, then process the output
CMD helics run --path=/config/tmp/logger_runner.json --no-log-files && python process_helics_tsv.py --input=/app/logger_raw.log --output=${LOGGER_OUTPUT_FILE:-/data/output/federation_processed.csv}
