# composegen/main_rework.py
"""
Reworked composegen script with modular, data-driven approach.
"""

import yaml
import json
import os
from pathlib import Path
import pandas as pd
from omegaconf import OmegaConf, DictConfig


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
    Load the experiment configuration, determine dynamic values, and resolve all interpolations.

    This function:
    1. Loads experiment.yml using OmegaConf
    2. Reads the grid file to determine num_nodes
    3. Calculates placement maps for node-based federates
    4. Validates that all nodes are assigned
    5. Calculates the total number of federate instances
    6. Resolves all OmegaConf interpolations

    Args:
        config_path: Path to experiment.yml
        data_input_path: Path to the data/input directory

    Returns:
        Fully resolved OmegaConf configuration object
    """
    # Register the eval resolver for OmegaConf
    OmegaConf.register_new_resolver("eval", eval)

    # Load the configuration
    conf = OmegaConf.load(config_path)

    # Determine number of nodes from the grid file
    grid_file_path = data_input_path / conf.federates.grid.grid_file
    num_nodes = get_num_nodes(grid_file_path)
    conf.federates.grid.num_nodes = num_nodes
    print(f"Grid has {num_nodes} nodes.")

    # Track global node assignments across all federates
    global_node_map = [None] * num_nodes

    # First pass: Apply explicit placements for all federates
    fill_remaining_federate = None
    for fed_name, fed_config in conf.federates.items():
        # Skip federates that never need node assignments
        if fed_name in ["broker", "recorder", "grid"]:
            conf.federates[fed_name].num_instances = 1
            continue

        # Track which federate has fill_remaining for second pass
        if fed_config.get("fill_remaining", False):
            fill_remaining_federate = fed_name

        # Apply explicit placements if provided
        if "placement" in fed_config and fed_config.placement:
            for node_idx in fed_config.placement:
                if 0 <= node_idx < num_nodes:
                    if global_node_map[node_idx] is not None:
                        print(
                            f"Warning: Node {node_idx} already assigned to {global_node_map[node_idx]}. Overwriting with {fed_name}."
                        )
                    global_node_map[node_idx] = fed_name
                else:
                    print(
                        f"Warning: Placement index {node_idx} is out of bounds for {num_nodes} nodes."
                    )

    # Second pass: Apply fill_remaining if specified
    if fill_remaining_federate:
        for i in range(num_nodes):
            if global_node_map[i] is None:
                global_node_map[i] = fill_remaining_federate

    # Validation: Check that all nodes are assigned
    if None in global_node_map:
        unassigned = [i for i, v in enumerate(global_node_map) if v is None]
        raise ValueError(
            f"Incomplete node assignment! Nodes {unassigned} are not assigned to any federate. "
            f"Use 'fill_remaining: true' on a federate to cover all nodes."
        )

    # Build placement maps and num_instances for each federate from global map
    for fed_name, fed_config in conf.federates.items():
        # Skip federates that don't use nodes
        if fed_name in ["broker", "recorder", "grid"]:
            continue

        # Build placement map from global assignments
        placement_map = {
            i: fed_name for i in range(num_nodes) if global_node_map[i] == fed_name
        }

        # Store the number of instances and placement map
        conf.federates[fed_name].num_instances = len(placement_map)
        conf.federates[fed_name].placement_map = placement_map

    # Validation: Check that all nodes are assigned
    if None in global_node_map:
        unassigned = [i for i, v in enumerate(global_node_map) if v is None]
        raise ValueError(
            f"Incomplete node assignment! Nodes {unassigned} are not assigned to any federate. "
            f"Use 'fill_remaining: true' on a federate to cover all nodes."
        )

    # Calculate total number of federates for the broker
    # This is: grid (1) + recorder (1) + sum of all node-based federate instances
    total_federates = 0
    for fed_name, fed_config in conf.federates.items():
        if fed_name == "broker":
            continue
        total_federates += fed_config.get("num_instances", 1)

    conf.federates.broker.total_federates = total_federates
    print(f"Total federates: {total_federates}")

    # Resolve all interpolations
    OmegaConf.resolve(conf)

    return conf


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
            "build": {"context": f"../../{fed_config.build_folder}"},
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

        # All services depend on broker except broker itself
        if fed_name != "broker":
            service["depends_on"] = ["broker"]

        compose_config["services"][fed_name] = service

    # Write the docker-compose.yml file
    with open(output_path, "w") as f:
        yaml.dump(compose_config, f, default_flow_style=False, sort_keys=False)

    print(f"Generated docker-compose.yml at {output_path}")


def create_runner_files(conf: DictConfig, output_dir: Path) -> None:
    """
    Generate runner.json files for all federates.

    Creates appropriate runner files based on federate type:
    - Simple federates (broker, grid, recorder): Single instance
    - Node-based federates (house, etc.): Multiple instances based on placement_map

    Args:
        conf: Fully resolved configuration object
        output_dir: Directory where runner files should be written
    """
    for fed_name, fed_config in conf.federates.items():
        runner_content = {"name": fed_name, "federates": []}

        # Check if this federate has a placement map (node-based federates)
        if "placement_map" in fed_config and fed_config.placement_map:
            # Node-based federate: Create instances for each assigned node
            for node_idx in sorted(fed_config.placement_map.keys()):
                instance_name = f"node_{node_idx}"

                # Build command for this instance
                if "command" in fed_config:
                    # Replace the placeholder name in the command with the actual instance name
                    instance_command = fed_config.command.replace(
                        f"--name={fed_name}", f"--name={instance_name}"
                    )
                else:
                    # Default command for node-based federates
                    if "player" in fed_name:
                        # For player federates, use helics_player
                        input_file = fed_config.get(
                            "input_file", f"/data/input/{fed_name}.csv"
                        )
                        instance_command = f"helics_player --input={input_file} --broker=broker --name={instance_name} --local"
                    else:
                        # For other federates (e.g., house with Python code), use python main.py
                        instance_command = (
                            f"python main.py --name={instance_name} --broker=broker"
                        )

                runner_content["federates"].append(
                    {
                        "directory": "/app",
                        "exec": instance_command,
                        "host": "localhost",
                        "name": instance_name,
                    }
                )
        else:
            # Simple federate: Single instance
            # Get command - either from config or use a default
            if "command" in fed_config:
                if fed_name == "broker":
                    command = fed_config.command.replace(
                        "${broker.federates}",
                        str(conf.federates.broker.total_federates),
                    )
                else:
                    command = fed_config.command
            else:
                # Default command for federates without explicit command
                if fed_name == "broker":
                    command = f"helics_broker --federates={conf.federates.broker.total_federates} --name={fed_name} --ipv4"
                elif fed_name == "grid":
                    command = f"python main.py --name={fed_name} --broker=broker --grid_file={fed_config.grid_file}"
                elif fed_name == "recorder":
                    target = fed_config.get("target", "grid")
                    output = fed_config.get("output_file", f"/data/output/{target}.log")
                    command = f"helics_recorder --name={fed_name} --capture={target} --output={output} --broker=broker"
                else:
                    command = f"--name={fed_name} --broker=broker"

            runner_content["federates"].append(
                {
                    "directory": "/app",
                    "exec": command,
                    "host": "localhost",
                    "name": fed_name,
                }
            )

        # Write the runner file
        runner_path = output_dir / f"{fed_name}_runner.json"
        with open(runner_path, "w") as f:
            json.dump(runner_content, f, indent=4)
        print(f"Generated {runner_path.name}")


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


if __name__ == "__main__":
    main()
