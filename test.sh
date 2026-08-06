#!/bin/bash
set -uo pipefail

cd "$(dirname "$0")"

# Each component's `test` Docker stage runs pytest during the build itself
# (see e.g. federates/grid/Dockerfile), so "the build succeeded" already means
# "the tests passed" - no compose/runtime plumbing needed here, just a build
# per component. Failures don't stop the loop, so one broken component
# doesn't hide failures in the others.

NAMES=()
RESULTS=()

run_component() {
  local name="$1" context="$2" dockerfile="$3"
  echo "=== ${name} ==="
  if docker build --target test -f "$dockerfile" -t "${name}-test" "$context"; then
    RESULTS+=("PASS")
  else
    RESULTS+=("FAIL")
  fi
  NAMES+=("$name")
  echo ""
}

run_component grid federates/grid federates/grid/Dockerfile
run_component house_player federates/house_player federates/house_player/Dockerfile
run_component house . federates/house/Dockerfile
run_component composegen composegen composegen/Dockerfile
run_component infdb databases/infdb databases/infdb/Dockerfile
# No experiment routes to the template, so nothing else would notice it rotting
# against a CST release. Building it here is what keeps `cp -r federates/template`
# a working starting point rather than one that fails on its first import.
run_component template federates/template federates/template/Dockerfile

echo "=== Summary ==="
FAILED=0
for i in "${!NAMES[@]}"; do
  printf '%-15s %s\n' "${NAMES[$i]}" "${RESULTS[$i]}"
  [[ "${RESULTS[$i]}" == "FAIL" ]] && FAILED=1
done

exit "$FAILED"
