#!/usr/bin/env python3
"""
Generate docker-compose.yml and runner.json files from experiment.yml using OmegaConf.

Generates:
- docker-compose.yml with 4 services (broker, grid, house, recorder)
- runner.json files for each federate class in config/tmp
"""

import os
from omegaconf import OmegaConf
import json
import pandas as pd


def create_docker_compose(conf, output_path):
    """
    Generate simplified docker-compose YAML with 4 services (broker, grid, house, recorder).
    No command overrides - federates use runner.json from /config mount.
    """
    fed_conf = OmegaConf.select(conf, "federates")

    # Compute num_nodes from Excel if grid_file is specified
    try:
        grid_file = fed_conf["grid"]["grid_file"]
    except Exception:
        grid_file = None

    if grid_file:
        excel_path = os.path.join("/data", "input", grid_file)
        if os.path.isfile(excel_path):
            try:
                df = pd.read_excel(excel_path, sheet_name="load", header=0)
                num_nodes = len(df)
                print(f"Detected {num_nodes} nodes from {grid_file} (load sheet).")
                conf["federates"]["grid"]["num_nodes"] = int(num_nodes)
            except Exception as e:
                print(f"WARNING: Could not read load sheet from {grid_file}: {e}")
        else:
            print(
                f"No valid grid_file found at {excel_path}; using num_nodes from config."
            )
    else:
        print("No grid_file specified in config; using num_nodes from config.")

    # Create simplified docker-compose with 4 services (no command overrides)
    compose_config = {
        "services": {
            "broker": {
                "container_name": "broker",
                "build": "${PWD}/broker",
                "volumes": ["${PWD}/config:/config", "${PWD}/data:/data"],
                "networks": ["helics-net"],
            },
            "grid": {
                "container_name": "grid",
                "build": "${PWD}/grid",
                "volumes": ["${PWD}/config:/config", "${PWD}/data:/data"],
                "networks": ["helics-net"],
                "depends_on": ["broker"],
            },
            "house": {
                "container_name": "house",
                "build": "${PWD}/house_player",
                "volumes": ["${PWD}/config:/config", "${PWD}/data:/data"],
                "networks": ["helics-net"],
                "depends_on": ["broker"],
            },
            "recorder": {
                "container_name": "recorder",
                "build": "${PWD}/recorder",
                "volumes": ["${PWD}/config:/config", "${PWD}/data:/data"],
                "networks": ["helics-net"],
                "depends_on": ["broker"],
            },
        },
        "networks": {"helics-net": {"driver": "bridge"}},
    }

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        OmegaConf.save(OmegaConf.create(compose_config), f, resolve=False)

    print(f"Generated {output_path} with {len(compose_config['services'])} services:")
    for service_name in compose_config["services"].keys():
        print(f"  - {service_name}")


def create_broker_runner(conf, output_path):
    """Generate broker runner.json file."""
    fed_conf = OmegaConf.select(conf, "federates")
    num_federates = (
        fed_conf["grid"]["num_nodes"] + 2
    )  # number of nodes in grid + recorder + houses

    runner = {
        "name": "broker_federation",
        "federates": [
            {
                "directory": ".",
                "exec": f"helics_broker --federates={num_federates} --name=broker --ipv4 --loglevel=debug --logfile=/data/output/broker.log",
                "host": "localhost",
                "name": "broker",
            }
        ],
    }

    with open(output_path, "w") as f:
        json.dump(runner, f, indent=2)
    print(f"Generated {output_path}")


def create_grid_runner(conf, output_path):
    """Generate grid runner.json file."""
    fed_conf = OmegaConf.select(conf, "federates")
    grid_file = fed_conf["grid"]["grid_file"]

    runner = {
        "name": "grid_federation",
        "federates": [
            {
                "directory": "/app",
                "exec": f"python main.py --broker=broker --grid_file={grid_file}",
                "host": "localhost",
                "name": "grid",
            }
        ],
    }

    with open(output_path, "w") as f:
        json.dump(runner, f, indent=2)
    print(f"Generated {output_path}")


