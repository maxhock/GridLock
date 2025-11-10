# composegen/main.py
"""
Modular, data-driven composegen script for HELICS co-simulation.
"""

import json
import os
from pathlib import Path
from typing import Dict, List

import pandas as pd
import yaml
from omegaconf import DictConfig, OmegaConf

# Constants
SKIP_NODE_ASSIGNMENT = {"broker", "recorder", "grid", "forecasting"}
DEFAULT_COMMAND_TEMPLATES = {
    "broker": "helics_broker --federates={total_federates} --name={name} --ipv4",
    "grid": "python main.py --name={name} --broker=broker --grid_file={grid_file}",
    "recorder": "helics_recorder --name={name} --capture={target} --output={output_file} --broker=broker",
    "forecasting": "python main.py --name={name} --broker=broker --grid_file={grid_file} --api_host={api_host} --api_port={api_port}",
}


def get_num_nodes(grid_file_path: Path) -> int:
    """
    Read the grid Excel file and determine the number of load nodes.

    Args:
        grid_file_path: Path to the grid Excel file

    Returns:
        Number of load nodes in the grid
    """
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
                "build": "${PWD}/house",
                "volumes": ["${PWD}/config:/config", "${PWD}/data:/data"],
                "networks": ["helics-net"],
                "depends_on": ["broker"],
            },
            "battery": {
                "container_name": "battery",
                "build": "${PWD}/battery",
                "volumes": ["${PWD}/config:/config", "${PWD}/data:/data"],
                "networks": ["helics-net"],
                "depends_on": ["broker"],
            },
            "controller": { 
                "container_name": "controller",
                "build": "${PWD}/controller",
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


def load_and_prepare_config(config_path: Path, data_input_path: Path) -> DictConfig:
    """
    Load experiment configuration and calculate dynamic values.

    Steps:
    1. Load experiment.yml with OmegaConf
    2. Determine num_nodes from grid file
    3. Calculate placement maps and validate node assignments
    4. Calculate total federate count for broker
    5. Resolve all interpolations

    Args:
        config_path: Path to experiment.yml
        data_input_path: Path to data/input directory

    Returns:
        Fully resolved OmegaConf configuration
    """
    conf = OmegaConf.load(config_path)

    # Determine number of nodes from grid file
    grid_file_path = data_input_path / conf.federates.grid.grid_file
    num_nodes = get_num_nodes(grid_file_path)
    conf.federates.grid.num_nodes = num_nodes
    print(f"Grid has {num_nodes} nodes.")

    # Calculate node assignments
    _calculate_node_assignments(conf, num_nodes)

    # Calculate total federates for broker
    total = sum(
        fed_config.get("num_instances", 1)
        for fed_name, fed_config in conf.federates.items()
        if fed_name != "broker"
    )
    conf.federates.broker.total_federates = total
    print(f"Total federates: {total}")

    OmegaConf.resolve(conf)
    return conf


def _calculate_node_assignments(conf: DictConfig, num_nodes: int) -> None:
    """Calculate and validate node-to-federate assignments."""
    global_node_map = [None] * num_nodes
    fill_remaining_fed = None

    # Set num_instances for simple federates
    for fed_name in SKIP_NODE_ASSIGNMENT:
        if fed_name in conf.federates:
            conf.federates[fed_name].num_instances = 1

    # First pass: explicit placements
    for fed_name, fed_config in conf.federates.items():
        if fed_name in SKIP_NODE_ASSIGNMENT:
            continue

        if fed_config.get("fill_remaining"):
            if fill_remaining_fed is not None:
                raise ValueError(
                    f"Multiple federates have 'fill_remaining: true': {fill_remaining_fed}, {fed_name}. "
                    "Only one federate can use fill_remaining."
                )
            fill_remaining_fed = fed_name

def create_house_runner(conf, output_path):
    """Generate house runner.json file with multiple house instances."""
    fed_conf = OmegaConf.select(conf, "federates")
    num_houses = fed_conf["grid"]["num_nodes"]
    stop_time = conf["general"]["end_time"] - conf["general"]["start_time"]
    dt_seconds = conf["general"]["time_step"]
    config_file = fed_conf["house"]["config_file"] # Assumes config entry

    federates = []
    for i in range(num_houses):
        federates.append(
            {
                "directory": "/app",
                "exec": f"python main.py --name=house_{i} --config={config_file} --stop_time={stop_time} --dt={dt_seconds}",
                "host": "localhost",
                "name": f"house_{i}",
            }
        )

    # Build placement maps
    for fed_name, fed_config in conf.federates.items():
        if fed_name in SKIP_NODE_ASSIGNMENT:
            continue

        placement_map = {
            i: fed_name for i in range(num_nodes) if global_node_map[i] == fed_name
        }
        conf.federates[fed_name].num_instances = len(placement_map)
        conf.federates[fed_name].placement_map = placement_map


def create_docker_compose(conf: DictConfig, output_path: Path) -> None:
    """
    Generate docker-compose.yml from the configuration.

    Dynamically creates a service for each federate defined in experiment.yml.

    Args:
        conf: Fully resolved configuration object
        output_path: Path where docker-compose.yml should be written
    """
    compose_config = {"networks": {"helics-net": {"driver": "bridge"}}, "services": {}}

    # Generate a service for each federate
    for fed_name, fed_config in conf.federates.items():
        service = {
            "build": f"${{PWD}}/{fed_config.build_folder}",
            "container_name": fed_name,
            "volumes": [
                "${PWD}/data:/data",
                "${PWD}/config/tmp:/config/tmp:ro",
            ],
            "networks": ["helics-net"],
        }

        # Add command only if specified in config
        if "command" in fed_config:
            service["command"] = fed_config.command

        compose_config["services"][fed_name] = service

    # Write the docker-compose.yml file
    with open(output_path, "w") as f:
        yaml.dump(compose_config, f, default_flow_style=False, sort_keys=False)

    print(f"Generated docker-compose.yml at {output_path}")

def create_battery_runner(conf, output_path):
    """Generate battery runner.json file with multiple battery instances."""
    fed_conf = OmegaConf.select(conf, "federates")
    num_batteries = fed_conf["grid"]["num_nodes"]
    stop_time = conf["general"]["end_time"] - conf["general"]["start_time"]
    dt_seconds = conf["general"]["time_step"]
    config_file = fed_conf["battery"]["config_file"] # Assumes a new config entry

    federates = []
    for i in range(num_batteries):
        federates.append(
            {
                "directory": "/app", # Assuming code is in /app
                "exec": f"python main.py --name=battery_{i} --config={config_file} --stop_time={stop_time} --dt={dt_seconds}",
                "host": "localhost",
                "name": f"battery_{i}",
            }
        )

    runner = {"name": "battery_federation", "federates": federates}

    with open(output_path, "w") as f:
        json.dump(runner, f, indent=2)
    print(f"Generated {output_path} with {num_batteries} battery instances")


def create_controller_runner(conf, output_path):
    """Generate controller runner.json file with multiple controller instances."""
    fed_conf = OmegaConf.select(conf, "federates")
    num_controllers = fed_conf["grid"]["num_nodes"]
    stop_time = conf["general"]["end_time"] - conf["general"]["start_time"]
    dt_seconds = conf["general"]["time_step"]

    federates = []
    for i in range(num_controllers):
        federates.append(
            {
                "directory": "/app", 
                "exec": f"python main.py --name=controller_{i} --stop_time={stop_time} --dt={dt_seconds}",
                "host": "localhost",
                "name": f"controller_{i}",
            }
        )

    runner = {"name": "controller_federation", "federates": federates}

    with open(output_path, "w") as f:
        json.dump(runner, f, indent=2)
    print(f"Generated {output_path} with {num_controllers} controller instances")

def create_runner_files(conf: DictConfig, output_dir: Path) -> None:
    """
    Generate runner.json files for all federates.

    Args:
        conf: Fully resolved configuration
        output_dir: Directory for runner files
    """
    for fed_name, fed_config in conf.federates.items():
        federates_list = (
            _create_node_based_instances(fed_name, fed_config)
            if fed_config.get("placement_map")
            else [_create_simple_instance(fed_name, fed_config, conf)]
        )

        runner_path = output_dir / f"{fed_name}_runner.json"
        with open(runner_path, "w") as f:
            json.dump({"name": fed_name, "federates": federates_list}, f, indent=4)
        print(f"Generated {runner_path.name}")


def _create_node_based_instances(fed_name: str, fed_config: DictConfig) -> List[Dict]:
    """Create federate instances for node-based federates."""
    instances = []
    for node_idx in sorted(fed_config.placement_map.keys()):
        instance_name = f"node_{node_idx}"

        if "command" in fed_config:
            command = fed_config.command.replace(
                f"--name={fed_name}", f"--name={instance_name}"
            )
        elif "player" in fed_name:
            input_file = fed_config.get("input_file", f"/data/input/{fed_name}.csv")
            # Player config is in /config/tmp
            config_file = f"/config/tmp/{fed_name}_config.json"
            command = f"helics_player --input={input_file} --config-file={config_file} --broker=broker --name={instance_name} --local"
        else:
            command = f"python main.py --name={instance_name} --broker=broker"

        instances.append(
            {
                "directory": "/app",
                "exec": command,
                "host": "localhost",
                "name": instance_name,
            }
        )
    return instances


def _create_simple_instance(
    fed_name: str, fed_config: DictConfig, conf: DictConfig
) -> Dict:
    """Create a single federate instance for simple federates."""
    if "command" in fed_config:
        command = fed_config.command
    elif fed_name in DEFAULT_COMMAND_TEMPLATES:
        params = {
            "name": fed_name,
            "total_federates": conf.federates.broker.total_federates,
            "grid_file": fed_config.get("grid_file", ""),
            "target": fed_config.get("target", "grid"),
            "output_file": fed_config.get(
                "output_file", f"/data/output/{fed_config.get('target', 'grid')}.log"
            ),
            "api_host": fed_config.get("api_host", "fastapi_server"),
            "api_port": fed_config.get("api_port", 8000),
        }
        command = DEFAULT_COMMAND_TEMPLATES[fed_name].format(**params)
    else:
        command = f"python main.py --name={fed_name} --broker=broker"

    return {
        "directory": "/app",
        "exec": command,
        "host": "localhost",
        "name": fed_name,
    }


def create_grid_config(conf: DictConfig, output_dir: Path) -> None:
    """
    Generate grid_config.json for the grid federate.

    This config file contains general simulation parameters and HELICS settings
    that the grid federate needs.

    Args:
        conf: Fully resolved configuration object
        output_dir: Directory where grid_config.json should be written
    """
    grid_config = {
        "name": conf.federates.grid.name,
        "loglevel": conf.general.loglevel,
        "coreType": "zmq",
        "period": conf.general.time_step,
        "offset": conf.general.start_time - conf.general.start_time,
        "max_cosim_duration": conf.general.end_time,
        "broker": conf.federates.broker.name,
        "uninterruptible": False,
        "terminate_on_error": True,
        "wait_for_current_time_update": True,
    }

    config_path = output_dir / "grid_config.json"
    with open(config_path, "w") as f:
        json.dump(grid_config, f, indent=4)
    print(f"Generated {config_path.name}")


def create_forecasting_config(conf: DictConfig, output_dir: Path) -> None:
    """
    Generate forecasting_config.json for the forecasting federate.

    This config file contains general simulation parameters and HELICS settings
    that the forecasting federate needs.

    Args:
        conf: Fully resolved configuration object
        output_dir: Directory where forecasting_config.json should be written
    """
    forecasting_config = {
        "name": conf.federates.forecasting.name,
        "loglevel": conf.general.loglevel,
        "coreType": "zmq",
        "period": conf.general.time_step,
        "offset": conf.general.start_time - conf.general.start_time,
        "max_cosim_duration": conf.general.end_time,
        "broker": conf.federates.broker.name,
        "uninterruptible": False,
        "terminate_on_error": True,
        "wait_for_current_time_update": True,
    }

    config_path = output_dir / "forecasting_config.json"
    with open(config_path, "w") as f:
        json.dump(forecasting_config, f, indent=4)
    print(f"Generated {config_path.name}")


def create_player_config(
    fed_name: str, fed_config: DictConfig, output_dir: Path
) -> None:
    """
    Generate HELICS player config file with unit specifications.

    Args:
        fed_name: Name of the player federate
        fed_config: Configuration for the player federate
        output_dir: Directory where player config should be written
    """
    # Default publication configuration
    player_config = {
        "publications": [
            {
                "key": "P",
                "type": "double",
                "unit": "kW",  # CSV data is in kW
                "global": False,
            }
        ]
    }

    config_path = output_dir / f"{fed_name}_config.json"
    with open(config_path, "w") as f:
        json.dump(player_config, f, indent=2)
    print(f"Generated {config_path.name}")


def main():
    """
    Main function to generate docker-compose.yml and runner files.
    """
    config_path = Path(os.environ.get("CONFIG_PATH", "/config/experiment.yml"))
    output_dir = Path(os.environ.get("OUTPUT_DIR", "/config/tmp"))
    data_input_dir = Path(os.environ.get("DATA_INPUT_DIR", "/data/input"))

    output_dir.mkdir(parents=True, exist_ok=True)

    # Load and prepare the configuration
    conf = load_and_prepare_config(config_path, data_input_dir)

    print("Configuration loaded and prepared successfully.")

    # Generate docker-compose.yml
    create_docker_compose(conf, output_dir / "docker-compose.yml")

    # Generate runner.json files for each federate class
    broker_runner_path = os.path.join(output_dir, "broker_runner.json")
    grid_runner_path = os.path.join(output_dir, "grid_runner.json")
    house_runner_path = os.path.join(output_dir, "house_runner.json")
    battery_runner_path = os.path.join(output_dir, "battery_runner.json")
    controller_runner_path = os.path.join(output_dir, "controller_runner.json") # NEW
    recorder_runner_path = os.path.join(output_dir, "recorder_runner.json")

    create_broker_runner(conf, broker_runner_path)
    create_grid_runner(conf, grid_runner_path)
    create_house_runner(conf, house_runner_path)
    create_battery_runner(conf, battery_runner_path)
    create_controller_runner(conf, controller_runner_path)
    create_recorder_runner(conf, recorder_runner_path)
    
    # Generate grid HELICS config
    grid_config_path = os.path.join(os.path.dirname(config_path), "grid_config.json")
    create_grid_config(conf, grid_config_path)


if __name__ == "__main__":
    main()
