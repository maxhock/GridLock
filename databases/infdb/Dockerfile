FROM ghcr.io/astral-sh/uv:python3.12-trixie-slim

WORKDIR /app

# COPY requirements and lockfile into the container
COPY pyproject.toml pyproject.toml
COPY uv.lock uv.lock

# Copy the source code into the container
COPY src src
COPY main.py main.py

# Run main.py with uv and environment defined in pyproject.toml
CMD ["uv", "run", "python", "main.py"]