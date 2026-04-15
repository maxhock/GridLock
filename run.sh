#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREFLIGHT_ENV_FILE="${PREFLIGHT_ENV_FILE:-$SCRIPT_DIR/config/preflight.env}"
PREFLIGHT_COMPOSE_FILE="$SCRIPT_DIR/docker-compose.preflight.yaml"
GENERATED_DIR="$SCRIPT_DIR/generated"
COMPOSE_FILE="$GENERATED_DIR/docker-compose.yaml"

print_stage() {
  echo "=== $1 ==="
}

load_env() {
  if [ ! -f "$PREFLIGHT_ENV_FILE" ]; then
    echo "Missing env file: $PREFLIGHT_ENV_FILE" >&2
    exit 1
  fi

  set -a
  source "$PREFLIGHT_ENV_FILE"
  set +a

  export PREFLIGHT_ENV_FILE
  export INFDB_ENV_FILE="${INFDB_ENV_FILE:-$PREFLIGHT_ENV_FILE}"
  export HOST_UID="${HOST_UID:-$(id -u)}"
  export HOST_GID="${HOST_GID:-$(id -g)}"
  export CST_DOCKER_SUBNET="${CST_DOCKER_SUBNET:-10.251.0.0/16}"
  export CST_DOCKER_GATEWAY="${CST_DOCKER_GATEWAY:-10.251.0.1}"
  export CST_DOCKER_IP_PREFIX="${CST_DOCKER_IP_PREFIX:-10.251.0}"
}

preflight_compose() {
  docker compose --env-file "$PREFLIGHT_ENV_FILE" -f "$PREFLIGHT_COMPOSE_FILE" "$@"
}

cleanup() {
  preflight_compose down --remove-orphans >/dev/null 2>&1 || true
}

main() {
  print_stage "Stage 1: load environment"
  load_env

  trap cleanup EXIT

  print_stage "Stage 2: run preflight compose"
  preflight_compose up -d --wait database mongodb
  
  # Run composegen to generate federation config and docker-compose.yaml
  preflight_compose build --quiet composegen
  preflight_compose run --rm --no-deps composegen
  
  # Keep databases running - DO NOT CLEANUP YET
  print_stage "Stage 2b: cleanup generated compose file"
  python3 "$SCRIPT_DIR/cleanup_compose.py"

  # Now run experiment compose - databases are still running in preflight network
  print_stage "Stage 3: run experiment compose"
  docker compose -f "$COMPOSE_FILE" --project-name "generated" up --build --quiet-build --remove-orphans --abort-on-container-exit
  
  # Cleanup happens on exit via trap
}

main "$@"
