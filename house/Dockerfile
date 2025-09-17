FROM python:3.11-slim
WORKDIR /app
# Install HELICS CLI
RUN pip install --no-cache-dir helics[cli]
# Create config mount point
RUN mkdir /config
VOLUME ["/config"]
# Default command: launch the house federate as a HELICS player using the CSV from the /config mount
CMD ["helics_player", "/config/sample_house.csv", "--broker=broker"]