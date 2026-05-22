FROM python:3.11-slim AS base

# Set working directory
WORKDIR /app

# Create config mount point
RUN mkdir /config


# Create data mount point
RUN mkdir /data


# Install dependencies from requirements.txt
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the forecasting script into the container
COPY main.py .
COPY forecasting.py .



# TODO not needed for now
# --- Test stage ---
# FROM base AS test
# # Install test dependencies
# RUN pip install --no-cache-dir pytest pytest-mock
# # Copy test files
# COPY test_main.py .
# # Always run tests in this stage
# RUN pytest --maxfail=1 --disable-warnings -v

# --- Final image ---
FROM base AS final
# Default command: run the grid simulation Python script
#CMD ["python", "main.py"]
CMD ["helics", "run", "--path=/config/tmp/forecasting_runner.json", "--no-log-files"]

# --- Usage ---
# To run tests during build:
# docker build --target test --build-arg RUN_TESTS=true -t grid-test .
# To build production image:
# docker build --target final -t grid-prod .