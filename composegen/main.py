import argparse
import copy
import traceback
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
import yaml
from treelib import Tree

from cosim_toolbox.sims import DockerRunner, FederateConfig, FederationConfig

# Constants
DEFAULT_CONFIG_PATH = Path("../config/experiment.yml")
FALLBACK_CONFIG_PATH = Path("../config/MV-LV.yml")
DATA_INPUT_PATH = Path("../data/input")
DEFAULT_START_TIME = "2023-01-01T00:00:00"

FEDERATE_TYPE_MAPPING = {
    "grid": {
        "image": "gridlock-grid:latest",
        "command": "python3 main.py",
    },
    "house": {
        "image": "gridlock-house:latest",
        "command": "python3 main.py",
    },
    "recorder": {
        "image": "gridlock-recorder:latest",
        "command": "helics_recorder",
    },
    "pv": {"image": "gridlock-house:latest", "command": "python3 main.py"},
    "battery": {"image": "gridlock-house:latest", "command": "python3 main.py"},
    "hems": {"image": "gridlock-house:latest", "command": "python3 main.py"},
}
DEFAULT_FEDERATE_MAPPING = {"image": "cosim-cst:latest", "command": "python3 main.py"}


def add_to_tree(tree: Tree, node_dict: dict, parent: Optional[str] = None) -> None:
    """Recursively add nodes from config dict to tree structure."""
    node_id = f"{parent}/{node_dict.get('id')}" if parent else node_dict.get("id")
    node_tag = node_dict.get("name")
    node_config = node_dict.get("config", {}).copy()
    node_config["type"] = node_dict.get("type")
    # Convert empty string values in node_config to None
    node_config = {k: (v if v != "" else None) for k, v in node_config.items()}

    # Create node with config data
    tree.create_node(
        tag=f"{node_tag}", identifier=node_id, parent=parent, data=node_config
    )

    for sub in node_dict.get("sub_federates", []):
        add_to_tree(tree, sub, parent=node_id)


def expand_grid_nodes(tree: Tree, grid_nodes: dict) -> Tree:
    """
    Expands the tree by placing federates onto grid buses according to 'placement' rules.
    - Removes original definition nodes from the grid parent.
    - Replicates subtrees for 'list' or 'fill' placements.
    - Ensures unique IDs for all placed nodes.
    """

    for grid_id, buses in grid_nodes.items():
        if not tree.contains(grid_id):
            continue

        # If no buses are defined for this grid, skip
        if not buses:
            continue

        # 1. Classify logic and extract prototypes
        # We process current children to build placement plan
        explicit_placements = {}  # Map[bus_id] -> template_subtree
        fill_templates = []  # List[template_subtree]

        # Get current children nodes to iterate over
        children = tree.children(grid_id)

        for child in children:
            placement = child.data.get("placement")

            # Extract the full subtree (template) for this federate
            # We use remove_subtree to detach it from the main tree immediately
            # giving us a clean slate to paste back onto.
            template_subtree = tree.remove_subtree(child.identifier)

            if isinstance(placement, int):
                placement = [placement]  # Normalize to list for uniform processing

            if isinstance(placement, list):
                for p in placement:
                    if p in explicit_placements:
                        raise ValueError(
                            f"Configuration Error: Bus {p} in grid '{grid_id}' is claimed by multiple federates."
                        )
                    if p not in buses:
                        raise ValueError(
                            f"Configuration Error: Federate '{child.tag}' placed on bus {p} which does not exist in grid '{grid_id}'."
                        )
                    # We store the same template reference; we must deepcopy when pasting
                    explicit_placements[p] = template_subtree

            elif placement == "fill":
                fill_templates.append(template_subtree)

            else:
                # If placement is None/Empty in config, we treat it simply as not having a specific spot.
                # Since we stripped the tree, we discard it unless specific logic is needed.
                raise ValueError(
                    f"Configuration Error: Federate '{child.tag}' does not have a valid placement in grid '{grid_id}'."
                )

        # 2. Re-populate the grid node with concrete instances per bus
        for bus in buses:
            # Determine which template to use
            template = None

            if bus in explicit_placements:
                template = explicit_placements[bus]
            elif fill_templates:
                # Use the first fill template available (simple logic)
                template = fill_templates[0]

            new_root_id = f"{grid_id}/bus_{bus}"

            if template:
                # We must modify the subtree to have unique IDs before pasting
                # Deepcopy ensures we don't mutate the template for other buses
                subtree_to_paste = copy.deepcopy(template)

                # Update IDs inside the subtree to be unique
                # Old Root ID -> New Root ID
                old_root_id = subtree_to_paste.root
                root_node = subtree_to_paste[old_root_id]

                # We need to systematically rename everything in this subtree
                # Mapping: old_id -> new_id
                # Strategy: Append grid and bus info to ensure global uniqueness

                # Logic: Rename the root specifically to the requested format
                # Rename descendants to strictly unique IDs

                # 1. Update root
                root_node.identifier = new_root_id
                # Update data to reflect actual placement
                root_node.data["placement"] = bus
                subtree_to_paste.update_node(old_root_id, identifier=new_root_id)

                # 2. Update all other nodes in subtree
                # BFS/DFS transversal to rename. Note: changing IDs while iterating needs care.
                # treelib doesn't support bulk re-id easily, so we iterate keys
                for node_id in list(subtree_to_paste.nodes.keys()):
                    if node_id == new_root_id:
                        continue  # Already handled root

                    # Generate unique ID by replacing the old root prefix with the new root ID
                    if node_id.startswith(old_root_id):
                        new_sub_id = node_id.replace(old_root_id, new_root_id, 1)
                    else:
                        new_sub_id = f"{new_root_id}/{node_id}"

                    subtree_to_paste.update_node(node_id, identifier=new_sub_id)

                # Paste the prepared subtree back into the main tree
                tree.paste(grid_id, subtree_to_paste)

            else:
                # "if placement is empty or none make a node of type 'empty'"
                # (And no fill template was available)
                tree.create_node(
                    tag=f"Empty Slot {bus}",
                    identifier=new_root_id,
                    parent=grid_id,
                    data={"type": "empty", "bus": bus, "placement": bus},
                )

    return tree


