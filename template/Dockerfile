FROM python:3.11-slim AS base

# Set working directory
WORKDIR /app

# Create config mount point
RUN mkdir /config
VOLUME ["/config"]

# Create data mount point
RUN mkdir /data
VOLUME ["/data"]

# Install HELICS CLI and pandapower
RUN pip install --no-cache-dir helics[cli] 
# Copy the grid simulation script and runner.json into the container
COPY main.py .
COPY runner.json .


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
# Default command: run via HELICS runner
CMD ["helics", "run", "--path=runner.json"]

# --- Usage ---
# To run tests during build:
# docker build --target test --build-arg RUN_TESTS=true -t grid-test .
# To build production image:
# docker build --target final -t grid-prod .