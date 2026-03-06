import traceback
import json
from pathlib import Path
from treelib import Tree
from cosim_toolbox.dbms import create_metadata_manager
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


def _create_metadata_manager(
    use_meta_db: str,
    meta_store_path: str = "meta_store",
):
    """Create CST metadata manager with backend-specific options."""
    kwargs = {"backend": use_meta_db}
    if use_meta_db == "json":
        kwargs["location"] = meta_store_path
    return create_metadata_manager(**kwargs)


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


def normalize_federation_keys(
    federation_name: str,
    use_meta_db: str,
    meta_store_path: str = "meta_store",
) -> None:
    """Normalize HELICS keys in federation metadata for json or mongo backend."""
    with _create_metadata_manager(use_meta_db, meta_store_path) as mgr:
        config = mgr.read_federation(federation_name)
        if not config:
            print(
                f"Warning: Federation '{federation_name}' not found "
                f"in metadata backend '{use_meta_db}'"
            )
            return

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

        mgr.write_federation(federation_name, config, overwrite=True)

    print(
        f"Normalized HELICS keys in federation '{federation_name}' "
        f"(backend={use_meta_db})"
    )


def discover_grid_federates(
    grid_id: str,
    use_meta_db: str,
    meta_store_path: str = "meta_store",
    location: list[dict] | None = None,
) -> list[str]:
    """Derive grid federate names from the experiment YAML location list.

    For fully-specified entries (``plz`` + ``kcid`` + ``bcid``) the name
    ``{grid_id}_{plz}_{kcid}_{bcid}`` is derived directly without touching
    the metadata store.

    Args:
        grid_id: Grid identifier from experiment YAML (``federation.id``).
        use_meta_db: Metadata backend type.
        meta_store_path: Path to the meta_store directory.
        location: Location query list from the experiment YAML node data.

    Returns:
        List of federate name strings (e.g. ["lv-grid_91301_1_4"]).
    """
    if not location:
        print(f"Warning: No location list provided for grid '{grid_id}'.")
        return []

    names: list[str] = []
    needs_scan: list[str] = []  # prefixes that require a metadata scan

    for entry in location:
        plz = entry.get("plz")
        kcid = entry.get("kcid")
        bcid = entry.get("bcid")
        if plz is None:
            print(
                f"Warning: Location entry {entry!r} for grid '{grid_id}' is missing "
                f"'plz' — skipping."
            )
            continue
        if kcid is not None and bcid is not None:
            names.append(f"{grid_id}_{plz}_{kcid}_{bcid}")
        else:
            # Only plz given – collect all matching entries from metadata store
            needs_scan.append(f"{grid_id}_{plz}_")

    if needs_scan:
        with _create_metadata_manager(use_meta_db, meta_store_path) as mgr:
            all_keys = mgr.list_items("custom_metadata")
        for prefix in needs_scan:
            matched = [k for k in all_keys if k.startswith(prefix)]
            if not matched:
                print(
                    f"Warning: No custom_metadata entries found for prefix '{prefix}' "
                    f"— was the infdb step executed?"
                )
            names.extend(matched)

    return names


# ---------------------------------------------------------------------------
# Net introspection helpers
# ---------------------------------------------------------------------------


def _read_load_list(
    grid_fed_name: str,
    use_meta_db: str,
    meta_store_path: str = "meta_store",
) -> list[tuple[int, str, int]]:
    """Read pandapower load list from the custom_metadata store.

    Parses the ``net_json`` field written by the infdb step without
    importing pandapower — the serialised DataFrame is decoded via
    plain JSON.

    Args:
        grid_fed_name: Federate name key in custom_metadata
                       (e.g. ``"lv-grid_91301_1_4"``).
        meta_store_path: Path to the meta_store directory.

    Returns:
        Sorted list of ``(pp_index, load_name, bus)`` tuples.
        Empty list if the metadata file is missing or has no loads.
    """
    with _create_metadata_manager(use_meta_db, meta_store_path) as mgr:
        meta_data = mgr.read("custom_metadata", grid_fed_name)
    if not meta_data:
        print(
            f"Warning: custom_metadata '{grid_fed_name}' not found "
            f"in backend '{use_meta_db}'"
        )
        return []

    net_json_raw = meta_data.get("net_json")
    net_json_str = (
        json.dumps(net_json_raw)
        if isinstance(net_json_raw, dict)
        else net_json_raw
    )
    if not net_json_str:
        print(f"Warning: no net_json in custom_metadata '{grid_fed_name}'")
        return []

    net_dict = json.loads(net_json_str)
    load_obj = net_dict.get("_object", {}).get("load", {})
    load_data_str = load_obj.get("_object")
    if not load_data_str:
        return []

    load_df = json.loads(load_data_str)
    columns: list[str] = load_df["columns"]
    indices: list[int] = load_df["index"]
    data: list[list] = load_df["data"]

    name_col = columns.index("name")
    bus_col = columns.index("bus")

    return [
        (idx, row[name_col], int(row[bus_col]))
        for idx, row in zip(indices, data)
    ]