def add_pub_sub(node, topic: str, unit: str, type: str = "publication") -> None:
    """
    Helper to add publication or subscription to node data structure.

    Args:
        node: Tree node to add pub/sub to
        topic: Topic name
        unit: Unit of measurement
        type: Either "publication" or "subscription"
    """
    key = "publications" if type == "publication" else "subscriptions"
    if key not in node.data:
        node.data[key] = {}
    node.data[key][topic] = unit


def map_params_to_type(federate_type: str) -> dict:
    """
    Maps internal federate types to Docker images and commands.

    Args:
        federate_type: Type of federate (grid, house, pv, etc.)

    Returns:
        Dictionary with image and command keys
    """
    return FEDERATE_TYPE_MAPPING.get(federate_type, DEFAULT_FEDERATE_MAPPING)


def validate_node(node, validation_errors: List[str]) -> None:
    """
    Validate a single node's configuration based on its type.

    Args:
        node: Tree node to validate
        validation_errors: List to append errors to
    """
    data = node.data
    node_type = data.get("type")

    if not node_type:
        validation_errors.append(
            f"[Structure] Node '{node.tag}' ({node.identifier}) is missing a 'type' definition."
        )
        return

    match node_type:
        case "grid":
            layout = data.get("layout")
            location = data.get("location")
            if not layout and not location:
                validation_errors.append(
                    f"[Grid] Node '{node.tag}' ({node.identifier}) must specify either 'layout' or 'location'."
                )
            if layout and location:
                validation_errors.append(
                    f"[Grid] Node '{node.tag}' ({node.identifier}) specifies both 'layout' and 'location'."
                )

        case "load":
            if not data.get("electrical_load") and not data.get("heat_load"):
                validation_errors.append(
                    f"[Load] Node '{node.tag}' ({node.identifier}) requires 'electrical_load' or 'heat_load'."
                )

        case "house":
            if not data.get("model"):
                validation_errors.append(
                    f"[House] Node '{node.tag}' ({node.identifier}) requires a 'model' definition."
                )

        case "pv":
            if not data.get("max_production"):
                validation_errors.append(
                    f"[PV] Node '{node.tag}' ({node.identifier}) requires 'max_production' (profile path)."
                )
            if not data.get("capacity"):
                validation_errors.append(
                    f"[PV] Node '{node.tag}' ({node.identifier}) requires 'capacity' definition."
                )

        case "battery":
            if not data.get("capacity"):
                validation_errors.append(
                    f"[Battery] Node '{node.tag}' ({node.identifier}) requires 'capacity' definition."
                )
            if not data.get("power"):
                validation_errors.append(
                    f"[Battery] Node '{node.tag}' ({node.identifier}) requires 'power' definition."
                )

        case "hems":
            if not data.get("control_strategy"):
                validation_errors.append(
                    f"[HEMS] Node '{node.tag}' ({node.identifier}) requires 'control_strategy' definition."
                )


