FROM ghcr.io/astral-sh/uv:python3.12-trixie-slim AS base

WORKDIR /app

ENV UV_LINK_MODE=copy
ENV PATH="/app/.venv/bin:$PATH"

# COPY requirements and lockfile into the container
COPY pyproject.toml pyproject.toml
COPY uv.lock uv.lock

# Pre-install the dependencies to cache them in a Docker layer
RUN uv sync --frozen --no-install-project

# Copy the source code into the container
COPY src src
COPY main.py main.py

# The InfDB library writes its log into the working directory. This container
# runs as the invoking host user (so that anything it leaves in generated/ is
# not root-owned), which is not the root that owns /app - hence the sticky
# world-writable bit: new files such as infdb.log can be created, but the
# application files already here still cannot be modified by that user.
RUN chmod 1777 /app

# --- Test stage ---
FROM base AS test
RUN uv pip install pytest pytest-mock
COPY test_main.py .
RUN pytest --maxfail=1 --disable-warnings -v

# --- Final image ---
FROM base AS final
# Run the app from the pre-built virtual environment
CMD ["python", "main.py"]

# --- Usage ---
# To run tests during build:
# docker build --target test -t infdb-test .
# To build production image:
# docker build --target final -t infdb-prod .