def _resolve_placement(
    placement,
    all_loads: list[tuple[int, str, int]],
) -> list[int]:
    """Resolve a placement value to a list of pandapower load indices.

    Args:
        placement: ``"fill"``, a single int, or a list of ints
                   representing pandapower load indices.
        all_loads: Full load list from :func:`_read_load_list`.

    Returns:
        List of valid pandapower load indices.

    Raises:
        ValueError: If an explicit index does not exist in the net.
    """
    valid_indices = {idx for idx, _, _ in all_loads}

    if placement == "fill":
        return [idx for idx, _, _ in all_loads]

    if isinstance(placement, int):
        placement = [placement]

    if isinstance(placement, list):
        for p in placement:
            if p not in valid_indices:
                raise ValueError(
                    f"Placement error: pandapower load index {p} "
                    f"does not exist in net (valid: {sorted(valid_indices)})"
                )
        return list(placement)

    raise ValueError(f"Unsupported placement value: {placement!r}")


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
    load_indices: list[int] | None = None,
) -> list[str]:
    """Wire pub/sub between a grid federate and one of its children.

    When *load_indices* is provided (for ``class: load``), one pub/sub
    group per pandapower load index is created so that each
    ``load_{idx}`` appears directly in the HELICS key.  This lets the
    grid federate map received values to the correct ``net.load`` row
    without any additional translation table.

    Returns the list of publication keys owned by the *child*.
    """
    child_pub_keys: list[str] = []

    if child_class == "load" and load_indices is not None:
        # ---- Per-load wiring (one group per load per signal) ------------
        for idx in load_indices:
            load_id = f"load_{idx}"

            _add_group(federation, f"{load_id}/active_power", child_fed_name, grid_fed_name, "double", "W")
            child_pub_keys.append(f"{child_fed_name.replace('.', '/')}/{load_id}/active_power")
            _add_group(federation, f"{load_id}/reactive_power", child_fed_name, grid_fed_name, "double", "VAr")
            child_pub_keys.append(f"{child_fed_name.replace('.', '/')}/{load_id}/reactive_power")

        print(
            f"    Wired {len(load_indices)} loads: "
            f"load_{load_indices[0]}..load_{load_indices[-1]}"
        )
    else:
        # ---- Single-group wiring (house, pv, battery, …) ----------------
        _add_group(federation, f"{child_local_id}/voltage", grid_fed_name, child_fed_name, "double", "V")

        if child_class in ("house", "load", "battery", "pv", "grid"):
            _add_group(federation, "active_power", child_fed_name, grid_fed_name, "double", "W")
            child_pub_keys.append(f"{child_fed_name.replace('.', '/')}/active_power")
            _add_group(federation, "reactive_power", child_fed_name, grid_fed_name, "double", "VAr")
            child_pub_keys.append(f"{child_fed_name.replace('.', '/')}/reactive_power")

        if child_class == "house":
            _add_group(federation, f"{child_local_id}/control", grid_fed_name, child_fed_name, "string", "json")

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
    # Apply patches
    apply_monkeypatches()

    name = general_cfg.get("name", "GridLock")
    use_meta_db = general_cfg.get("use_meta_db", "json")
    use_data_db = general_cfg.get("use_data_db", "postgres")

    federation = FederationConfig(
        f"{name}Scenario",
        f"{name}Analysis",
        f"{name}Federation",
        True,
        use_meta_db,
        use_data_db,
    )

    time_step = general_cfg.get("time_step", 1.0)

    # Nodes handled via location expansion – skip in the generic loop
    handled_nodes: set[str] = set()

    # ---- Phase 1: location-based grid expansion --------------------------
    for node in tree.all_nodes():
        if node.data.get("class") != "grid":
            continue
        if not node.data.get("location"):
            continue

        grid_tree_id = node.identifier
        grid_fed_names = discover_grid_federates(
            grid_tree_id, use_meta_db, location=node.data.get("location")
        )
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
            # Pre-read load list once per grid (needed for placement resolution)
            all_loads = _read_load_list(fed_name, use_meta_db)

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

                # Resolve which pandapower loads this child handles
                load_indices: list[int] | None = None
                if child_class == "load" and all_loads:
                    placement = child_data.get("placement")
                    load_indices = _resolve_placement(placement, all_loads)
                    print(
                        f"  Resolved placement {placement!r} → "
                        f"{len(load_indices)} load(s)"
                    )

                # Wire pub/sub between grid and this child
                _wire_grid_child(
                    federation, fed_name, child_fed_name,
                    child_local_id, child_class,
                    load_indices=load_indices,
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

        fed = FederateConfig(node.identifier, period=time_step)

        federation.add_federate_config(fed)

        mapped = map_params_to_class(node_class)
        if node_class == "grid":
            layout = data.get("layout")
            if layout:
                mapped["command"] += f" --grid_file {layout}"
            else:
                print(
                    f"Warning: No layout or location for grid {node.identifier}"
                )
        fed.config("image", mapped["image"])
        fed.config("command", mapped["command"])
        fed.config("federate_type", node_type)

    # Build pub/sub using add_group with proper src/des structure
    # Map topics to their publishers and subscribers
    topic_map: dict[str, dict] = {} # topic -> {"publishers": [fed_names], "subscribers": [fed_names], "unit": str, "dtype": str}
    
    for node in tree.all_nodes():
        if node.identifier in handled_nodes:
            continue

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

    try:
        federation.write_config(start_str, end_str)
        
        # Normalize dots to slashes in all HELICS keys
        normalize_federation_keys(federation.federation_name, use_meta_db)
        
        DockerRunner.define_yaml(
            federation.scenario_name,
            use_meta_db=use_meta_db,
            use_data_db=use_data_db,
        )
        print("Success: Federation configuration and docker-compose.yml generated.")
    except Exception as e:
        print(f"Error generating config: {e}")
        traceback.print_exc()
