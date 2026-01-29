"""Configuration validation functions.

This module contains validation logic for federation configuration nodes,
following Google Python Style Guide.
"""

from typing import List

from treelib import Tree


def validate_node(node, validation_errors: List[str]) -> None:
    """Validate a single node's configuration based on its type.

    Args:
        node: Tree node to validate.
        validation_errors: List to append errors to.
    """
    data = node.data
    node_type = data.get("type")

    if not node_type:
        validation_errors.append(
            f"[Structure] Node '{node.tag}' ({node.identifier}) is missing "
            f"a 'type' definition."
        )
        return

    match node_type:
        case "grid":
            layout = data.get("layout")
            location = data.get("location")
            if not layout and not location:
                validation_errors.append(
                    f"[Grid] Node '{node.tag}' ({node.identifier}) must specify "
                    f"either 'layout' or 'location'."
                )
            if layout and location:
                validation_errors.append(
                    f"[Grid] Node '{node.tag}' ({node.identifier}) specifies both "
                    f"'layout' and 'location'."
                )

        case "load":
            if not data.get("electrical_load") and not data.get("heat_load"):
                validation_errors.append(
                    f"[Load] Node '{node.tag}' ({node.identifier}) requires "
                    f"'electrical_load' or 'heat_load'."
                )

        case "house":
            if not data.get("model"):
                validation_errors.append(
                    f"[House] Node '{node.tag}' ({node.identifier}) requires "
                    f"a 'model' definition."
                )

        case "pv":
            if not data.get("max_production"):
                validation_errors.append(
                    f"[PV] Node '{node.tag}' ({node.identifier}) requires "
                    f"'max_production' (profile path)."
                )
            if not data.get("capacity"):
                validation_errors.append(
                    f"[PV] Node '{node.tag}' ({node.identifier}) requires "
                    f"'capacity' definition."
                )

        case "battery":
            if not data.get("capacity"):
                validation_errors.append(
                    f"[Battery] Node '{node.tag}' ({node.identifier}) requires "
                    f"'capacity' definition."
                )
            if not data.get("power"):
                validation_errors.append(
                    f"[Battery] Node '{node.tag}' ({node.identifier}) requires "
                    f"'power' definition."
                )

        case "hems":
            if not data.get("control_strategy"):
                validation_errors.append(
                    f"[HEMS] Node '{node.tag}' ({node.identifier}) requires "
                    f"'control_strategy' definition."
                )


def validate_tree(tree: Tree) -> None:
    """Validate all nodes in the tree.

    Args:
        tree: Tree structure to validate.

    Raises:
        ValueError: If validation errors are found.
    """
    validation_errors = []
    for node in tree.all_nodes():
        validate_node(node, validation_errors)

    if validation_errors:
        print("Configuration Invalid:")
        for error in validation_errors:
            print(f" - {error}")
        raise ValueError("Configuration validation failed due to errors listed above.")
