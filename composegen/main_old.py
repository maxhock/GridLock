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
RUNNER_FEDERATES = {"grid", "house", "controller", "battery", "house_player"}
SKIP_NODE_ASSIGNMENT = {"broker", "recorder", "grid", "forecasting"}
DEFAULT_COMMAND_TEMPLATES = {
    "broker": "helics_broker --federates={total_federates} --name={name} --ipv4",
    "grid": "python main.py --name={name} --broker=broker --grid_file={grid_file}",
    "recorder": "helics_recorder --name={name} --capture={target} --output={output_file} --broker={broker}",
    "forecasting": "python main.py --name={name} --broker=broker --grid_file={grid_file} --api_host={api_host} --api_port={api_port}",
}


def get_num_nodes(grid_file_path: Path) -> int:
    if not grid_file_path.exists():
        raise FileNotFoundError(f"Grid file not found: {grid_file_path}")

    df = pd.read_excel(grid_file_path, sheet_name="load", header=0)
    num_nodes = int(len(df))
    if num_nodes <= 0:
        raise ValueError(f"Invalid num_nodes={num_nodes} from {grid_file_path}")
    return num_nodes


def _get_nodes_for_fed(conf: DictConfig, fed_key: str) -> List[int]:
    num_nodes = int(conf.federates.grid.num_nodes)
    fed_cfg = conf.federates.get(fed_key)
    if fed_cfg is None:
        return list(range(num_nodes))

    placement = fed_cfg.get("placement")
    if placement is None:
        return list(range(num_nodes))

    return [int(node) for node in placement]


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

    for fed_key, fed_cfg in conf.federates.items():
        if "name" not in fed_cfg or not fed_cfg.name:
            fed_cfg.name = fed_key
        if "build_folder" not in fed_cfg or not fed_cfg.build_folder:
            fed_cfg.build_folder = fed_cfg.name

    grid_file = conf.federates.grid.grid_file
    num_nodes = get_num_nodes(data_input_path / grid_file)
    conf.federates.grid.num_nodes = int(num_nodes)
    print(f"Grid has {num_nodes} nodes (from {grid_file}).")

    for fed_key, fed_cfg in conf.federates.items():
        if fed_key in {"broker", "grid", "recorder"}:
            fed_cfg.num_instances = 1
        elif fed_key in {"house", "controller", "battery", "house_player"}:
            fed_cfg.num_instances = len(_get_nodes_for_fed(conf, fed_key))
        else:
            fed_cfg.num_instances = int(fed_cfg.get("num_instances", 1))

    total = sum(
        int(fed_cfg.get("num_instances", 1))
        for fed_key, fed_cfg in conf.federates.items()
        if fed_key != "broker"
    )
    conf.federates.broker.total_federates = int(total)
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


def _runner_write(output_path: Path, name: str, federates: List[Dict]) -> None:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump({"name": name, "federates": federates}, f, indent=2)
    print(f"Generated {output_path} ({len(federates)} federates)")


def create_broker_runner(conf: DictConfig, output_path: Path) -> None:
    federates = [
        {
            "directory": "/app",
            "exec": DEFAULT_COMMAND_TEMPLATES["broker"].format(
                total_federates=int(conf.federates.broker.total_federates),
                name=conf.federates.broker.name,
            ),
            "host": "localhost",
            "name": conf.federates.broker.name,
        }
    ]
    _runner_write(output_path, "broker_runner", federates)


def create_grid_runner(conf: DictConfig, output_path: Path) -> None:
    federates = [
        {
            "directory": "/app",
            "exec": f"python main.py --grid_file={conf.federates.grid.grid_file}",
            "host": "localhost",
            "name": conf.federates.grid.name,
        }
    ]
    _runner_write(output_path, "grid_federation", federates)


def create_house_runner(conf: DictConfig, output_path: Path) -> None:
    """Generate house runner.json file with multiple house instances."""
    nodes = _get_nodes_for_fed(conf, "house")
    stop_time = float(conf.general.end_time - conf.general.start_time)
    dt_seconds = int(conf.general.time_step)
    config_file = conf.federates.house.get("config_file", "/config/house_config.yaml")

    federates = []
    for i in nodes:
        federates.append(
            {
                "directory": "/app",
                "exec": f"python house/main.py --name=house_{i} --config={config_file} --stop_time={stop_time} --dt={dt_seconds}",
                "host": "localhost",
                "name": f"house_{i}",
            }
        )
    _runner_write(output_path, "house_federation", federates)


