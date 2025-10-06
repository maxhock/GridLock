#!/usr/bin/env python3
"""
Generate docker-compose.yml from experiment.yml using OmegaConf config merging.

Uses OmegaConf.merge() to create compose-shaped config objects for each service
and combines them into a complete docker-compose structure.

Reads config/experiment.yml and creates services for:
- broker, grid, house_1..house_N, recorder
"""

import os
from omegaconf import OmegaConf
import json
import pandas as pd

def create_docker_compose(conf, output_path):
    """
    Generate docker-compose YAML from experiment config and save to output_path.
    """

    # Compute num_nodes from Excel if grid_file is specified
    fed_conf = OmegaConf.select(conf, "federates")

    try:
        grid_file = fed_conf["grid"]["grid_file"]
    except Exception:
        grid_file = None

    if grid_file:
        # Try relative to config, then fallback to CWD
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
            print(f"No valid grid_file found at {excel_path}; using num_nodes from config.")
    else:
        print("No grid_file specified in config; using num_nodes from config.")

    fed_conf = OmegaConf.select(conf, "federates")
    if not OmegaConf.has_resolver("eval"):
        OmegaConf.register_new_resolver("eval", eval)
    OmegaConf.resolve(fed_conf["grid"])

    fed_conf["house"]["build_folder"] = fed_conf["house"]["build_folder"]
    for i in range(fed_conf["house"]["num_houses"]):
        conf_house_i = fed_conf["house"]
        conf_house_i["name"] = f"house_{i}"
        fed_conf[f"house_{i}"] = conf_house_i
    del fed_conf["house"]

    new_conf = OmegaConf.create()
    OmegaConf.resolve(fed_conf)
    for key in fed_conf:
        new_conf = OmegaConf.merge(new_conf,{
            "services":{
                key:{
                    "container_name": fed_conf[key].name,
                    "build": "${PWD}/"+f"{fed_conf[key].build_folder}",
                    "volumes": [
                        "${PWD}/config:/config",
                        "${PWD}/data:/data"
                    ],
                    "networks": ["helics-net"],
                    "command": fed_conf[key].command
                }},
            "networks": {
                "helics-net": {
                    "driver": "bridge"
                }}})
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w') as f:
        OmegaConf.save(new_conf, f, resolve=False)
    print(f"Generated {output_path} with {len(new_conf['services'])} services:")
    for service_name in new_conf["services"].keys():
        print(f"  - {service_name}")

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
        "max_cosim_duration": conf["general"]["end_time"] - conf["general"]["start_time"],
        "broker": fed_conf["broker"]["name"] if "broker" in fed_conf else "broker",
        "uninterruptible": False,
        "terminate_on_error": True,
        "wait_for_current_time_update": True
    }
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(grid_config, f, indent=4)
    print(f"Generated {output_path} with grid config:")
    print(json.dumps(grid_config, indent=4))

def main(config_path, output_dir):
    """
    Main entry: loads config, generates docker-compose and grid config files.
    """
    conf = OmegaConf.load(config_path)

    compose_path = os.path.join(output_dir, "docker-compose.yml")
    grid_config_path = os.path.join(os.path.dirname(config_path), "helics_grid_config.json")
    create_docker_compose(conf, compose_path)
    create_grid_config(conf, grid_config_path)

if __name__ == "__main__":
    config_dir = os.environ.get("CONFIG_DIR", "/config")
    output_dir = os.environ.get("OUTPUT_DIR", os.path.join(config_dir, "tmp"))
    experiment_path = os.path.join(config_dir, "experiment.yml")
    main(experiment_path, output_dir)