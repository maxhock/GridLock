"""
Composegen: Configuration generator for GridLock HELICS co-simulation.

This script reads experiment.yml, validates the configuration, expands
multi-instance federates based on a node placement strategy, and generates
docker-compose.yml, runner.json, and config.json files for each federate.
"""

import json
import os
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import yaml
from omegaconf import DictConfig, OmegaConf


# --- Constants ---

# Federates that do not get assigned to nodes
SKIP_NODE_ASSIGNMENT = frozenset(
    {"broker", "recorder", "grid", "transformer", "logger"}
)

# Default command templates for known federates
DEFAULT_COMMAND_TEMPLATES = {
    "broker": "helics_broker --federates={total_federates} --name={name} --ipv4",
    "grid": "python main.py --name={name} --broker=broker --grid_file={grid_file}",
    "recorder": "helics_recorder --name={name} --capture={target} --output={output_file} --broker=broker",
    "transformer": "helics_app source /config/tmp/transformer_config.json --broker=broker",
    "logger": "helics_recorder --name={name} --capture={target} --output=/app/logger_raw.log --broker=broker",
}


# --- Validation and Loading ---


def load_experiment_config(config_path: Path) -> DictConfig:
    """
    Load experiment.yml and return an OmegaConf DictConfig.

    Args:
        config_path: Path to experiment.yml

    Returns:
        OmegaConf DictConfig object

    Raises:
        FileNotFoundError: If the config file does not exist
        SystemExit: If the config is invalid
    """
    if not config_path.exists():
        print(f"ERROR: Configuration file not found at {config_path}")
        sys.exit(1)

    conf = OmegaConf.load(config_path)

    # Validate required top-level keys
    if "federates" not in conf:
        print("ERROR: 'federates' section is missing from experiment.yml")
        sys.exit(1)

    if "general" not in conf:
        print("ERROR: 'general' section is missing from experiment.yml")
        sys.exit(1)

    # Validate grid federate has grid_file
    if "grid" not in conf.federates:
        print("ERROR: 'grid' federate is required in experiment.yml")
        sys.exit(1)

    if not conf.federates.grid.get("grid_file"):
        print("ERROR: 'grid_file' must be specified for the grid federate")
        sys.exit(1)

    return conf


def get_load_indices(grid_file_path: Path) -> list[int]:
    """
    Read the grid Excel file and return the list of load indices.

    These indices represent the available nodes that federates can be
    assigned to. The indices are read from the first column of the Excel
    file, which contains the original pandapower load indices.

    Args:
        grid_file_path: Path to the grid Excel file

    Returns:
        List of load indices from the grid

    Raises:
        SystemExit: If the file cannot be read or has no loads
    """
    if not grid_file_path.exists():
        print(f"ERROR: Grid file not found at {grid_file_path}")
        sys.exit(1)

    try:
        df = pd.read_excel(grid_file_path, sheet_name="load", index_col=0)
    except Exception as e:
        print(f"ERROR: Could not read 'load' sheet from grid file: {e}")
        sys.exit(1)

    if df.empty:
        print("ERROR: Grid file has no loads defined")
        sys.exit(1)

    return list(df.index)


def get_ext_grid_indices(grid_file_path: Path) -> list[int]:
    """
    Read the grid Excel file and return the list of ext_grid indices.

    The indices are read from the first column of the Excel file,
    which contains the original pandapower ext_grid indices.

    Args:
        grid_file_path: Path to the grid Excel file

    Returns:
        List of ext_grid indices from the grid
    """
    try:
        df = pd.read_excel(grid_file_path, sheet_name="ext_grid", index_col=0)
        return list(df.index)
    except Exception:
        return [0]  # Default to single ext_grid if not found


# --- Placement Logic ---


