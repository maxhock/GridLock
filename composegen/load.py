import traceback
from treelib import Tree
from cosim_toolbox.sims import FederationConfig, FederateConfig, DockerRunner
from monkeypatch import apply_monkeypatches


def map_params_to_class(federate_class: str) -> dict:
    """Maps internal federate types to Docker images and commands."""
    mapping = {
        "grid": {
            "image": "grid",
            "command": "python3 main.py",
        },
        "house": {
            "image": "house",
            "command": "python3 main.py",
        },
        "load": {
            "image": "house_player",
            "command": "",
        },
        "recorder": {
            "image": "recorder",
            "command": "helics_recorder",
        },
        "pv": {
            "image": "house",
            "command": "python3 main.py",
        },
        "battery": {
            "image": "house",
            "command": "python3 main.py",
        },
        "hems": {
            "image": "house",
            "command": "python3 main.py",
        },
    }
    return mapping.get(
        federate_class,
        {"image": "cosim-cst:latest", "command": "python3 main.py"},
    )


def load(tree: Tree, general_cfg: dict) -> None:
    """Generate CST federation configuration and docker-compose from the transformed tree.

    Args:
        tree: Transformed tree with pub/sub wiring.
        general_cfg: Processed general configuration dict.
    """
    name = general_cfg.get("name", "GridLock")
    federation = FederationConfig(
        f"{name}Scenario",
        f"{name}Analysis",
        f"{name}Federation",
        True,
        "json",
        "csv",
    )

    for node in tree.all_nodes():
        data = node.data
        node_class = data.get("class")
        node_type = data.get("type")

        if not node_type or node_type == "empty":
            continue

        time_step = general_cfg.get("time_step", 1.0)
        fed = FederateConfig(node.identifier, period=time_step)

        federation.add_federate_config(fed)

        mapped = map_params_to_class(node_class)
        if node_class == "grid":
            layout = data.get("layout")
            if layout:
                mapped["command"] += f" --grid_file {layout}"
            else:
                print(
                    f"Warning: No layout specified for grid {node.identifier}"
                )
        fed.config("image", mapped["image"])
        fed.config("command", mapped["command"])
        fed.config("federate_type", node_type)

        # Add pub sub
        for topic, unit in data.get("publications", {}).items():
            dtype = "string" if unit == "json" else "double"
            if not hasattr(fed, "publications"):
                fed.publications = []

            fed.publications.append(
                {"key": topic, "type": dtype, "unit": unit, "global": True}
            )

        for topic, unit in data.get("subscriptions", {}).items():
            dtype = "string" if unit == "json" else "double"
            if not hasattr(fed, "subscriptions"):
                fed.subscriptions = []

            fed.subscriptions.append(
                {"key": topic, "type": dtype, "unit": unit, "required": True}
            )

    start_str = general_cfg["start_time"]
    end_str = general_cfg["end_time"]

    print("Generating configuration definitions...")

    # Apply monkey patches before generating
    apply_monkeypatches()

    try:
        federation.write_config(start_str, end_str)
        DockerRunner.define_yaml(federation.scenario_name, use_meta_db="json")
        print("Success: Federation configuration and docker-compose.yml generated.")
    except Exception as e:
        print(f"Error generating config: {e}")
        traceback.print_exc()
