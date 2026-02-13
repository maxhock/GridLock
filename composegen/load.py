import traceback
import json
from pathlib import Path
from treelib import Tree
from cosim_toolbox.sims import FederationConfig, FederateConfig, DockerRunner, HelicsPubGroup, HelicsSubGroup
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


def normalize_federation_keys(federation_name: str) -> None:
    """Post-process federation JSON to replace dots with slashes in all HELICS keys.
    
    Args:
        federation_name: Name of the federation file to process
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
        for fed_name, fed_config in config["federation"].items():
            if "HELICS_config" in fed_config:
                helics_cfg = fed_config["HELICS_config"]
                
                # Normalize publication keys
                if "publications" in helics_cfg:
                    for pub in helics_cfg["publications"]:
                        if "key" in pub:
                            pub["key"] = pub["key"].replace(".", "/")
                
                # Normalize subscription keys
                if "subscriptions" in helics_cfg:
                    for sub in helics_cfg["subscriptions"]:
                        if "key" in sub:
                            sub["key"] = sub["key"].replace(".", "/")
    
    # Write back the normalized config
    with open(federation_path, "w") as f:
        json.dump(config, f, indent=2)
    
    print(f"Normalized HELICS keys in {federation_path}")


def load_manifest(meta_store_path: str = "meta_store") -> dict | None:
    """Load the manifest written by infdb-data pre-flight.

    The manifest lists resolved grid federate names so composegen
    knows exact count/names without needing InfDB access.

    Args:
        meta_store_path: Path to the meta_store directory.

    Returns:
        Manifest dict with grid_id and grid_federates list,
        or None if no manifest exists (layout-only experiment).
    """
    manifest_path = Path(meta_store_path) / "manifest.json"
    if not manifest_path.exists():
        return None
    with open(manifest_path) as f:
        return json.load(f)


def load(tree: Tree, general_cfg: dict) -> None:
    """Generate CST federation configuration and docker-compose from the transformed tree.

    For location-based grids (resolved by infdb-data pre-flight), reads
    manifest.json from meta_store to get the exact federate names and
    count. Each resolved grid federate gets its own FederateConfig entry.

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

    # Load manifest from infdb-data pre-flight (may be None for layout-only)
    manifest = load_manifest()

    for node in tree.all_nodes():
        data = node.data
        node_class = data.get("class")
        node_type = data.get("type")

        if not node_type or node_type == "empty":
            continue

        time_step = general_cfg.get("time_step", 1.0)

        mapped = map_params_to_class(node_class)
        if node_class == "grid":
            location = data.get("location")
            layout = data.get("layout")
            if location and manifest:
                # Location-based: create one FederateConfig per resolved grid
                # from the manifest written by infdb-data
                for fed_name in manifest["grid_federates"]:
                    fed = FederateConfig(fed_name, period=time_step)
                    federation.add_federate_config(fed)
                    cmd = f"{mapped['command']} --scenario {name}Scenario --federate_name {fed_name}"
                    fed.config("image", mapped["image"])
                    fed.config("command", cmd)
                    fed.config("federate_type", node_type)
                    print(f"Added grid federate from manifest: {fed_name}")
                # Skip adding the template grid node itself
                continue
            elif layout:
                mapped["command"] += f" --grid_file {layout}"
            else:
                print(
                    f"Warning: No layout or location specified for grid {node.identifier}"
                )

        fed = FederateConfig(node.identifier, period=time_step)
        federation.add_federate_config(fed)
        fed.config("image", mapped["image"])
        fed.config("command", mapped["command"])
        fed.config("federate_type", node_type)

    # Build pub/sub using add_group with proper src/des structure
    # Map topics to their publishers and subscribers
    topic_map = {}  # topic -> {"publishers": [fed_names], "subscribers": [fed_names], "unit": str, "dtype": str}
    
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