def build_node_placement_matrix(
    conf: DictConfig, node_indices: list[int]
) -> dict[int, str]:
    """
    Build a placement matrix mapping each node index to a federate name.

    This function validates:
    - All nodes in explicit placements exist in the grid
    - No node is double-booked
    - Exactly one federate uses fill_remaining (if not all nodes are explicitly placed)
    - All nodes are assigned

    Args:
        conf: The experiment configuration
        node_indices: List of valid node indices from the grid

    Returns:
        Dictionary mapping node index -> federate name

    Raises:
        SystemExit: If any validation fails
    """
    node_set = set(node_indices)
    placement_matrix: dict[int, str] = {}
    fill_remaining_federate: str | None = None

    # First pass: Process explicit placements and find fill_remaining
    for fed_name, fed_config in conf.federates.items():
        if fed_name in SKIP_NODE_ASSIGNMENT:
            continue

        # Check for fill_remaining flag
        if fed_config.get("fill_remaining", False):
            if fill_remaining_federate is not None:
                print(
                    f"ERROR: Multiple federates have 'fill_remaining: true': "
                    f"'{fill_remaining_federate}' and '{fed_name}'. "
                    f"Only one federate can use fill_remaining."
                )
                sys.exit(1)
            fill_remaining_federate = fed_name

        # Process explicit placements
        if placement := fed_config.get("placement"):
            for node_idx in placement:
                # Validate node exists
                if node_idx not in node_set:
                    print(
                        f"ERROR: Federate '{fed_name}' references node {node_idx}, "
                        f"but it does not exist in the grid. "
                        f"Valid nodes: {sorted(node_indices)}"
                    )
                    sys.exit(1)

                # Check for double-booking
                if node_idx in placement_matrix:
                    print(
                        f"ERROR: Node {node_idx} is double-booked. "
                        f"Already assigned to '{placement_matrix[node_idx]}', "
                        f"but '{fed_name}' also claims it."
                    )
                    sys.exit(1)

                placement_matrix[node_idx] = fed_name

    # Second pass: Fill remaining nodes
    unassigned_nodes = [n for n in node_indices if n not in placement_matrix]

    if unassigned_nodes:
        if fill_remaining_federate is None:
            print(
                f"ERROR: Nodes {unassigned_nodes} are not assigned to any federate. "
                f"Either add explicit placements or set 'fill_remaining: true' on one federate."
            )
            sys.exit(1)

        for node_idx in unassigned_nodes:
            placement_matrix[node_idx] = fill_remaining_federate

    return placement_matrix


def expand_federate_configs(
    conf: DictConfig,
    node_indices: list[int],
    placement_matrix: dict[int, str],
    ext_grid_indices: list[int],
) -> DictConfig:
    """
    Expand multi-instance federates into individual instance configurations.

    For each node-based federate, this creates a placement_map that maps
    node indices to instance names (node_X), and optionally an input_file_map
    that maps node indices to their specific input files.

    Args:
        conf: The experiment configuration
        node_indices: List of valid node indices
        placement_matrix: Mapping of node index -> federate name
        ext_grid_indices: List of ext_grid indices for transformer

    Returns:
        Updated configuration with expanded federate details

    Raises:
        SystemExit: If input_files count doesn't match placement count
    """
    # Store node indices for reference
    conf.federates.grid.node_indices = node_indices
    conf.federates.grid.num_nodes = len(node_indices)

    # Build placement maps for each node-based federate
    for fed_name, fed_config in conf.federates.items():
        if fed_name in SKIP_NODE_ASSIGNMENT:
            # Simple federates get 1 instance
            conf.federates[fed_name].num_instances = 1
            continue

        # Build placement map: {node_idx: "node_X"}
        placement_map = {}
        assigned_nodes = []
        for node_idx in node_indices:
            if placement_matrix.get(node_idx) == fed_name:
                placement_map[node_idx] = f"node_{node_idx}"
                assigned_nodes.append(node_idx)

        conf.federates[fed_name].placement_map = placement_map
        conf.federates[fed_name].num_instances = len(placement_map)

        # Handle multiple input files for player federates
        if input_files := fed_config.get("input_files"):
            input_files_list = list(input_files)
            num_files = len(input_files_list)
            num_instances = len(assigned_nodes)

            if num_files != num_instances:
                print(
                    f"ERROR: Federate '{fed_name}' has {num_files} input_files "
                    f"but {num_instances} node placements. These must match."
                )
                sys.exit(1)

            # Map each node index to its input file (in order)
            input_file_map = {}
            for i, node_idx in enumerate(sorted(assigned_nodes)):
                input_file_map[node_idx] = input_files_list[i]

            conf.federates[fed_name].input_file_map = input_file_map
            print(f"      {fed_name}: mapped {num_files} input files to nodes")

    # Handle transformer (one per ext_grid)
    if "transformer" in conf.federates:
        ext_grid_map = {idx: f"transformer_{idx}" for idx in ext_grid_indices}
        conf.federates.transformer.ext_grid_map = ext_grid_map
        conf.federates.transformer.num_instances = len(ext_grid_indices)

    # Calculate total federates for broker (excluding broker itself)
    total_federates = sum(
        fed_config.get("num_instances", 1)
        for fed_name, fed_config in conf.federates.items()
        if fed_name != "broker"
    )
    conf.federates.broker.total_federates = total_federates

    return conf


