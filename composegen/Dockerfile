FROM python:3.11-slim AS base

# Set working directory
WORKDIR /app

# Create mount points and mark them as volumes
RUN mkdir -p /config /data /app/generated

# Install omegaconf, pyyaml, pandas, and openpyxl for config parsing and Excel reading
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy all files from build context (respects .dockerignore)
COPY . .

# --- Test stage ---
FROM base AS test
RUN pip install --no-cache-dir pytest pytest-mock
RUN pytest --maxfail=1 --disable-warnings -v

# --- Final image ---
FROM base AS final
# base's blanket `COPY . .` (needed so the test stage can see test_main.py)
# also picks it up here; drop it so it doesn't ship in the production image.
RUN rm -f test_main.py
# Inform users: /config must be mounted at runtime
ENTRYPOINT ["python", "main.py"]

# --- Usage ---
# To run tests during build:
# docker build --target test -t composegen-test .
# To build production image:
# docker build --target final -t composegen-prod .
