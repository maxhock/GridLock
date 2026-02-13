"""Pre-flight data resolver for GridLock.

Reads experiment.yml location queries, fetches data from InfDB,
and writes resolved data into CST stores so all federates can
read it at runtime without needing InfDB access themselves.

Grid nets → CST metadata store (custom collection "grid_data")
Timeseries → CST timeseries store (preloaded TSRecords)  [future]
Manifest   → meta_store/manifest.json (federate names for composegen)
"""

import argparse
import json
from pathlib import Path

import yaml
from infdb import InfDB
from cosim_toolbox.dbms import create_metadata_manager

from src.infdb_data import resolve_grid_queries


DEFAULT_EXPERIMENT_PATH = "/config/experiment-LV.yml"
DEFAULT_META_STORE = "meta_store"


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments.

    Returns:
        Namespace with experiment_path and meta_store_path.
    """
    parser = argparse.ArgumentParser(
        description="Pre-flight data resolver: InfDB → CST stores"
    )
    parser.add_argument(
        "--experiment",
        type=str,
        default=DEFAULT_EXPERIMENT_PATH,
        help="Path to experiment YAML file",
    )
    parser.add_argument(
        "--meta_store",
        type=str,
        default=DEFAULT_META_STORE,
        help="Path to CST meta_store directory",
    )
    args, _ = parser.parse_known_args()
    return args


def extract_grid_config(experiment_path: str) -> dict:
    """Extract grid federation config from experiment YAML.

    Reads the top-level federation node and returns its id and
    location queries.

    Args:
        experiment_path: Path to experiment YAML file.

    Returns:
        Dict with grid_id (str) and location (list of query dicts).

    Raises:
        FileNotFoundError: If experiment file doesn't exist.
        ValueError: If no location queries are defined.
    """
    path = Path(experiment_path)
    if not path.exists():
        raise FileNotFoundError(f"Experiment config not found: {experiment_path}")

    with open(path) as f:
        cfg = yaml.safe_load(f)

    federation = cfg.get("federation", {})
    grid_id = federation.get("id", "grid")
    config = federation.get("config", {})
    location = config.get("location")

    if not location:
        raise ValueError(
            f"No 'location' queries defined in experiment config "
            f"at federation.config.location"
        )

    return {"grid_id": grid_id, "location": location}


def write_grid_to_metadata(
    grid_id: str,
    net_list: list,
    meta_store_path: str,
) -> list[str]:
    """Write resolved pandapower nets to CST metadata store.

    Each net is stored as a JSON string in a custom "grid_data"
    collection, keyed by its federate name.

    Args:
        grid_id: Base grid identifier (e.g. "lv-grid").
        net_list: List of (federate_name, pandapower_net) tuples.
        meta_store_path: Path to meta_store directory.

    Returns:
        List of federate names that were written.
    """
    import pandapower as pp

    md_mgr = create_metadata_manager(backend="json", location=meta_store_path)
    md_mgr.connect()

    federate_names: list[str] = []
    try:
        for federate_name, net in net_list:
            net_json_str = pp.to_json(net)
            data = {
                "grid_id": grid_id,
                "net_json": net_json_str,
                "bus_count": len(net.bus),
                "load_count": len(net.load),
                "ext_grid_count": len(net.ext_grid),
            }
            md_mgr.write("grid_data", federate_name, data, overwrite=True)
            federate_names.append(federate_name)
            print(f"  Stored {federate_name} ({len(net.bus)} buses)")
    finally:
        md_mgr.disconnect()

    return federate_names


def write_manifest(
    federate_names: list[str],
    grid_id: str,
    meta_store_path: str,
) -> None:
    """Write manifest.json listing resolved federate names.

    Composegen reads this to know exact federate count and names
    for generating correct federation JSON and broker -f count.

    Args:
        federate_names: List of resolved grid federate names.
        grid_id: Base grid identifier.
        meta_store_path: Path to meta_store directory.
    """
    manifest_path = Path(meta_store_path) / "manifest.json"
    manifest = {
        "grid_id": grid_id,
        "grid_federates": federate_names,
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)
    print(f"Wrote manifest to {manifest_path}")


def main(
    experiment_path: str | None = None,
    meta_store_path: str | None = None,
) -> None:
    """Resolve InfDB data and write to CST stores.

    Args:
        experiment_path: Path to experiment YAML. Parsed from CLI if None.
        meta_store_path: Path to meta_store dir. Parsed from CLI if None.
    """
    if experiment_path is None:
        args = parse_args()
        experiment_path = args.experiment
        meta_store_path = args.meta_store

    print(f"=== infdb-data pre-flight resolver ===")
    print(f"Experiment: {experiment_path}")
    print(f"Meta store: {meta_store_path}")

    # 1. Extract location queries from experiment.yml
    grid_config = extract_grid_config(experiment_path)
    grid_id = grid_config["grid_id"]
    location = grid_config["location"]
    print(f"Grid '{grid_id}': {len(location)} location query(ies)")

    # 2. Connect to InfDB and resolve queries
    infdb = InfDB(tool_name="infdb-data", config_path="configs")
    log = infdb.get_logger()
    log.info("Starting pre-flight data resolution")

    try:
        net_list = resolve_grid_queries(
            infdb=infdb, log=log, grid_id=grid_id, queries=location
        )
        print(f"Resolved {len(net_list)} grid(s) from InfDB")
    except Exception as e:
        log.error(f"Failed to resolve grid queries: {e}")
        infdb.stop_logger()
        raise

    infdb.stop_logger()

    # 3. Write nets to CST metadata store
    print("Writing grids to CST metadata store...")
    federate_names = write_grid_to_metadata(grid_id, net_list, meta_store_path)

    # 4. Write manifest for composegen
    write_manifest(federate_names, grid_id, meta_store_path)

    print(f"=== Pre-flight complete: {len(federate_names)} grid federate(s) resolved ===")


if __name__ == "__main__":
    main()
