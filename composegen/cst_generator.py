"""CoSim Toolbox (CST) configuration generator.

This module generates CST-compatible configuration files for HELICS federations,
following Google Python Style Guide.
"""

import traceback

from treelib import Tree

from cosim_toolbox.sims import DockerRunner, FederateConfig, FederationConfig

from .config import DEFAULT_FEDERATE_MAPPING, FEDERATE_TYPE_MAPPING


def map_params_to_type(federate_type: str) -> dict:
    """Map federate types to Docker images and commands.

    Args:
        federate_type: Type of federate (grid, house, pv, etc.).

    Returns:
        Dictionary with image and command keys.
    """
    return FEDERATE_TYPE_MAPPING.get(federate_type, DEFAULT_FEDERATE_MAPPING)


def generate_cst_config(tree: Tree, general_cfg: dict) -> None:
    """Generate CST (CoSim Toolbox) configuration and write output files.

    Args:
        tree: Tree structure with federation configuration.
        general_cfg: General configuration dictionary.
    """
    print("--- Generating CST Configuration ---")

    name = general_cfg.get("name", "GridLock")
    federation = FederationConfig(
        f"{name}Scenario", f"{name}Analysis", f"{name}Federation", True, "json", "csv"
    )

    for node in tree.all_nodes():
        data = node.data
        node_type = data.get("type")

        if not node_type or node_type == "empty":
            continue

        time_step = general_cfg.get("time_step", 1.0)
        fed = FederateConfig(node.identifier, period=time_step)

        federation.add_federate_config(fed)

        mapped = map_params_to_type(node_type)
        fed.config("image", mapped["image"])
        fed.config("command", mapped["command"])
        fed.config("federate_type", node_type)

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
    try:
        federation.write_config(start_str, end_str)
        DockerRunner.define_yaml(federation.scenario_name, use_meta_db="json")
        print("Success: Federation configuration and docker-compose.yml generated.")
    except Exception as e:
        print(f"Error generating config: {e}")
        traceback.print_exc()
