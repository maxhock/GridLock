"""Publication and subscription setup for HELICS federates.

This module handles setting up publication and subscription topics between
federates in the HELICS federation, following Google Python Style Guide.
"""

from treelib import Tree


def add_pub_sub(node, topic: str, unit: str, type: str = "publication") -> None:
    """Add publication or subscription to node data structure.

    Args:
        node: Tree node to add pub/sub to.
        topic: Topic name.
        unit: Unit of measurement.
        type: Either "publication" or "subscription".

    Raises:
        ValueError: If type is not "publication" or "subscription".
    """
    if type not in ["publication", "subscription"]:
        raise ValueError(
            f"Invalid type '{type}'. Must be 'publication' or 'subscription'"
        )

    key = "publications" if type == "publication" else "subscriptions"
    if key not in node.data:
        node.data[key] = {}
    node.data[key][topic] = unit


def setup_publications_subscriptions(tree: Tree) -> None:
    """Add publication and subscription topics to all nodes.

    Sets up pub/sub topics based on the hierarchy and node types.

    Args:
        tree: Tree structure to add pub/sub topics to.
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
