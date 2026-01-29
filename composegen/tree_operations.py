"""Tree structure operations for federation configuration.

This module handles building and manipulating the tree structure that
represents the federation hierarchy, following Google Python Style Guide.
"""

import copy
from typing import Optional

from treelib import Tree


def add_to_tree(tree: Tree, node_dict: dict, parent: Optional[str] = None) -> None:
    """Recursively add nodes from config dict to tree structure.

    Args:
        tree: Tree structure to add nodes to.
        node_dict: Dictionary containing node configuration.
        parent: Optional parent node identifier.
    """
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


def expand_grid_nodes(tree: Tree, grid_nodes: dict) -> None:
    """Expand tree by placing federates onto grid buses.

    Places federates onto grid buses according to 'placement' rules:
    - Removes original definition nodes from the grid parent
    - Replicates subtrees for 'list' or 'fill' placements
    - Ensures unique IDs for all placed nodes

    Args:
        tree: Tree structure to modify in-place.
        grid_nodes: Dictionary mapping grid IDs to lists of bus numbers.

    Raises:
        ValueError: If placement configuration is invalid.
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
                            f"Configuration Error: Bus {p} in grid '{grid_id}' "
                            f"is claimed by multiple federates."
                        )
                    if p not in buses:
                        raise ValueError(
                            f"Configuration Error: Federate '{child.tag}' placed "
                            f"on bus {p} which does not exist in grid '{grid_id}'."
                        )
                    # We store the same template reference; we must deepcopy when pasting
                    explicit_placements[p] = template_subtree

            elif placement == "fill":
                fill_templates.append(template_subtree)

            else:
                # If placement is None/Empty in config, we treat it simply as
                # not having a specific spot.
                # Since we stripped the tree, we discard it unless specific logic is needed.
                raise ValueError(
                    f"Configuration Error: Federate '{child.tag}' does not have "
                    f"a valid placement in grid '{grid_id}'."
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
                # BFS/DFS transversal to rename. Note: changing IDs while iterating
                # needs care. treelib doesn't support bulk re-id easily, so we iterate keys
                for node_id in list(subtree_to_paste.nodes.keys()):
                    if node_id == new_root_id:
                        continue  # Already handled root

                    # Generate unique ID by replacing the old root prefix with
                    # the new root ID
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
