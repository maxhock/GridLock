FROM python:3.11-slim AS base

# Set working directory
WORKDIR /app

# Create mount points and mark them as volumes
RUN mkdir -p /data /app/meta_store
VOLUME ["/data", "/app/meta_store"]

# Install dependencies from requirements.txt
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY main.py .


# --- Test stage ---
FROM base AS test
# Install test dependencies
RUN pip install --no-cache-dir pytest pytest-mock pandas
# Copy test files
COPY test_main.py .
# Always run tests in this stage
RUN pytest --maxfail=1 --disable-warnings -v

# --- Final image ---
FROM base AS final
# Entry point - scenario and federate_name args provided by docker-compose command
CMD ["python", "main.py"]

# --- Usage ---
# To run tests during build:
# docker build --target test -t grid-test .
# To build production image:
# docker build --target final -t grid-prod .