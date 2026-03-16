FROM python:3.11-slim AS base

# 1. Python-specific optimizations
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
    
# Set working directory
WORKDIR /app

# Create mount points and mark them as volumes
RUN mkdir -p /data /app/meta_store

# Install HELICS CLI
RUN pip install --no-cache-dir helics[cli] 
# Copy the template script and runner.json into the container
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
# Default command: run via HELICS runner
CMD ["helics", "run", "--path=runner.json"]

# --- Usage ---
# To run tests during build:
# docker build --target test --build-arg RUN_TESTS=true -t grid-test .
# To build production image:
# docker build --target final -t grid-prod .