def create_house_runner(conf, output_path):
    """
    Generate house runner.json file with node instances based on placement configuration.

    Supports:
    - Optional 'placement' list: explicit bus indices to place houses on
    - Optional 'fill_remaining' bool: if true, fill unassigned nodes with houses
    """
    fed_conf = OmegaConf.select(conf, "federates")
    num_nodes = fed_conf["grid"]["num_nodes"]
    house_conf = fed_conf["house"]
    input_file = house_conf["input_file"]

    # Get optional placement configuration
    placement = house_conf.get("placement", None)
    fill_remaining = house_conf.get("fill_remaining", False)

    # Initialize node assignment map (None = unassigned)
    node_map = [None] * num_nodes

    # Apply explicit placements
    if placement is not None:
        for node_idx in placement:
            if 0 <= node_idx < num_nodes:
                node_map[node_idx] = "house"
            else:
                print(
                    f"WARNING: placement index {node_idx} is out of range [0, {num_nodes-1}]"
                )

    # Fill remaining nodes if requested
    if fill_remaining:
        for i in range(num_nodes):
            if node_map[i] is None:
                node_map[i] = "house"

    # If no placement specified and fill_remaining is False, default to all nodes (backward compatibility)
    if placement is None and not fill_remaining:
        node_map = ["house"] * num_nodes

    # Validate that all nodes are assigned
    unassigned_nodes = [i for i, v in enumerate(node_map) if v is None]
    if unassigned_nodes:
        error_msg = f"ERROR: {len(unassigned_nodes)} node(s) are unassigned: {unassigned_nodes}\n"
        error_msg += "       Either add them to the 'placement' list or set 'fill_remaining: true'"
        raise ValueError(error_msg)

    # Generate federates for assigned nodes
    federates = []
    for i, assignment in enumerate(node_map):
        if assignment == "house":
            federates.append(
                {
                    "directory": ".",
                    "exec": f"helics_player {input_file} --broker=broker --local --name=node_{i}",
                    "host": "localhost",
                    "name": f"node_{i}",
                }
            )

    runner = {"name": "house_federation", "federates": federates}

    with open(output_path, "w") as f:
        json.dump(runner, f, indent=2)

    # Report what was created
    assigned_nodes = [i for i, v in enumerate(node_map) if v is not None]

    print(f"Generated {output_path} with {len(federates)} house node instances")
    print(f"  Assigned nodes: {assigned_nodes}")


def create_recorder_runner(conf, output_path):
    """Generate recorder runner.json file."""
    fed_conf = OmegaConf.select(conf, "federates")
    target = fed_conf["recorder"]["target"]
    output_file = fed_conf["recorder"]["output_file"]

    runner = {
        "name": "recorder_federation",
        "federates": [
            {
                "directory": ".",
                "exec": f"helics_recorder --name=recorder --capture={target} --output={output_file} --broker=broker",
                "host": "localhost",
                "name": "recorder",
            }
        ],
    }

    with open(output_path, "w") as f:
        json.dump(runner, f, indent=2)
    print(f"Generated {output_path}")


def create_grid_config(conf, output_path):
    """
    Generate grid config JSON from experiment config and save to output_path.
    """
    fed_conf = OmegaConf.select(conf, "federates")
    grid_config = {
        "name": fed_conf["grid"]["name"] if "grid" in fed_conf else "grid",
        "loglevel": "warning",
        "coreType": "zmq",
        "period": conf["general"]["time_step"],
        "offset": conf["general"]["start_time"],
        "max_cosim_duration": conf["general"]["end_time"]
        - conf["general"]["start_time"],
        "broker": fed_conf["broker"]["name"] if "broker" in fed_conf else "broker",
        "uninterruptible": False,
        "terminate_on_error": True,
        "wait_for_current_time_update": True,
    }
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(grid_config, f, indent=4)
    print(f"Generated {output_path} with grid config:")
    print(json.dumps(grid_config, indent=4))


def main(config_path, output_dir):
    """
    Main entry: loads config, generates docker-compose, runner.json files, and grid config.
    """
    conf = OmegaConf.load(config_path)

    # Generate docker-compose.yml
    compose_path = os.path.join(output_dir, "docker-compose.yml")
    create_docker_compose(conf, compose_path)

    # Generate runner.json files for each federate class
    broker_runner_path = os.path.join(output_dir, "broker_runner.json")
    grid_runner_path = os.path.join(output_dir, "grid_runner.json")
    house_runner_path = os.path.join(output_dir, "house_runner.json")
    recorder_runner_path = os.path.join(output_dir, "recorder_runner.json")

    create_broker_runner(conf, broker_runner_path)
    create_grid_runner(conf, grid_runner_path)
    create_house_runner(conf, house_runner_path)
    create_recorder_runner(conf, recorder_runner_path)

    # Generate grid HELICS config
    grid_config_path = os.path.join(output_dir, "grid_config.json")
    create_grid_config(conf, grid_config_path)


if __name__ == "__main__":
    config_dir = os.environ.get("CONFIG_DIR", "/config")
    output_dir = os.environ.get("OUTPUT_DIR", os.path.join(config_dir, "tmp"))
    experiment_path = os.path.join(config_dir, "experiment.yml")
    main(experiment_path, output_dir)
