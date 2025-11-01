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
            print(
                f"No valid grid_file found at {excel_path}; using num_nodes from config."
            )
    else:
        print("No grid_file specified in config; using num_nodes from config.")

    fed_conf = OmegaConf.select(conf, "federates")
    if not OmegaConf.has_resolver("eval"):
        OmegaConf.register_new_resolver("eval", safe_eval)
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
        new_conf = OmegaConf.merge(
            new_conf,
            {
                "services": {
                    key: {
                        "container_name": fed_conf[key].name,
                        "build": "${PWD}/" + f"{fed_conf[key].build_folder}",
                        "volumes": ["${PWD}/config:/config", "${PWD}/data:/data"],
                        "networks": ["helics-net"],
                        "command": fed_conf[key].command,
                    }
                },
                "networks": {"helics-net": {"driver": "bridge"}},
            },
        )
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        OmegaConf.save(new_conf, f, resolve=False)
    print(f"Generated {output_path} with {len(new_conf['services'])} services:")
    for service_name in new_conf["services"].keys():
        print(f"  - {service_name}")


def create_helics_runner_config(conf, output_path):
    """
    Generate helics_runner.json from experiment config and save to output_path.
    This is the new HELICS-preferred method for launching co-simulations.
    """
    fed_conf = OmegaConf.select(conf, "federates")
    
    # Compute num_nodes from Excel if grid_file is specified (same as before)
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
    
    # Build helics_runner configuration
    runner_config = {
        "name": "GridLock_CoSimulation",
        "broker": True,
        "federates": []
    }
    
    # Add broker arguments
    total_federates = 0
    
    # Grid federate (single instance)
    if "grid" in fed_conf:
        grid_fed = {
            "directory": "/workspace/grid",
            "exec": f"python main.py --grid_file={fed_conf['grid']['grid_file']}",
            "host": "localhost",
            "name": fed_conf["grid"]["name"]
        }
        runner_config["federates"].append(grid_fed)
        total_federates += 1
    
    # House federates (multiple instances via count)
    if "house" in fed_conf:
        num_houses = fed_conf["house"]["num_houses"]
        house_fed = {
            "directory": ".",
            "exec": f"helics_player {fed_conf['house']['input_file']}",
            "host": "localhost", 
            "name": fed_conf["house"]["name"],
            "count": num_houses
        }
        runner_config["federates"].append(house_fed)
        total_federates += num_houses
    
    # Recorder federate (single instance)
    if "recorder" in fed_conf:
        recorder_fed = {
            "directory": ".",
            "exec": f"helics_recorder --capture={fed_conf['recorder']['target']} --output={fed_conf['recorder']['output_file']}",
            "host": "localhost",
            "name": fed_conf["recorder"]["name"]
        }
        runner_config["federates"].append(recorder_fed)
        total_federates += 1
    
    # Set broker arguments
    runner_config["broker_args"] = f"--federates={total_federates}"
    
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(runner_config, f, indent=2)
    
    print(f"Generated {output_path} with {len(runner_config['federates'])} federate types:")
    print(f"  Total federate instances: {total_federates}")
    for fed in runner_config["federates"]:
        count = fed.get("count", 1)
        print(f"  - {fed['name']}: {count} instance(s)")
    
    return runner_config


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
    Main entry: loads config, generates helics_runner.json and grid config files.
    """
    conf = OmegaConf.load(config_path)

    # Generate helics_runner.json (new approach)
    runner_path = os.path.join(output_dir, "helics_runner.json")
    create_helics_runner_config(conf, runner_path)
    
    # Generate grid-specific HELICS config
    grid_config_path = os.path.join(
        os.path.dirname(config_path), "helics_grid_config.json"
    )
    create_grid_config(conf, grid_config_path)
    
    # Keep docker-compose generation for backward compatibility (can be removed later)
    compose_path = os.path.join(output_dir, "docker-compose.yml")
    create_docker_compose(conf, compose_path)


if __name__ == "__main__":
    config_dir = os.environ.get("CONFIG_DIR", "/config")
    output_dir = os.environ.get("OUTPUT_DIR", os.path.join(config_dir, "tmp"))
    experiment_path = os.path.join(config_dir, "experiment.yml")
    main(experiment_path, output_dir)
