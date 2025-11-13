FROM python:3.12 AS base
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

# Copy the 1.grid_sampling folder
COPY 1.grid_sampling/gridreadout ./gridreadout/

# Copy the 2.demand_allocation folder
COPY 2.demand_allocation/gridalloc ./gridalloc/

# Symlink gridreadout/results to gridalloc/data/grids
RUN rm -rf /app/gridalloc/data/grids
RUN ln -s /app/gridreadout/results /app/gridalloc/data/grids

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
CMD ["python", "main.py", "1"]

# --- Usage ---
# To run tests during build:
# docker build --target test -t data-generation-test .
# To build production image:
# docker build --target final -t data-generation .