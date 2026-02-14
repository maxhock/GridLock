FROM ghcr.io/astral-sh/uv:python3.12-trixie-slim

WORKDIR /workspaces/infdb

# COPY requirements into the container
COPY pyproject.toml pyproject.toml

# Copy the source code into the container
COPY src src
COPY main.py main.py

# Volumes for configs, meta_store, and experiment config are mounted at runtime:
#   - ./configs:/workspaces/infdb/configs     (InfDB connection config)
#   - ../meta_store:/workspaces/infdb/meta_store  (CST metadata store)
#   - ../config:/config                            (experiment YAML)

# Run main.py with uv and environment defined in pyproject.toml
CMD ["uv", "run", "python", "main.py"]