import traceback
import json
from pathlib import Path
from treelib import Tree
from cosim_toolbox.sims import (
    FederationConfig,
    FederateConfig,
    DockerRunner,
    HelicsPubGroup,
    HelicsSubGroup,
)
from monkeypatch import apply_monkeypatches


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
            "command": "python3 main.py",
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


def normalize_federation_keys(federation_name: str) -> None:
    """Post-process federation JSON to replace dots with slashes in all HELICS keys.        
    Args:
        federation_name: Name of the federation file to process.
    """
    federation_path = Path("meta_store/federations") / f"{federation_name}.json"        
    if not federation_path.exists():
        print(f"Warning: Federation file {federation_path} not found for normalization")
        return    
    # Read the federation config
    with open(federation_path, "r") as f:
        config = json.load(f)    
    # Process all federates
    if "federation" in config:
        for _fed_name, fed_config in config["federation"].items():
            if "HELICS_config" in fed_config:
                helics_cfg = fed_config["HELICS_config"]

                for pub in helics_cfg.get("publications", []):
                    if "key" in pub:
                        pub["key"] = pub["key"].replace(".", "/")

                for sub in helics_cfg.get("subscriptions", []):
                    if "key" in sub:
                        sub["key"] = sub["key"].replace(".", "/")

    with open(federation_path, "w") as f:
        json.dump(config, f, indent=2)

    print(f"Normalized HELICS keys in {federation_path}")


def discover_grid_federates(
    grid_id: str,
    meta_store_path: str = "meta_store",
) -> list[str]:
    """Discover resolved grid federate names from the CST metadata store.

    The infdb data-setup step writes each resolved pandapower net into
    the ``custom_metadata`` collection, keyed by federate name.  This
    function scans that collection for entries whose ``grid_id`` matches
    the experiment's grid identifier.

    Args:
        grid_id: Grid identifier from experiment YAML (``federation.id``).
        meta_store_path: Path to the meta_store directory.

    Returns:
        Sorted list of federate name strings (e.g. ["lv-grid_91301_0"]).
    """
    custom_dir = Path(meta_store_path) / "custom_metadata"
    if not custom_dir.exists():
        return []

    federate_names: list[str] = []
    for json_file in sorted(custom_dir.glob("*.json")):
        try:
            with open(json_file) as f:
                data = json.load(f)
            if data.get("grid_id") == grid_id:
                federate_names.append(json_file.stem)
        except (json.JSONDecodeError, KeyError):
            continue

    return federate_names


# ---------------------------------------------------------------------------
# Pub / sub wiring helpers
# ---------------------------------------------------------------------------

def _add_group(
    federation: FederationConfig,
    group_name: str,
    pub_fed: str,
    sub_fed: str,
    dtype: str = "double",
    unit: str = "W",
) -> None:
    """Register one pub→sub group via CST.

    CST prepends ``pub_fed/`` to *group_name* to form the full
    HELICS key.  Dots in federate names are later normalised to
    slashes by :func:`normalize_federation_keys`.
    """
    key_format = {
        "src": {"from_fed": pub_fed, "keys": ["", ""], "indices": []},
        "des": [
            {
                "from_fed": pub_fed,
                "to_fed": sub_fed,
                "keys": ["", ""],
                "indices": [],
            }
        ],
    }
    federation.add_group(group_name, dtype, key_format, unit=unit, globl=True)


def _wire_grid_child(
    federation: FederationConfig,
    grid_fed_name: str,
    child_fed_name: str,
    child_local_id: str,
    child_class: str,
) -> list[str]:
    """Wire pub/sub between a grid federate and one of its children.

    Returns the list of publication keys owned by the *child*
    (needed for player file generation).
    """
    child_pub_keys: list[str] = []

    # Grid publishes voltage → child subscribes
    _add_group(
        federation,
        f"{child_local_id}/voltage",
        pub_fed=grid_fed_name,
        sub_fed=child_fed_name,
        dtype="double",
        unit="V",
    )

    if child_class in ("house", "load", "battery", "pv", "grid"):
        # Child publishes active_power → grid subscribes
        _add_group(
            federation,
            "active_power",
            pub_fed=child_fed_name,
            sub_fed=grid_fed_name,
            dtype="double",
            unit="W",
        )
        # After dot→slash normalisation the full key becomes
        # <grid_fed_name>/<child_local_id>/active_power
        child_pub_keys.append(
            f"{child_fed_name.replace('.', '/')}/active_power"
        )

        # Child publishes reactive_power → grid subscribes
        _add_group(
            federation,
            "reactive_power",
            pub_fed=child_fed_name,
            sub_fed=grid_fed_name,
            dtype="double",
            unit="VAr",
        )
        child_pub_keys.append(
            f"{child_fed_name.replace('.', '/')}/reactive_power"
        )

    if child_class == "house":
        # Grid publishes control → house subscribes
        _add_group(
            federation,
            f"{child_local_id}/control",
            pub_fed=grid_fed_name,
            sub_fed=child_fed_name,
            dtype="string",
            unit="json",
        )

    return child_pub_keys


