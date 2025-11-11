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
SKIP_NODE_ASSIGNMENT = {"broker", "recorder", "grid"}
DEFAULT_COMMAND_TEMPLATES = {
    "broker": "helics_broker --federates={total_federates} --name={name} --ipv4",
    "grid": "python main.py --name={name} --broker=broker --grid_file={grid_file}",
    "recorder": "helics_recorder --name={name} --capture={target} --output={output_file} --broker=broker",
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
        df = pd.read_excel(grid_file_path, sheet_name="load")
        return len(df)
    except FileNotFoundError:
        raise FileNotFoundError(f"Grid file not found at {grid_file_path}")
    except Exception as e:
        raise RuntimeError(f"Error reading grid file: {e}")


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
    OmegaConf.register_new_resolver("eval", eval)
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
            fill_remaining_fed = fed_name

        if placements := fed_config.get("placement"):
            for node_idx in placements:
                if 0 <= node_idx < num_nodes:
                    if global_node_map[node_idx]:
                        print(
                            f"Warning: Node {node_idx} reassigned from "
                            f"{global_node_map[node_idx]} to {fed_name}"
                        )
                    global_node_map[node_idx] = fed_name
                else:
                    print(f"Warning: Node {node_idx} out of bounds (0-{num_nodes-1})")

    # Second pass: fill remaining
    if fill_remaining_fed:
        global_node_map = [fed or fill_remaining_fed for fed in global_node_map]

    # Validate all nodes assigned
    if unassigned := [i for i, v in enumerate(global_node_map) if v is None]:
        raise ValueError(
            f"Nodes {unassigned} unassigned. Use 'fill_remaining: true' to cover all nodes."
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
            "build": f"../../{fed_config.build_folder}",
            "container_name": fed_name,
            "volumes": [
                "../../data:/data",
                "../../config/tmp:/config/tmp:ro",
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
        if fed_name == "broker":
            command = command.replace(
                "${broker.federates}", str(conf.federates.broker.total_federates)
            )
    elif fed_name in DEFAULT_COMMAND_TEMPLATES:
        params = {
            "name": fed_name,
            "total_federates": conf.federates.broker.total_federates,
            "grid_file": fed_config.get("grid_file", ""),
            "target": fed_config.get("target", "grid"),
            "output_file": fed_config.get(
                "output_file", f"/data/output/{fed_config.get('target', 'grid')}.log"
            ),
        }
        command = DEFAULT_COMMAND_TEMPLATES[fed_name].format(**params)
    else:
        command = f"--name={fed_name} --broker=broker"

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
        "offset": conf.general.start_time,
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

    # Generate runner files for all federates
    create_runner_files(conf, output_dir)

    # Generate grid config file
    create_grid_config(conf, output_dir)

    # Generate player config files for any player federates
    for fed_name, fed_config in conf.federates.items():
        if "player" in fed_name and fed_name not in SKIP_NODE_ASSIGNMENT:
            create_player_config(fed_name, fed_config, output_dir)


if __name__ == "__main__":
    main()
