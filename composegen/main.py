# composegen/main.py
import json
import os
from pathlib import Path
from typing import Dict, List

import pandas as pd
import yaml
from omegaconf import DictConfig, OmegaConf

# Federates that run via helics runner (inside one container)
RUNNER_FEDERATES = {"grid", "house", "controller", "battery", "house_player"}

DEFAULT_COMMAND_TEMPLATES = {
    "broker": "helics_broker --federates={total_federates} --name={name} --ipv4",
    "recorder": "helics_recorder --name={name} --capture={target} --output={output_file} --broker={broker}",
}


def get_num_nodes(grid_file_path: Path) -> int:
    if not grid_file_path.exists():
        raise FileNotFoundError(f"Grid file not found: {grid_file_path}")

    df = pd.read_excel(grid_file_path, sheet_name="load", header=0)
    n = int(len(df))
    if n <= 0:
        raise ValueError(f"Invalid num_nodes={n} from {grid_file_path}")
    return n


def _get_nodes_for_fed(conf: DictConfig, fed_key: str) -> List[int]:
    """
    If federate has 'placement', use that list. Otherwise full range(num_nodes).
    """
    num_nodes = int(conf.federates.grid.num_nodes)
    fed_cfg = conf.federates.get(fed_key)
    if fed_cfg is None:
        return list(range(num_nodes))

    placement = fed_cfg.get("placement", None)
    if placement is None:
        return list(range(num_nodes))

    return [int(x) for x in placement]


def load_and_prepare_config(config_path: Path, data_input_path: Path) -> DictConfig:
    conf = OmegaConf.load(config_path)

    # Normalize federate entries: ensure name + build_folder exist
    for fed_key, fed_cfg in conf.federates.items():
        if "name" not in fed_cfg or not fed_cfg.name:
            fed_cfg.name = fed_key
        if "build_folder" not in fed_cfg or not fed_cfg.build_folder:
            fed_cfg.build_folder = fed_cfg.name

    # num_nodes from grid file
    grid_file = conf.federates.grid.grid_file
    num_nodes = get_num_nodes(data_input_path / grid_file)
    conf.federates.grid.num_nodes = int(num_nodes)
    print(f"Grid has {num_nodes} nodes (from {grid_file}).")

    # num_instances defaults
    for fed_key, fed_cfg in conf.federates.items():
        if fed_key == "broker":
            fed_cfg.num_instances = 1
        elif fed_key == "grid":
            fed_cfg.num_instances = 1
        elif fed_key == "recorder":
            fed_cfg.num_instances = 1
        elif fed_key in {"house", "controller", "battery", "house_player"}:
            fed_cfg.num_instances = len(_get_nodes_for_fed(conf, fed_key))
        else:
            fed_cfg.num_instances = int(fed_cfg.get("num_instances", 1))

    # total federates for broker (excluding broker)
    total = 0
    for fed_key, fed_cfg in conf.federates.items():
        if fed_key == "broker":
            continue
        total += int(fed_cfg.get("num_instances", 1))
    conf.federates.broker.total_federates = int(total)
    print(f"Total federates (excluding broker): {total}")

    OmegaConf.resolve(conf)
    return conf


