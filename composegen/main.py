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

def main():
    # Configuration paths
    config_dir = os.environ.get("CONFIG_DIR", "/config")
    output_dir = os.environ.get("OUTPUT_DIR", os.path.join(config_dir, "tmp"))
    
    experiment_path = os.path.join(config_dir, "experiment.yml")
    compose_path = os.path.join(output_dir, "docker-compose.yml")
    
    # Load experiment config with OmegaConf
    conf = OmegaConf.load(experiment_path)
    
    
    OmegaConf.register_new_resolver("eval", eval)


    OmegaConf.resolve(conf["grid"])
    for i in range(conf["house"]["num_houses"]):
        conf_house_i = conf["house"]
        conf_house_i["name"] = f"house_{i}"
        conf[f"house_{i}"] = conf_house_i
        
    del conf["house"]

    new_conf = OmegaConf.create()
    OmegaConf.resolve(conf)
    for key in conf:
        new_conf = OmegaConf.merge(new_conf,{
            "services":{
                key:{
                    "container_name": conf[key].name,
                    "build": "${PWD}/"+f"{conf[key].name.split('_')[0]}",
                    "volumes": [
                        "${PWD}/config:/config",
                        "${PWD}/data:/data"
                    ],
                    "networks": ["helics-net"],
                    "command": conf[key].command
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

    print(f"Generated {compose_path} with {len(new_conf)} services:")
    for service_name in new_conf["services"].keys():
        print(f"  - {service_name}")

if __name__ == "__main__":
    main()