#!/bin/bash
# Stop CST persistent data stores
# Add '--volumes' to also remove persistent data volumes

source $CST_ROOT/cosim.env
docker compose -f $STACK_DIR/docker-compose.yaml down --remove-orphans
