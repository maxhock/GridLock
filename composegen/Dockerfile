FROM python:3.11-slim AS base

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

# --- Test stage ---
FROM base AS test
# Install test dependencies
RUN pip install pytest pytest-mock
# Copy test files
COPY test_main.py .
# Always run tests in this stage
RUN pytest --maxfail=1 --disable-warnings -v

# --- Final image ---
FROM base AS final
# Copy only production files (main.py)
COPY --from=base /app/main.py .
# Inform users: /config must be mounted at runtime
ENTRYPOINT ["python", "main.py"]

# --- Usage ---
# To run tests during build:
# docker build --target test -t composegen-test .
# To build production image:
# docker build --target final -t composegen-prod .