def validate_tree(tree: Tree) -> None:
    """
    Validate all nodes in the tree and raise error if any validation fails.

    Args:
        tree: Tree structure to validate

    Raises:
        ValueError: If validation errors are found
    """
    validation_errors = []
    for node in tree.all_nodes():
        validate_node(node, validation_errors)

    if validation_errors:
        print("Configuration Invalid:")
        for error in validation_errors:
            print(f" - {error}")
        raise ValueError("Configuration validation failed due to errors listed above.")


def extract_grid_nodes(tree: Tree) -> Dict[str, Optional[List[int]]]:
    """
    Extract grid nodes and their bus information from tree.

    Args:
        tree: Tree structure containing grid nodes

    Returns:
        Dictionary mapping grid IDs to lists of bus numbers

    Raises:
        ValueError: If grid configuration is invalid
    """
    grid_ids = [
        node_id
        for node_id in tree.expand_tree(filter=lambda x: x.data["type"] == "grid")
    ]
    grid_nodes = {}

    for grid_id in grid_ids:
        grid_node = tree.get_node(grid_id)
        layout = grid_node.data.get("layout")
        location = grid_node.data.get("location")

        if layout is None:
            if location is None:
                raise ValueError(f"No layout or location specified for grid {grid_id}")
            else:
                # TODO: infDB.load(location)
                grid_nodes[grid_id] = None
                print(f"Loading layout from infDB for {grid_id} at location {location}")
        else:
            if location is not None:
                raise ValueError(
                    f"Both layout and location specified for grid {grid_id}. Please specify only one."
                )
            else:
                layout_path = DATA_INPUT_PATH / layout
                data_df = pd.read_excel(layout_path, sheet_name="load")
                grid_nodes[grid_id] = data_df["bus"].tolist()
                print(f"Loaded layout from file for {grid_id} from {layout_path}")

    return grid_nodes


def process_general_config(general_cfg: dict) -> None:
    """
    Process and validate general configuration settings.

    Args:
        general_cfg: General configuration dictionary

    Raises:
        ValueError: If required fields are missing or invalid
    """
    required_fields = ["end_time"]
    for field in required_fields:
        if field not in general_cfg or general_cfg[field] is None:
            raise ValueError(f"[General] Missing required field '{field}'.")

    if "start_time" not in general_cfg or general_cfg["start_time"] is None:
        general_cfg["start_time"] = DEFAULT_START_TIME

    start_time = general_cfg["start_time"]
    end_time = general_cfg["end_time"]
    try:
        start_ts = pd.Timestamp(start_time)
        if isinstance(end_time, (int, float)):
            end_ts = start_ts + pd.Timedelta(seconds=end_time)
            general_cfg["end_time"] = end_ts.isoformat()
        elif isinstance(end_time, str):
            pd.Timestamp(end_time)
        else:
            raise ValueError(f"End time format not recognized: {end_time}")
    except Exception as e:
        raise ValueError(f"Timestamp logic failed: {e}")