def _runner_write(path: Path, name: str, federates: List[Dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump({"name": name, "federates": federates}, f, indent=2)
    print(f"Generated {path} ({len(federates)} federates)")


def create_broker_runner(conf: DictConfig, output_path: Path) -> None:
    total = int(conf.federates.broker.total_federates)
    broker_name = conf.federates.broker.name
    cmd = DEFAULT_COMMAND_TEMPLATES["broker"].format(total_federates=total, name=broker_name)
    federates = [{
        "directory": "/app",
        "exec": cmd,
        "host": "localhost",
        "name": broker_name,
    }]
    _runner_write(output_path, "broker_runner", federates)


def create_grid_runner(conf: DictConfig, output_path: Path) -> None:
    grid_file = conf.federates.grid.grid_file
    federates = [{
        "directory": "/app",
        "exec": f"python main.py --grid_file={grid_file}",
        "host": "localhost",
        "name": conf.federates.grid.name,
    }]
    _runner_write(output_path, "grid_federation", federates)


def create_grid_config(conf: DictConfig, output_path: Path) -> None:
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
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(grid_config, f, indent=2)
    print(f"Generated {output_path}")


def create_house_runner(conf: DictConfig, output_path: Path) -> None:
    nodes = _get_nodes_for_fed(conf, "house")
    stop_time = float(conf.general.end_time - conf.general.start_time)
    dt = int(conf.general.time_step)
    config_file = conf.federates.house.get("config_file", "/config/house_config.yaml")

    federates = []
    for i in nodes:
        federates.append({
            "directory": "/app",
            "exec": f"python house/main.py --name=house_{i} --config={config_file} --stop_time={stop_time} --dt={dt}",
            "host": "localhost",
            "name": f"house_{i}",
        })
    _runner_write(output_path, "house_federation", federates)


def create_controller_runner(conf: DictConfig, output_path: Path) -> None:
    # If controller has no placement, reuse house placement if it exists, else all nodes
    if conf.federates.controller.get("placement", None) is None and conf.federates.house.get("placement", None) is not None:
        nodes = [int(x) for x in conf.federates.house.placement]
    else:
        nodes = _get_nodes_for_fed(conf, "controller")

    stop_time = float(conf.general.end_time - conf.general.start_time)
    dt = int(conf.general.time_step)

    federates = []
    for i in nodes:
        federates.append({
            "directory": "/app",
            "exec": f"python controller/main.py --name=controller_{i} --stop_time={stop_time} --dt={dt}",
            "host": "localhost",
            "name": f"controller_{i}",
        })
    _runner_write(output_path, "controller_federation", federates)


def create_recorder_runner(conf: DictConfig, output_path: Path) -> None:
    target = conf.federates.recorder.get("target", "grid")
    output_file = conf.federates.recorder.get("output_file", f"/data/output/{target}.log")
    broker = conf.federates.broker.name
    name = conf.federates.recorder.name

    cmd = DEFAULT_COMMAND_TEMPLATES["recorder"].format(
        name=name, target=target, output_file=output_file, broker=broker
    )
    federates = [{
        "directory": "/app",
        "exec": cmd,
        "host": "localhost",
        "name": name,
    }]
    _runner_write(output_path, "recorder_federation", federates)


def create_docker_compose(conf: DictConfig, output_path: Path) -> None:
    compose = {
        "networks": {"helics-net": {"driver": "bridge"}},
        "services": {}
    }

    broker_name = conf.federates.broker.name

    for fed_key, fed_cfg in conf.federates.items():
        name = fed_cfg.name
        build_folder = fed_cfg.build_folder

        service = {
            "container_name": name,
            "build": {
                "context": "${PWD}",
                "dockerfile": f"{build_folder}/Dockerfile",
            },
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
                "PYTHONPATH": "/app",   # <--- fixes: controller imports house.*
            },
        }

        if fed_key != "broker":
            service["depends_on"] = [broker_name]

        # commands
        if fed_key in RUNNER_FEDERATES:
            service["command"] = f"helics run --path=/config/tmp/{fed_key}_runner.json --no-log-files"
        elif fed_key == "broker":
            service["command"] = DEFAULT_COMMAND_TEMPLATES["broker"].format(
                total_federates=int(conf.federates.broker.total_federates),
                name=broker_name,
            )
        elif "command" in fed_cfg:
            service["command"] = fed_cfg.command

        compose["services"][name] = service

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        yaml.dump(compose, f, default_flow_style=False, sort_keys=False)

    print(f"Generated docker-compose.yml at {output_path}")


def main():
    config_path = Path(os.environ.get("CONFIG_PATH", "/config/experiment.yml"))
    output_dir = Path(os.environ.get("OUTPUT_DIR", "/config/tmp"))
    data_input_dir = Path(os.environ.get("DATA_INPUT_DIR", "/data/input"))

    output_dir.mkdir(parents=True, exist_ok=True)

    conf = load_and_prepare_config(config_path, data_input_dir)

    create_docker_compose(conf, output_dir / "docker-compose.yml")

    create_broker_runner(conf, output_dir / "broker_runner.json")
    create_grid_runner(conf, output_dir / "grid_runner.json")
    create_grid_config(conf, output_dir / "grid_config.json")

    if "house" in conf.federates:
        create_house_runner(conf, output_dir / "house_runner.json")
    if "controller" in conf.federates:
        create_controller_runner(conf, output_dir / "controller_runner.json")
    if "recorder" in conf.federates:
        create_recorder_runner(conf, output_dir / "recorder_runner.json")

    print("composegen finished successfully.")


if __name__ == "__main__":
    main()