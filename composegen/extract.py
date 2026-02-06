import yaml
import pandas as pd
from pathlib import Path
from treelib import Tree


def add_to_tree(tree: Tree, node_dict: dict, parent: str = None) -> None:
    """Recursively add nodes from config dict to tree structure."""
    node_id = f"{parent}.{node_dict.get('id')}" if parent else node_dict.get("id")
    node_tag = node_dict.get("name")
    node_config = node_dict.get("config", {}).copy()
    node_config["class"] = node_dict.get("class")
    node_config["type"] = "value"

    # Convert empty string values in node_config to None
    node_config = {k: (v if v != "" else None) for k, v in node_config.items()}

    # Create node with config data
    tree.create_node(
        tag=f"{node_tag}",
        identifier=node_id,
        parent=parent,
        data=node_config,
    )

    for sub in node_dict.get("sub_federates", []):
        add_to_tree(tree, sub, parent=node_id)


def extract(config_path: Path) -> tuple[dict, Tree, dict]:
    """Extract configuration from YAML and grid layout files.

    Args:
        config_path: Path to the experiment configuration YAML file.

    Returns:
        Tuple of (full config dict, populated tree, grid_nodes dict).
    """
    print(f"Loading configuration from {config_path}")
    with open(config_path, "r") as f:
        cfg = yaml.safe_load(f)

    tree = Tree()
    add_to_tree(tree, cfg["federation"])

    # Extract grid nodes
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
                raise ValueError(
                    f"No layout or location specified for grid {grid_id}"
                )
            else:
                # TODO: infDB.load(location)
                grid_nodes[grid_id] = None
                print(
                    f"Loading layout from infDB for {grid_id} at location {location}"
                )
        else:
            if location is not None:
                raise ValueError(
                    f"Both layout and location specified for grid {grid_id}. Please specify only one."
                )
            else:
                layout_path = Path("../data/input") / layout
                # Assuming pandas is available
                data_df = pd.read_excel(layout_path, sheet_name="load")
                grid_nodes[grid_id] = data_df["bus"].tolist()
                print(f"Loaded layout from file for {grid_id} from {layout_path}")

    return cfg, tree, grid_nodes