def setup_publications_subscriptions(tree: Tree) -> None:
    """
    Add publication and subscription topics to all nodes based on hierarchy.

    Args:
        tree: Tree structure to add pub/sub topics to
    """
    # Clean up previous runs if any
    for node in tree.all_nodes():
        if "publications" in node.data:
            del node.data["publications"]
        if "subscriptions" in node.data:
            del node.data["subscriptions"]

    for parent_node in tree.all_nodes():
        parent_type = parent_node.data.get("type")
        children = tree.children(parent_node.identifier)

        for child_node in children:
            child_type = child_node.data.get("type")

            if parent_type == "grid":
                voltage_topic = f"{child_node.identifier}/voltage"
                add_pub_sub(parent_node, voltage_topic, "V", "publication")
                add_pub_sub(child_node, voltage_topic, "V", "subscription")

                if child_type in ["house", "load", "battery", "pv", "grid"]:
                    p_topic = f"{child_node.identifier}/active_power"
                    q_topic = f"{child_node.identifier}/reactive_power"
                    add_pub_sub(parent_node, p_topic, "W", "subscription")
                    add_pub_sub(parent_node, q_topic, "VAr", "subscription")
                    add_pub_sub(child_node, p_topic, "W", "publication")
                    add_pub_sub(child_node, q_topic, "VAr", "publication")

                if child_type == "house":
                    control_topic = f"{child_node.identifier}/control"
                    add_pub_sub(parent_node, control_topic, "json", "publication")
                    add_pub_sub(child_node, control_topic, "json", "subscription")

            elif parent_type == "house":
                if child_type in ["pv", "battery", "hems"]:
                    p_topic = f"{child_node.identifier}/active_power"
                    q_topic = f"{child_node.identifier}/reactive_power"
                    v_topic = f"{child_node.identifier}/voltage"

                    add_pub_sub(parent_node, p_topic, "W", "subscription")
                    add_pub_sub(parent_node, q_topic, "VAr", "subscription")
                    add_pub_sub(parent_node, v_topic, "V", "subscription")

                    add_pub_sub(child_node, p_topic, "W", "publication")
                    add_pub_sub(child_node, q_topic, "VAr", "publication")
                    add_pub_sub(child_node, v_topic, "V", "publication")

                    control_topic = f"{child_node.identifier}/control"
                    add_pub_sub(parent_node, control_topic, "json", "publication")
                    add_pub_sub(child_node, control_topic, "json", "subscription")


def load_config_file(config_path: Optional[Path] = None) -> dict:
    """
    Load configuration from YAML file.

    Args:
        config_path: Path to configuration file. If None, uses default paths.

    Returns:
        Configuration dictionary
    """
    if config_path is None:
        config_path = FALLBACK_CONFIG_PATH
        if not config_path.exists():
            config_path = DEFAULT_CONFIG_PATH

    print(f"Loading configuration from {config_path}")
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


def generate_cst_config(tree: Tree, general_cfg: dict) -> None:
    """
    Generate CST (CoSim Toolbox) configuration and write output files.

    Args:
        tree: Tree structure with federation configuration
        general_cfg: General configuration dictionary
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


def main() -> None:
    """Main entry point for the composegen configuration generator."""
    parser = argparse.ArgumentParser(description="Generate federation configuration.")
    parser.add_argument(
        "config_file", nargs="?", help="Path to the experiment configuration YAML file"
    )
    args = parser.parse_args()

    # --- ETL: EXTRACT ---
    print("--- Extracting Configuration ---")
    tree = Tree()

    config_path = Path(args.config_file) if args.config_file else None
    cfg = load_config_file(config_path)

    add_to_tree(tree, cfg["federation"])

    # Extract grid nodes
    grid_nodes = extract_grid_nodes(tree)

    # --- ETL: TRANSFORM ---
    print("--- Transforming Configuration ---")

    # Validate configuration
    validate_tree(tree)

    # Expand grid nodes based on placement rules
    expand_grid_nodes(tree, grid_nodes)

    # Process general configuration
    general_cfg = cfg.get("general", {})
    process_general_config(general_cfg)

    # Add publications and subscriptions
    setup_publications_subscriptions(tree)

    # --- ETL: LOAD / GENERATE ---
    generate_cst_config(tree, general_cfg)


if __name__ == "__main__":
    main()
