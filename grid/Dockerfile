FROM python:3.11-slim AS base

# Set working directory
WORKDIR /app

# Create config and data mount points
RUN mkdir -p /config /data
VOLUME ["/config", "/data"]

# Install HELICS CLI and pandapower
RUN pip install --no-cache-dir helics[cli] pandapower openpyxl

# Copy the grid simulation script into the container
COPY main.py .


# --- Test stage ---
FROM base AS test
# Install test dependencies
RUN pip install --no-cache-dir pytest pytest-mock
# Copy test files
COPY test_main.py .
# Always run tests in this stage
RUN pytest --maxfail=1 --disable-warnings -v

# --- Final image ---
FROM base AS final
# Default command: run helics_runner with the grid config
# The config file will be provided by docker-compose
CMD ["helics_runner", "/config/tmp/helics_runner_grid.json"]

# --- Usage ---
# To run tests during build:
# docker build --target test -t grid-test .
# To build production image:
# docker build --target final -t grid-prod .