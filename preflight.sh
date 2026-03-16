#!/bin/bash
set -e

# Preflight: runs infdb data setup and composegen to prepare configs and data.
# Used by run.sh and as devcontainer initializeCommand.
# Requires Docker CLI on the machine where it runs.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Source project environment
if [ -f "$SCRIPT_DIR/cosim.env" ]; then
  source "$SCRIPT_DIR/cosim.env"
fi

INFDB_ENV_FILE="${INFDB_ENV_FILE:-$(pwd)/databases/infdb/.env}"

resolve_workspace_bind_root() {
  # Docker bind mounts are resolved by the Docker daemon host. Resolution order:
  # 1) Use HOST_WORKSPACE_FOLDER if explicitly provided.
  # 2) If in a container, inspect this container's mounts and take the host source
  #    path that backs SCRIPT_DIR.
  # 3) Fallback to current directory for normal host-side execution.
  if [ -n "${HOST_WORKSPACE_FOLDER:-}" ]; then
    echo "$HOST_WORKSPACE_FOLDER"
    return
  fi

  if [ -f "/.dockerenv" ] && command -v docker >/dev/null 2>&1; then
    local detected
    detected="$(docker inspect --format "{{range .Mounts}}{{if eq .Destination \"$SCRIPT_DIR\"}}{{.Source}}{{end}}{{end}}" "$HOSTNAME" 2>/dev/null || true)"
    if [ -n "$detected" ]; then
      echo "$detected"
      return
    fi
  fi

  echo "$(pwd)"
}

WORKSPACE_BIND_ROOT="$(resolve_workspace_bind_root)"

# Use dedicated preflight DB host vars so inherited service env values
# (e.g. MONGO_HOST=mongodb://localhost) do not break one-off docker runs.
PREFLIGHT_MONGO_HOST="${PREFLIGHT_MONGO_HOST:-mongodb://host.docker.internal}"
PREFLIGHT_MONGO_PORT="${PREFLIGHT_MONGO_PORT:-27017}"

if [ ! -f "$INFDB_ENV_FILE" ]; then
  echo "Error: INFDB env file not found: $INFDB_ENV_FILE"
  exit 1
fi

# ---------------------------------------------------------------------------
# Build helpers
#
# Images are only (re)built when:
#   a) the image does not yet exist locally, OR
#   b) FORCE_REBUILD=1 is set in the environment.
#
# This means changes to experiment-LV.yml never trigger a rebuild
# (the config is always mounted, never baked into the image).
# Set FORCE_REBUILD=1 when you have changed code inside composegen/
# or databases/infdb/ and need a fresh image.
# ---------------------------------------------------------------------------

_build_if_needed() {
  local tag="$1"
  local dockerfile="$2"
  local context="$3"
  if [ "${FORCE_REBUILD:-0}" = "1" ] || ! docker image inspect "$tag" >/dev/null 2>&1; then
    echo "  Building image '$tag'..."
    docker build -f "$dockerfile" -t "$tag" "$context"
  else
    echo "  Image '$tag' exists – skipping build (set FORCE_REBUILD=1 to rebuild)."
  fi
}

# Step 1: Run infdb data setup to resolve grids and write to CST metadata store
echo "=== Preflight Step 1: infdb data setup ==="
_build_if_needed infdb ./databases/infdb/Dockerfile databases/infdb
docker run --rm \
  -v "$WORKSPACE_BIND_ROOT/databases/infdb/configs:/app/configs:ro" \
  -v "$WORKSPACE_BIND_ROOT/meta_store:/app/meta_store" \
  -v "$WORKSPACE_BIND_ROOT/config:/config:ro" \
  --env-file "$INFDB_ENV_FILE" \
  -e MONGO_HOST="$PREFLIGHT_MONGO_HOST" \
  -e MONGO_PORT="$PREFLIGHT_MONGO_PORT" \
  --add-host=host.docker.internal:host-gateway \
  infdb

# Step 2: Build and run composegen (reads manifest.json from meta_store)
echo "=== Preflight Step 2: composegen ==="
_build_if_needed composegen ./composegen/Dockerfile composegen
docker run --rm \
  -v "$WORKSPACE_BIND_ROOT/config:/config" \
  -v "$WORKSPACE_BIND_ROOT/data:/data" \
  -v "$WORKSPACE_BIND_ROOT/meta_store:/app/meta_store" \
  -e MONGO_HOST="$PREFLIGHT_MONGO_HOST" \
  -e MONGO_PORT="$PREFLIGHT_MONGO_PORT" \
  -e RUNTIME_CST_HOST="${CST_HOST:-localhost}" \
  -e RUNTIME_POSTGRES_HOST="${POSTGRES_HOST:-${CST_HOST:-localhost}}" \
  -e RUNTIME_POSTGRES_PORT="${POSTGRES_PORT:-5432}" \
  -e RUNTIME_MONGO_HOST="${MONGO_HOST:-mongodb://${CST_HOST:-localhost}}" \
  -e RUNTIME_MONGO_PORT="${MONGO_PORT:-27017}" \
  --add-host=host.docker.internal:host-gateway \
  composegen

echo "=== Preflight complete ==="