def _resolve_timeseries_path(child_data: dict) -> str:
    """Resolve the timeseries file path for a load federate.

    Checks whether ``electrical_load`` in the child config points
    to a CSV file directly or a standard profile type.  For profile
    types the infdb step is expected to provide the data; for
    testing a default CSV is used.

    Args:
        child_data: Tree node data dict for the child federate.

    Returns:
        Container-relative path to the timeseries CSV, or empty string.
    """
    electrical_load = child_data.get("electrical_load")
    if not electrical_load:
        return ""

    # Direct CSV reference – assume it's in the data/input volume
    if electrical_load.endswith(".csv"):
        return f"/data/input/{electrical_load}"

    # Standard profile type (H0, H25, …) – use default test file
    # TODO: Replace with infdb-derived timeseries once available
    return "/data/input/building_timeseries.csv"


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def load(tree: Tree, general_cfg: dict) -> None:
    """Generate CST federation configuration and docker-compose.

    For location-based grids the ``custom_metadata`` entries written by
    the infdb data-setup step are scanned so that one grid *and* its
    child federates are instantiated per resolved pandapower net.

    Args:
        tree: Transformed tree with class/config data.
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

    # Nodes handled via location expansion – skip in the generic loop
    handled_nodes: set[str] = set()

    # ---- Phase 1: location-based grid expansion --------------------------
    for node in tree.all_nodes():
        if node.data.get("class") != "grid":
            continue
        if not node.data.get("location"):
            continue

        grid_tree_id = node.identifier
        grid_fed_names = discover_grid_federates(grid_tree_id)
        if not grid_fed_names:
            print(
                f"Warning: Grid '{grid_tree_id}' uses location queries but "
                f"no custom_metadata entries found – was the infdb step executed?"
            )
            continue

        children = tree.children(grid_tree_id)
        handled_nodes.add(grid_tree_id)
        for child in children:
            handled_nodes.add(child.identifier)

        grid_mapped = map_params_to_class("grid")

        for fed_name in grid_fed_names:
            # -- grid federate ------------------------------------------------
            fed = FederateConfig(fed_name, period=time_step)
            federation.add_federate_config(fed)
            cmd = (
                f"{grid_mapped['command']} "
                f"--scenario {name}Scenario --federate_name {fed_name}"
            )
            fed.config("image", grid_mapped["image"])
            fed.config("command", cmd)
            fed.config("federate_type", "value")
            print(f"Added grid federate: {fed_name}")

            # -- child federates (load, house, …) ----------------------------
            for child in children:
                child_data = child.data
                child_class = child_data.get("class")
                child_mapped = map_params_to_class(child_class)
                child_local_id = child.identifier.split(".")[-1]
                child_fed_name = f"{fed_name}.{child_local_id}"

                child_fed = FederateConfig(child_fed_name, period=time_step)
                federation.add_federate_config(child_fed)
                child_fed.config("image", child_mapped["image"])
                child_fed.config("federate_type", "value")

                # Wire pub/sub between grid and this child
                _wire_grid_child(
                    federation, fed_name, child_fed_name,
                    child_local_id, child_class,
                )

                # Build command – all child classes are now CST federates
                child_cmd = (
                    f"{child_mapped['command']} "
                    f"--scenario {name}Scenario "
                    f"--federate_name {child_fed_name}"
                )

                # For load federates, resolve the timeseries file path
                if child_class == "load":
                    ts_path = _resolve_timeseries_path(child_data)
                    if ts_path:
                        child_cmd += f" --timeseries {ts_path}"

                child_fed.config("command", child_cmd)
                print(f"  Added child federate: {child_fed_name} ({child_class})")

    # ---- Phase 2: non-location nodes (layout grids, standalone, …) -------
    for node in tree.all_nodes():
        if node.identifier in handled_nodes:
            continue

        data = node.data
        node_class = data.get("class")
        node_type = data.get("type")

        if not node_type or node_type == "empty":
            continue

        mapped = map_params_to_class(node_class)

        if node_class == "grid":
            layout = data.get("layout")
            if layout:
                mapped["command"] += f" --grid_file {layout}"
            else:
                print(
                    f"Warning: No layout or location for grid {node.identifier}"
                )

        fed = FederateConfig(node.identifier, period=time_step)
        federation.add_federate_config(fed)
        fed.config("image", mapped["image"])
        fed.config("command", mapped["command"])
        fed.config("federate_type", node_type)

    # Build pub/sub using add_group with proper src/des structure
    # Map topics to their publishers and subscribers
    topic_map: dict[str, dict] = {} # topic -> {"publishers": [fed_names], "subscribers": [fed_names], "unit": str, "dtype": str}
    
    for node in tree.all_nodes():
        data = node.data
        node_type = data.get("type")
        
        if not node_type or node_type == "empty":
            continue
            
        # Track publications
        for topic, unit in data.get("publications", {}).items():
            dtype = "string" if unit == "json" else "double"
            if topic not in topic_map:
                topic_map[topic] = {"publishers": [], "subscribers": [], "unit": unit, "dtype": dtype}
            topic_map[topic]["publishers"].append(node.identifier)
        
        # Track subscriptions
        for topic, unit in data.get("subscriptions", {}).items():
            dtype = "string" if unit == "json" else "double"
            if topic not in topic_map:
                topic_map[topic] = {"publishers": [], "subscribers": [], "unit": unit, "dtype": dtype}
            topic_map[topic]["subscribers"].append(node.identifier)
    
    # For each unique topic, create add_group calls
    for topic, info in topic_map.items():
        publishers = info["publishers"]
        subscribers = info["subscribers"]
        unit = info["unit"]
        dtype = info["dtype"]
        
        if not publishers:
            continue
            
        # For each publisher, create a group with all its subscribers as destinations
        for pub_fed in publishers:
            # Build the key_format dict matching the user's example pattern
            key_format = {
                "src": {
                    "from_fed": pub_fed,
                    "keys": ["", ""],
                    "indices": []
                },
                "des": []
            }
            
            # Add all subscribers as destinations
            for sub_fed in subscribers:
                key_format["des"].append({
                    "from_fed": pub_fed,
                    "to_fed": sub_fed,
                    "keys": ["", ""],
                    "indices": []
                })
            
            # CST will prepend from_fed/ to the group name, so strip it from topic to avoid duplication
            # Replace all dots with slashes throughout for consistent path separators
            # First, normalize the topic to use slashes
            normalized_topic = topic.replace(".", "/")
            normalized_pub_fed = pub_fed.replace(".", "/")
            
            group_name = normalized_topic
            if normalized_topic.startswith(normalized_pub_fed + "/"):
                # Strip "normalized_pub_fed/" to avoid duplication
                group_name = normalized_topic[len(normalized_pub_fed) + 1:]
            
            # Call add_group - CST will prepend from_fed/ (which still has dots, but we've normalized the rest)
            federation.add_group(group_name, dtype, key_format, unit=unit, globl=True)
    
    # Define I/O to finalize all group definitions
    federation.define_io()
    
    start_str = general_cfg["start_time"]
    end_str = general_cfg["end_time"]

    print("Generating configuration definitions...")

    # Apply monkey patches before generating
    apply_monkeypatches()

    try:
        federation.write_config(start_str, end_str)
        
        # Normalize dots to slashes in all HELICS keys
        normalize_federation_keys(federation.federation_name)
        
        DockerRunner.define_yaml(federation.scenario_name, use_meta_db="json")
        print("Success: Federation configuration and docker-compose.yml generated.")
    except Exception as e:
        print(f"Error generating config: {e}")
        traceback.print_exc()
