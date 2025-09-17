"""
This script generates a docker-compose.yaml file based on the experiment parameters in config/master_config.yaml (Omegaconf).

- Reads broker, grid, and house config from master_config.yaml
- Dynamically creates the correct number of house services
- Writes docker-compose.yaml in the config/ folder
"""
import os
from omegaconf import OmegaConf
import yaml

CONFIG_PATH = os.path.join("/config", "experiment_config.yaml")
COMPOSE_PATH = os.path.join("/config", "tmp", "docker-compose.yaml")
CONFIG_MOUNT = "${PWD}/config"

cfg = OmegaConf.load(CONFIG_PATH)
broker_cfg = cfg.experiment.broker
grid_cfg = cfg.experiment.grid
house_cfg = cfg.experiment.house
num_houses = cfg.experiment.num_houses

# Derive broker name for use in commands
broker_name = broker_cfg.name

services = {}

# Calculate total number of federates: houses + grid + recorder
num_federates = num_houses + 1 + 1  # grid + recorder

# Broker service
docker_broker = {
    "build": "${PWD}/broker",
    "container_name": broker_name,
    "volumes": [f"{CONFIG_MOUNT}:/config"],
    "networks": ["helics-net"],
    "command": [
        "helics_broker",
        f"--federates={num_federates}",
        f"--name={broker_name}",
        "--ipv4"
    ]
}
services[broker_name] = docker_broker

# Grid service
docker_grid = {
    "build": "${PWD}/grid",
    "container_name": "grid",
    "volumes": [f"{CONFIG_MOUNT}:/config"],
    "networks": ["helics-net"],
    "command": [
        "python",
        grid_cfg.script
    ]
}
services["grid"] = docker_grid

# House services
for i in range(1, num_houses + 1):
    house_name = f"house_{i}"
    docker_house = {
        "build": "${PWD}/house",
        "container_name": house_name,
        "volumes": [f"{CONFIG_MOUNT}:/config"],
        "networks": ["helics-net"],
        "command": [
            "helics_player",
            house_cfg.csv,
            f"--broker={broker_name}",
            "--name", house_name,
            "--local"
        ]
    }
    services[house_name] = docker_house


# Recorder service
docker_recorder = {
    "build": "${PWD}/recorder",
    "container_name": "recorder",
    "volumes": ["${PWD}/data:/data"],
    "networks": ["helics-net"],
    "command": [
        "helics_recorder",
        "--capture=Grid",
        "--output=/data/output/grid.log",
        "--broker=broker",
        "--local"
    ]
}
services["recorder"] = docker_recorder

# Full compose dictionary
compose_dict = {
    "services": services,
    "networks": {
        "helics-net": {
            "driver": "bridge"
        }
    }
}


# Write the compose file to config/
with open(COMPOSE_PATH, "w") as f:
    yaml.dump(compose_dict, f, sort_keys=False)

print(f"Generated {COMPOSE_PATH} with {len(services)} services.")