def create_docker_compose(conf: DictConfig, output_path: Path) -> None:
    """
    Generate docker-compose.yml from the configuration.

    Dynamically creates a service for each federate defined in experiment.yml.

    Args:
        conf: Fully resolved configuration object
        output_path: Path where docker-compose.yml should be written
    """
    compose_config = {"networks": {"helics-net": {"driver": "bridge"}}, "services": {}}

    broker_name = conf.federates.broker.name

    # Generate a service for each federate
    for fed_name, fed_config in conf.federates.items():
        service_name = fed_config.name
        service = {
            "build": {
                "context": "${PWD}",
                "dockerfile": f"{fed_config.build_folder}/Dockerfile",
            },
            "container_name": service_name,
            "working_dir": "/app",
            "volumes": [
                "${PWD}/data:/data",
                "${PWD}/config:/config:ro",
                "${PWD}/config/tmp:/config/tmp:ro",
                "${PWD}/examples:/app/examples:ro",
            ],
            "networks": ["helics-net"],
            "environment": {
                "HELICS_BROKER": broker_name,
                "PYTHONPATH": "/app",
            },
        }

        if fed_name != "broker":
            service["depends_on"] = [broker_name]

        if fed_name in RUNNER_FEDERATES:
            service["command"] = (
                f"helics run --path=/config/tmp/{fed_name}_runner.json --no-log-files"
            )
        elif fed_name == "broker":
            service["command"] = DEFAULT_COMMAND_TEMPLATES["broker"].format(
                total_federates=int(conf.federates.broker.total_federates),
                name=broker_name,
            )
        elif "command" in fed_config:
            service["command"] = fed_config.command

        compose_config["services"][service_name] = service

    # Write the docker-compose.yml file
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        yaml.dump(compose_config, f, default_flow_style=False, sort_keys=False)

    print(f"Generated docker-compose.yml at {output_path}")


def create_battery_runner(conf: DictConfig, output_path: Path) -> None:
    """Generate battery runner.json file with multiple battery instances."""
    nodes = _get_nodes_for_fed(conf, "battery")
    stop_time = float(conf.general.end_time - conf.general.start_time)
    dt_seconds = int(conf.general.time_step)
    config_file = conf.federates.battery.get("config_file", "battery/config.yaml")

    federates = []
    for i in nodes:
        federates.append(
            {
                "directory": "/app",
                "exec": f"python battery/main.py --name=battery_{i} --config={config_file} --stop_time={stop_time} --dt={dt_seconds}",
                "host": "localhost",
                "name": f"battery_{i}",
            }
        )
    _runner_write(output_path, "battery_federation", federates)


def create_controller_runner(conf: DictConfig, output_path: Path) -> None:
    """Generate controller runner.json file with multiple controller instances."""
    if (
        conf.federates.controller.get("placement") is None
        and conf.federates.get("house") is not None
        and conf.federates.house.get("placement") is not None
    ):
        nodes = [int(node) for node in conf.federates.house.placement]
    else:
        nodes = _get_nodes_for_fed(conf, "controller")

    stop_time = float(conf.general.end_time - conf.general.start_time)
    dt_seconds = int(conf.general.time_step)

    federates = []
    for i in nodes:
        federates.append(
            {
                "directory": "/app",
                "exec": f"python controller/main.py --name=controller_{i} --stop_time={stop_time} --dt={dt_seconds}",
                "host": "localhost",
                "name": f"controller_{i}",
            }
        )
    _runner_write(output_path, "controller_federation", federates)


def create_recorder_runner(conf: DictConfig, output_path: Path) -> None:
    target = conf.federates.recorder.get("target", "grid")
    output_file = conf.federates.recorder.get(
        "output_file", f"/data/output/{target}.log"
    )
    broker_name = conf.federates.broker.name
    name = conf.federates.recorder.name

    federates = [
        {
            "directory": "/app",
            "exec": DEFAULT_COMMAND_TEMPLATES["recorder"].format(
                name=name,
                target=target,
                output_file=output_file,
                broker=broker_name,
            ),
            "host": "localhost",
            "name": name,
        }
    ]
    _runner_write(output_path, "recorder_federation", federates)


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


def create_grid_config(conf: DictConfig, output_path: Path) -> None:
    """
    Generate grid_config.json for the grid federate.

    This config file contains general simulation parameters and HELICS settings
    that the grid federate needs.

    Args:
        conf: Fully resolved configuration object
        output_path: Path where grid_config.json should be written
    """
    grid_config = {
        "name": conf.federates.grid.name,
        "loglevel": conf.general.get("loglevel", "warning"),
        "coreType": "zmq",
        "period": float(conf.general.time_step),
        "offset": 0.0,
        "max_cosim_duration": float(conf.general.end_time),
        "broker": conf.federates.broker.name,
        "uninterruptible": False,
        "terminate_on_error": True,
        "wait_for_current_time_update": True,
    }

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(grid_config, f, indent=4)
    print(f"Generated {output_path}")


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

    create_broker_runner(conf, output_dir / "broker_runner.json")
    create_grid_runner(conf, output_dir / "grid_runner.json")
    create_grid_config(conf, output_dir / "grid_config.json")

    if "house" in conf.federates:
        create_house_runner(conf, output_dir / "house_runner.json")
    if "battery" in conf.federates:
        create_battery_runner(conf, output_dir / "battery_runner.json")
    if "controller" in conf.federates:
        create_controller_runner(conf, output_dir / "controller_runner.json")
    if "recorder" in conf.federates:
        create_recorder_runner(conf, output_dir / "recorder_runner.json")

    print("composegen finished successfully.")


if __name__ == "__main__":
    main()
