FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Create config mount point
RUN mkdir /config
VOLUME ["/config"]

# Create data mount point
RUN mkdir /data
VOLUME ["/data"]

# Install omegaconf and pyyaml for config parsing
RUN pip install omegaconf pyyaml

# Copy the main generator script
COPY main.py .
# Inform users: /config must be mounted at runtime
ENTRYPOINT ["python", "main.py"]