#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREFLIGHT_ENV_FILE="${PREFLIGHT_ENV_FILE:-$SCRIPT_DIR/config/preflight.env}"
PREFLIGHT_COMPOSE_FILE="$SCRIPT_DIR/docker-compose.preflight.yaml"
GENERATED_DIR="$SCRIPT_DIR/generated"
COMPOSE_FILE="$GENERATED_DIR/docker-compose.yaml"
DEFAULT_EXPERIMENT_FILE="$SCRIPT_DIR/config/experiment.yaml"

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
}

preflight_compose() {
  docker compose --env-file "$PREFLIGHT_ENV_FILE" -f "$PREFLIGHT_COMPOSE_FILE" "$@"
}

resolve_experiment_path() {
  local requested_path="${1:-$DEFAULT_EXPERIMENT_FILE}"

  if [[ "$requested_path" != /* ]]; then
    requested_path="$PWD/$requested_path"
  fi

  if [ ! -f "$requested_path" ]; then
    echo "Missing experiment config: $requested_path" >&2
    exit 1
  fi

  EXPERIMENT_FILE="$(realpath "$requested_path")"
  export EXPERIMENT_FILE
}

prepare_runtime_experiment() {
  mkdir -p "$GENERATED_DIR"
  local experiment_basename
  experiment_basename="$(basename "$EXPERIMENT_FILE")"
  RUNTIME_EXPERIMENT_FILE="$GENERATED_DIR/$experiment_basename"
  cp "$EXPERIMENT_FILE" "$RUNTIME_EXPERIMENT_FILE"
  export GRIDLOCK_EXPERIMENT_PATH_IN_CONTAINER="/app/generated/$experiment_basename"
}

cleanup() {
  preflight_compose down --remove-orphans >/dev/null 2>&1 || true
}

main() {
  resolve_experiment_path "${1:-}"

  print_stage "Stage 1: load environment"
  load_env
  prepare_runtime_experiment
  echo "Using experiment config: $EXPERIMENT_FILE"

  trap cleanup EXIT

  print_stage "Stage 2: run preflight compose"
  preflight_compose up -d --wait database mongodb
  preflight_compose up --build --quiet-build --abort-on-container-exit --exit-code-from composegen --no-deps infdb composegen

  print_stage "Stage 3: run experiment compose"
  docker compose -f "$COMPOSE_FILE" up --build --quiet-build --remove-orphans --abort-on-container-exit
}

main "$@"
