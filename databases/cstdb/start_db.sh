#!/bin/bash
# Start CST persistent data stores

source $CST_ROOT/cosim.env
docker compose -f $STACK_DIR/docker-compose.yaml up -d --remove-orphans
