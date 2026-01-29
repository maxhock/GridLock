"""Grid-specific operations for federation configuration.

This module handles extraction and processing of grid node information,
following Google Python Style Guide.
"""

from typing import Dict, List, Optional

import pandas as pd
from treelib import Tree

from .config import DATA_INPUT_PATH


def extract_grid_nodes(tree: Tree) -> Dict[str, Optional[List[int]]]:
    """Extract grid nodes and their bus information from tree.

    Args:
        tree: Tree structure containing grid nodes.

    Returns:
        Dictionary mapping grid IDs to lists of bus numbers.

    Raises:
        ValueError: If grid configuration is invalid.
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
                    f"Both layout and location specified for grid {grid_id}. "
                    f"Please specify only one."
                )
            else:
                layout_path = DATA_INPUT_PATH / layout
                data_df = pd.read_excel(layout_path, sheet_name="load")
                grid_nodes[grid_id] = data_df["bus"].tolist()
                print(f"Loaded layout from file for {grid_id} from {layout_path}")

    return grid_nodes