# --- Docker Compose Generation ---


def create_docker_compose(conf: DictConfig, output_path: Path) -> None:
    """
    Generate docker-compose.yml from the configuration.

    Creates one service per federate type. Multi-instance federates
    are handled by their runner.json files.

    Args:
        conf: Fully resolved configuration object
        output_path: Path where docker-compose.yml should be written
    """
    compose_config = {
        "networks": {"helics-net": {"driver": "bridge"}},
        "services": {},
    }

    for fed_name, fed_config in conf.federates.items():
        # Infer build_folder if not specified
        build_folder = fed_config.get("build_folder", fed_name)

        service: dict[str, Any] = {
            "build": f"${{PWD}}/{build_folder}",
            "container_name": fed_name,
            "volumes": [
                "${PWD}/data:/data",
                "${PWD}/config/tmp:/config/tmp:ro",
            ],
            "networks": ["helics-net"],
        }

        # Add command if specified in config
        if "command" in fed_config:
            service["command"] = fed_config.command

        # Add environment variables for logger
        if fed_name == "logger" and "output_file" in fed_config:
            service["environment"] = {"LOGGER_OUTPUT_FILE": fed_config.output_file}

        compose_config["services"][fed_name] = service

    with open(output_path, "w") as f:
        yaml.dump(compose_config, f, default_flow_style=False, sort_keys=False)

    print(f"Generated: {output_path}")


# --- Runner JSON Generation ---


def create_runner_files(conf: DictConfig, output_dir: Path) -> None:
    """
    Generate runner.json files for all federates.

    For multi-instance federates, creates entries for each instance.

    Args:
        conf: Fully resolved configuration
        output_dir: Directory for runner files
    """
    # Collect all federate instance names for recorder/logger
    all_instance_names = _collect_all_instance_names(conf)

    # Store for recorder/logger target
    if "recorder" in conf.federates:
        conf.federates.recorder.all_targets = ";".join(all_instance_names)
    if "logger" in conf.federates:
        conf.federates.logger.all_targets = ";".join(all_instance_names)

    # Generate runner file for each federate
    for fed_name, fed_config in conf.federates.items():
        federates_list = _build_federates_list(fed_name, fed_config, conf)

        runner_content = {
            "name": fed_name,
            "federates": federates_list,
        }

        runner_path = output_dir / f"{fed_name}_runner.json"
        with open(runner_path, "w") as f:
            json.dump(runner_content, f, indent=4)

        print(f"Generated: {runner_path.name}")


def _collect_all_instance_names(conf: DictConfig) -> list[str]:
    """Collect names of all federate instances for recorder/logger targeting."""
    names = []

    for fed_name, fed_config in conf.federates.items():
        if fed_name in {"broker", "recorder", "logger"}:
            continue

        if placement_map := fed_config.get("placement_map"):
            for node_idx in sorted(placement_map.keys()):
                names.append(f"node_{node_idx}")
        elif ext_grid_map := fed_config.get("ext_grid_map"):
            for idx in sorted(ext_grid_map.keys()):
                names.append(f"transformer_{idx}")
        else:
            names.append(fed_name)

    return names


def _build_federates_list(
    fed_name: str, fed_config: DictConfig, conf: DictConfig
) -> list[dict]:
    """Build the list of federate instances for a runner.json."""
    if placement_map := fed_config.get("placement_map"):
        return _build_node_instances(fed_name, fed_config, placement_map)
    elif ext_grid_map := fed_config.get("ext_grid_map"):
        return _build_transformer_instances(ext_grid_map)
    else:
        return [_build_simple_instance(fed_name, fed_config, conf)]


