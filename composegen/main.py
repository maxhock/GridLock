#!/usr/bin/env python3
"""
Generate helics_runner.json from experiment.yml using OmegaConf config merging.

Uses OmegaConf to parse experiment config and generate helics_runner.json
that describes all federates and how to launch them.

Reads config/experiment.yml and creates federate definitions for:
- broker, grid, house_1..house_N, recorder
"""

import os
import ast
import operator
from omegaconf import OmegaConf
import json
import pandas as pd


def safe_eval(expression: str) -> int:
    """
    Safely evaluate simple arithmetic expressions using AST parsing.
    Only allows integers and basic operators (+, -, *, /).
    
    Args:
        expression: String containing a simple arithmetic expression
        
    Returns:
        Result of the evaluation as an integer
        
    Raises:
        ValueError: If the expression contains invalid operations
    """
    # Define allowed operations
    operators = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.UAdd: operator.pos,
        ast.USub: operator.neg,
    }
    
    def eval_node(node):
        if isinstance(node, ast.Constant):  # Python 3.8+
            return node.value
        elif isinstance(node, ast.Num):  # Python 3.7 and earlier
            return node.n
        elif isinstance(node, ast.BinOp):
            left = eval_node(node.left)
            right = eval_node(node.right)
            op = operators.get(type(node.op))
            if op is None:
                raise ValueError(f"Unsupported operator: {type(node.op).__name__}")
            return op(left, right)
        elif isinstance(node, ast.UnaryOp):
            operand = eval_node(node.operand)
            op = operators.get(type(node.op))
            if op is None:
                raise ValueError(f"Unsupported unary operator: {type(node.op).__name__}")
            return op(operand)
        else:
            raise ValueError(f"Unsupported expression: {type(node).__name__}")
    
    try:
        tree = ast.parse(expression, mode='eval')
        result = eval_node(tree.body)
        return int(result)
    except SyntaxError as e:
        raise ValueError(f"Invalid syntax in expression '{expression}': {e}")
    except Exception as e:
        raise ValueError(f"Failed to evaluate expression '{expression}': {e}")


