FROM python:3.11 AS base
# Set working directory
WORKDIR /app

# Create config mount point
RUN mkdir /config
VOLUME ["/config"]

# Create data mount point
RUN mkdir /data
VOLUME ["/data"]


# Install dependencies from requirements.txt
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# --- Test stage ---
FROM base AS test
# Install test dependencies (pytest is already in environment_HPC.yml)
RUN pip install --no-cache-dir pytest-mock
# Copy test files
COPY test_main.py .
# Always run tests in this stage
RUN pytest --maxfail=1 --disable-warnings -v

# --- Final image ---
FROM base AS final
# Default command: run main.py
CMD ["python", "main.py"]

# --- Usage ---
# To run tests during build:
# docker build --target test -t data-generation-test .
# To build production image:
# docker build --target final -t data-generation .