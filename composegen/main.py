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

def main():
    # Configuration paths
    config_dir = os.environ.get("CONFIG_DIR", "/config")
    output_dir = os.environ.get("OUTPUT_DIR", os.path.join(config_dir, "tmp"))
    
    experiment_path = os.path.join(config_dir, "experiment.yml")
    compose_path = os.path.join(output_dir, "docker-compose.yml")
    
    # Load experiment config with OmegaConf
    conf = OmegaConf.load(experiment_path)
    fed_conf = OmegaConf.select(conf, "federates")
    
    OmegaConf.register_new_resolver("eval", eval)


    OmegaConf.resolve(fed_conf["grid"])
    # Force evaluation of build_folder to set it prior to creating house instances
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

    # Ensure output directory exists
    os.makedirs(output_dir, exist_ok=True)
    
    # Convert to container and write docker-compose.yml
    with open(compose_path, 'w') as f:
        # Convert OmegaConf to plain dict/list structure for YAML output
        OmegaConf.save(new_conf, f, resolve=False)

    print(f"Generated {compose_path} with {len(new_conf['services'])} services:")
    for service_name in new_conf["services"].keys():
        print(f"  - {service_name}")

    # Generate helics_grid_config.yaml with the same structure as the existing file
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

    grid_config_path = os.path.join(config_dir, "helics_grid_config.json")
    with open(grid_config_path, "w") as f:
        json.dump(grid_config, f, indent=4)
    print(f"Generated {grid_config_path} with grid config:")
    print(json.dumps(grid_config, indent=4))

    

if __name__ == "__main__":
    main()