def create_docker_compose(conf, output_path):
    """
    Generate docker-compose YAML with one container per federate class.
    Each container uses helics_runner to spawn multiple instances.
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
            print(
                f"No valid grid_file found at {excel_path}; using num_nodes from config."
            )
    else:
        print("No grid_file specified in config; using num_nodes from config.")

    fed_conf = OmegaConf.select(conf, "federates")
    if not OmegaConf.has_resolver("eval"):
        OmegaConf.register_new_resolver("eval", safe_eval)
    OmegaConf.resolve(fed_conf)

    # Build docker-compose with one service per federate CLASS
    services = {}
    
    # Broker service
    if "broker" in fed_conf:
        services["broker"] = {
            "container_name": fed_conf["broker"]["name"],
            "build": "${PWD}/" + fed_conf["broker"]["build_folder"],
            "volumes": ["${PWD}/config:/config", "${PWD}/data:/data"],
            "networks": ["helics-net"],
            "command": f"helics_runner /config/tmp/helics_runner_broker.json"
        }
    
    # Grid service
    if "grid" in fed_conf:
        services["grid"] = {
            "container_name": fed_conf["grid"]["name"],
            "build": "${PWD}/" + fed_conf["grid"]["build_folder"],
            "volumes": ["${PWD}/config:/config", "${PWD}/data:/data"],
            "networks": ["helics-net"],
            "command": f"helics_runner /config/tmp/helics_runner_grid.json",
            "depends_on": ["broker"]
        }
    
    # House service (one container for all house instances)
    if "house" in fed_conf:
        services["house"] = {
            "container_name": fed_conf["house"]["name"],
            "build": "${PWD}/" + fed_conf["house"]["build_folder"],
            "volumes": ["${PWD}/config:/config", "${PWD}/data:/data"],
            "networks": ["helics-net"],
            "command": f"helics_runner /config/tmp/helics_runner_house.json",
            "depends_on": ["broker"]
        }
    
    # Recorder service
    if "recorder" in fed_conf:
        services["recorder"] = {
            "container_name": fed_conf["recorder"]["name"],
            "build": "${PWD}/" + fed_conf["recorder"]["build_folder"],
            "volumes": ["${PWD}/config:/config", "${PWD}/data:/data"],
            "networks": ["helics-net"],
            "command": f"helics_runner /config/tmp/helics_runner_recorder.json",
            "depends_on": ["broker"]
        }
    
    new_conf = {
        "services": services,
        "networks": {"helics-net": {"driver": "bridge"}}
    }
    
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        OmegaConf.save(OmegaConf.create(new_conf), f, resolve=False)
    
    print(f"\nGenerated {output_path} with {len(services)} services (one per federate class):")
    for service_name in services.keys():
        print(f"  - {service_name}")


def create_helics_runner_configs(conf, output_dir):
    """
    Generate separate helics_runner.json files for each federate class.
    Each container will use its own helics_runner.json to spawn instances.
    """
    fed_conf = OmegaConf.select(conf, "federates")
    
    # Compute num_nodes from Excel if grid_file is specified
    try:
        grid_file = fed_conf["grid"]["grid_file"]
        # Try /data/input first (container context), then relative path
        excel_path = os.path.join("/data", "input", grid_file)
        if not os.path.isfile(excel_path):
            # Try relative to current directory (for local testing)
            excel_path = os.path.join(os.path.dirname(__file__), "..", "data", "input", grid_file)
        
        if os.path.isfile(excel_path):
            try:
                df = pd.read_excel(excel_path, sheet_name="load", header=0)
                num_nodes = len(df)
                print(f"Detected {num_nodes} nodes from {grid_file} (load sheet).")
                conf["federates"]["grid"]["num_nodes"] = int(num_nodes)
            except Exception as e:
                print(f"WARNING: Could not read load sheet from {grid_file}: {e}")
        else:
            print(f"WARNING: Could not find grid_file at {excel_path}")
    except Exception as e:
        print(f"WARNING: Error processing grid_file: {e}")
    
    # Resolve any references in the config
    if not OmegaConf.has_resolver("eval"):
        OmegaConf.register_new_resolver("eval", safe_eval)
    
    # Only resolve if num_nodes was successfully set
    if "num_nodes" in conf.get("federates", {}).get("grid", {}):
        OmegaConf.resolve(fed_conf)
    else:
        print("WARNING: num_nodes not set, skipping resolution")
    
    os.makedirs(output_dir, exist_ok=True)
    configs_created = []
    
    # Calculate total federates for broker
    total_federates = 1  # grid
    if "house" in fed_conf:
        total_federates += fed_conf["house"]["num_houses"]
    total_federates += 1  # recorder
    
    # Create broker helics_runner.json
    broker_config = {
        "name": "GridLock_Broker",
        "broker": True,
        "broker_args": f"--federates={total_federates} --name={fed_conf['broker']['name']}"
    }
    broker_path = os.path.join(output_dir, "helics_runner_broker.json")
    with open(broker_path, "w") as f:
        json.dump(broker_config, f, indent=2)
    configs_created.append(("broker", broker_path, 1))
    
    # Create grid helics_runner.json (single instance)
    if "grid" in fed_conf:
        grid_config = {
            "name": "GridLock_Grid",
            "broker": False,
            "federates": [{
                "directory": "/app",
                "exec": f"python main.py --grid_file={fed_conf['grid']['grid_file']}",
                "host": "localhost",
                "name": fed_conf["grid"]["name"]
            }]
        }
        grid_path = os.path.join(output_dir, "helics_runner_grid.json")
        with open(grid_path, "w") as f:
            json.dump(grid_config, f, indent=2)
        configs_created.append(("grid", grid_path, 1))
    
    # Create house helics_runner.json (multiple instances via count)
    if "house" in fed_conf:
        num_houses = fed_conf["house"]["num_houses"]
        house_config = {
            "name": "GridLock_House",
            "broker": False,
            "federates": [{
                "directory": "/app",
                "exec": f"helics_player {fed_conf['house']['input_file']}",
                "host": "localhost",
                "name": fed_conf["house"]["name"],
                "count": num_houses
            }]
        }
        house_path = os.path.join(output_dir, "helics_runner_house.json")
        with open(house_path, "w") as f:
            json.dump(house_config, f, indent=2)
        configs_created.append(("house", house_path, num_houses))
    
    # Create recorder helics_runner.json (single instance)
    if "recorder" in fed_conf:
        recorder_config = {
            "name": "GridLock_Recorder",
            "broker": False,
            "federates": [{
                "directory": "/app",
                "exec": f"helics_recorder --capture={fed_conf['recorder']['target']} --output={fed_conf['recorder']['output_file']}",
                "host": "localhost",
                "name": fed_conf["recorder"]["name"]
            }]
        }
        recorder_path = os.path.join(output_dir, "helics_runner_recorder.json")
        with open(recorder_path, "w") as f:
            json.dump(recorder_config, f, indent=2)
        configs_created.append(("recorder", recorder_path, 1))
    
    print(f"\nGenerated {len(configs_created)} helics_runner config files:")
    for name, path, count in configs_created:
        print(f"  - {name}: {path} ({count} instance(s))")
    
    return configs_created


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
    Main entry: loads config, generates helics_runner configs per federate class and docker-compose.
    """
    conf = OmegaConf.load(config_path)

    # Generate separate helics_runner.json files for each federate class
    create_helics_runner_configs(conf, output_dir)
    
    # Generate grid-specific HELICS config
    grid_config_path = os.path.join(
        os.path.dirname(config_path), "helics_grid_config.json"
    )
    create_grid_config(conf, grid_config_path)
    
    # Generate docker-compose.yml with one service per federate class
    compose_path = os.path.join(output_dir, "docker-compose.yml")
    create_docker_compose(conf, compose_path)


if __name__ == "__main__":
    config_dir = os.environ.get("CONFIG_DIR", "/config")
    output_dir = os.environ.get("OUTPUT_DIR", os.path.join(config_dir, "tmp"))
    experiment_path = os.path.join(config_dir, "experiment.yml")
    main(experiment_path, output_dir)