def _build_node_instances(
    fed_name: str, fed_config: DictConfig, placement_map: dict
) -> list[dict]:
    """Build instances for node-based federates.

    For player federates, uses input_file_map if available (multiple files),
    otherwise falls back to single input_file. Paths without a leading '/'
    are assumed to be relative to /data/input/.
    """
    instances = []
    input_file_map = fed_config.get("input_file_map", {})

    for node_idx in sorted(placement_map.keys()):
        instance_name = f"node_{node_idx}"

        if "player" in fed_name:
            # Use per-node file if available, otherwise fall back to single file
            if node_idx in input_file_map:
                input_file = input_file_map[node_idx]
            else:
                input_file = fed_config.get("input_file", f"{fed_name}.csv")

            # Prepend /data/input/ if the path is not absolute
            if not input_file.startswith("/"):
                input_file = f"/data/input/{input_file}"

            config_file = f"/config/tmp/{fed_name}_config.json"
            command = (
                f"helics_player --input={input_file} "
                f"--config-file={config_file} "
                f"--broker=broker --name={instance_name} --local"
            )
        else:
            command = f"python main.py --name={instance_name} --broker=broker"

        instances.append({
            "directory": "/app",
            "exec": command,
            "host": "localhost",
            "name": instance_name,
        })

    return instances


def _build_transformer_instances(ext_grid_map: dict) -> list[dict]:
    """Build instances for transformer federates."""
    instances = []
    config_file = "/config/tmp/transformer_config.json"

    for idx in sorted(ext_grid_map.keys()):
        instance_name = f"transformer_{idx}"
        command = (
            f"helics_app source {config_file} "
            f"--broker=broker --name={instance_name} --local"
        )

        instances.append({
            "directory": "/app",
            "exec": command,
            "host": "localhost",
            "name": instance_name,
        })

    return instances


def _build_simple_instance(
    fed_name: str, fed_config: DictConfig, conf: DictConfig
) -> dict:
    """Build a single instance for simple federates."""
    if "command" in fed_config:
        command = fed_config.command
    elif fed_name in DEFAULT_COMMAND_TEMPLATES:
        target = fed_config.get("target", "grid")
        if fed_name in {"recorder", "logger"} and fed_config.get("all_targets"):
            target = fed_config.all_targets

        params = {
            "name": fed_name,
            "total_federates": conf.federates.broker.total_federates,
            "grid_file": conf.federates.grid.get("grid_file", ""),
            "target": target,
            "output_file": fed_config.get("output_file", "/data/output/federation.log"),
            "stop_time": conf.general.end_time,
            "voltage": fed_config.get("voltage", 1.0),
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


# --- Config JSON Generation ---


def create_config_files(conf: DictConfig, output_dir: Path) -> None:
    """
    Generate config.json files for federates that need them.

    Args:
        conf: Fully resolved configuration
        output_dir: Directory for config files
    """
    # Grid config
    _create_grid_config(conf, output_dir)

    # Logger config
    if "logger" in conf.federates:
        _create_logger_config(conf, output_dir)

    # Transformer config
    if "transformer" in conf.federates:
        _create_transformer_config(conf, output_dir)

    # Player configs for any player federates
    for fed_name, fed_config in conf.federates.items():
        if "player" in fed_name and fed_name not in SKIP_NODE_ASSIGNMENT:
            _create_player_config(fed_name, output_dir)


def _create_grid_config(conf: DictConfig, output_dir: Path) -> None:
    """Generate grid_config.json."""
    config = {
        "name": conf.federates.grid.name,
        "loglevel": conf.general.loglevel,
        "coreType": "zmq",
        "period": conf.general.time_step,
        "offset": 0,
        "max_cosim_duration": conf.general.end_time,
        "broker": conf.federates.broker.name,
        "uninterruptible": False,
        "terminate_on_error": True,
        "wait_for_current_time_update": True,
    }

    config_path = output_dir / "grid_config.json"
    with open(config_path, "w") as f:
        json.dump(config, f, indent=4)

    print(f"Generated: {config_path.name}")


def _create_logger_config(conf: DictConfig, output_dir: Path) -> None:
    """Generate logger_config.json."""
    config = {
        "name": conf.federates.logger.name,
        "loglevel": conf.general.get("loglevel", "warning"),
        "coreType": "zmq",
        "period": conf.general.time_step,
        "offset": 0,
        "max_cosim_duration": conf.general.end_time,
        "broker": conf.federates.broker.name,
        "uninterruptible": False,
        "terminate_on_error": True,
        "wait_for_current_time_update": True,
    }

    config_path = output_dir / "logger_config.json"
    with open(config_path, "w") as f:
        json.dump(config, f, indent=4)

    print(f"Generated: {config_path.name}")


def _create_transformer_config(conf: DictConfig, output_dir: Path) -> None:
    """Generate transformer_config.json for helics_app source."""
    voltage = conf.federates.transformer.get("voltage", 1.0)

    config = {
        "publications": [
            {
                "key": "VM",
                "type": "double",
                "global": False,
                "value": voltage,
            }
        ],
    }

    config_path = output_dir / "transformer_config.json"
    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)

    print(f"Generated: {config_path.name}")


