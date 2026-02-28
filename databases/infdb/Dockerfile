FROM ghcr.io/astral-sh/uv:python3.12-trixie-slim

WORKDIR /app

# COPY requirements and lockfile into the container
COPY pyproject.toml pyproject.toml
COPY uv.lock uv.lock

# Pre-install the dependencies to cache them in a Docker layer
RUN uv sync --frozen --no-install-project

# Copy the source code into the container
COPY src src
COPY main.py main.py

# Install the project itself
RUN uv sync --frozen

# Run main.py with uv
CMD ["uv", "run", "python", "main.py"]