def _create_player_config(fed_name: str, output_dir: Path) -> None:
    """Generate player config file with publication specifications."""
    config = {
        "publications": [
            {
                "key": "P",
                "type": "double",
                "unit": "kW",
                "global": False,
            }
        ]
    }

    config_path = output_dir / f"{fed_name}_config.json"
    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)

    print(f"Generated: {config_path.name}")


# --- Main Entry Point ---


def main() -> None:
    """
    Main function: orchestrates configuration loading, validation,
    expansion, and file generation.
    """
    # Paths from environment or defaults
    config_path = Path(os.environ.get("CONFIG_PATH", "/config/experiment.yml"))
    output_dir = Path(os.environ.get("OUTPUT_DIR", "/config/tmp"))
    data_input_dir = Path(os.environ.get("DATA_INPUT_DIR", "/data/input"))

    print("=" * 60)
    print("COMPOSEGEN: GridLock Configuration Generator")
    print("=" * 60)

    # Ensure output directory exists
    output_dir.mkdir(parents=True, exist_ok=True)

    # Step 1: Load and validate configuration
    print("\n[1/5] Loading configuration...")
    conf = load_experiment_config(config_path)
    print(f"      Loaded {config_path}")

    # Step 2: Read grid to get node indices
    print("\n[2/5] Reading grid file...")
    grid_file_path = data_input_dir / conf.federates.grid.grid_file
    node_indices = get_load_indices(grid_file_path)
    ext_grid_indices = get_ext_grid_indices(grid_file_path)
    print(f"      Found {len(node_indices)} nodes: {node_indices}")
    print(f"      Found {len(ext_grid_indices)} ext_grids: {ext_grid_indices}")

    # Step 3: Build and validate placement matrix
    print("\n[3/5] Building placement matrix...")
    placement_matrix = build_node_placement_matrix(conf, node_indices)
    print("      Placement matrix:")
    for node_idx, fed_name in sorted(placement_matrix.items()):
        print(f"        node_{node_idx} -> {fed_name}")

    # Step 4: Expand federate configurations
    print("\n[4/5] Expanding federate configurations...")
    conf = expand_federate_configs(conf, node_indices, placement_matrix, ext_grid_indices)
    for fed_name, fed_config in conf.federates.items():
        num = fed_config.get("num_instances", 1)
        print(f"        {fed_name}: {num} instance(s)")
    print(f"      Total federates for broker: {conf.federates.broker.total_federates}")

    # Resolve all interpolations
    OmegaConf.resolve(conf)

    # Step 5: Generate output files
    print("\n[5/5] Generating output files...")
    create_docker_compose(conf, output_dir / "docker-compose.yml")
    create_runner_files(conf, output_dir)
    create_config_files(conf, output_dir)

    print("\n" + "=" * 60)
    print("COMPOSEGEN: Complete!")
    print("=" * 60)


if __name__ == "__main__":
    main()
