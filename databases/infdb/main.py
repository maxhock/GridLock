"""Grid resolver: turns an experiment's grid source into nets in the CST metadata store.

Each net is written to `custom_metadata` under its grid federate's name, so this
is the only stage needing InfDB access - federates read the net back from CST.
"""

import argparse
import os
from pathlib import Path
import shutil
import tempfile

import yaml
import pandapower as pp
from infdb import InfDB
from cosim_toolbox.dbms import create_metadata_manager

from src.infdb_data import resolve_grid_queries


DEFAULT_EXPERIMENT_PATH = os.getenv("CONFIG_PATH", "/config/experiment-LV.yml")
DEFAULT_META_STORE = "generated"
DEFAULT_INFDB_CONFIG_DIR = "configs"


def _coerce_config_value(key: str, value: str) -> str | int:
    """Convert environment values to the expected config types."""
    if key == "exposed_port":
        return int(value)
    return value


def _first_env_value(*names: str) -> str | None:
    """Return the first non-empty environment value from the given names."""
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return None


def prepare_infdb_config(
    config_dir: str = DEFAULT_INFDB_CONFIG_DIR,
) -> tuple[str, str | None]:
    """Create a runtime InfDB config directory with env overrides applied.

    The upstream InfDB package hardcodes ``host.docker.internal`` when
    ``host`` is set to ``"None"`` in the YAML config, and only reads
    env values for fields that are explicitly ``"None"``. To keep the
    host and port configurable from the shared preflight env file, we
    materialize a temporary config file with the env values written in.

    Returns ``(config dir to use, temp dir to clean up or None)``.
    """
    config_path = Path(config_dir) / "config-infdb.yml"
    with open(config_path) as handle:
        config = yaml.safe_load(handle)

    postgres_config = (
        config.setdefault("infdb", {})
        .setdefault("hosts", {})
        .setdefault("postgres", {})
    )
    overrides = {
        "user": ("INFDB_USER",),
        "password": ("INFDB_PASSWORD",),
        "db": ("INFDB_DB",),
        "host": ("INFDB_HOST",),
        "exposed_port": ("INFDB_PORT",),
        "epsg": ("INFDB_EPSG",),
    }

    changed = False
    for key, env_names in overrides.items():
        env_value = _first_env_value(*env_names)
        if env_value:
            postgres_config[key] = _coerce_config_value(key, env_value)
            changed = True

    if not changed:
        return config_dir, None

    temp_dir = tempfile.mkdtemp(prefix="infdb-config-")
    temp_config_path = Path(temp_dir) / "config-infdb.yml"
    with open(temp_config_path, "w") as handle:
        yaml.safe_dump(config, handle, sort_keys=False)

    return temp_dir, temp_dir


def parse_args() -> argparse.Namespace:
    """Read the experiment path and meta store location `run.sh` put on the CLI."""
    parser = argparse.ArgumentParser(
        description="data setup data resolver: InfDB → CST stores"
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
    """Read where the grid comes from: an InfDB location or a local layout.

    Returns a dict tagged by `source`: `"infdb"` carries `location`, `"layout"`
    carries `layout`. Both carry `grid_id` and `use_meta_db`.
    """
    path = Path(experiment_path)
    if not path.exists():
        raise FileNotFoundError(f"Experiment config not found: {experiment_path}")

    with open(path) as f:
        cfg = yaml.safe_load(f)

    general = cfg.get("general", {})
    federation = cfg.get("federation", {})
    grid_id = federation.get("id", "grid")
    config = federation.get("config", {})
    location = config.get("location")
    layout = config.get("layout")
    use_meta_db = general.get("use_meta_db", "json")

    if location and layout:
        raise ValueError(
            "Grid configuration must define either 'location' or 'layout', not both."
        )

    if location:
        return {
            "grid_id": grid_id,
            "source": "infdb",
            "location": location,
            "use_meta_db": use_meta_db,
        }

    if layout:
        return {
            "grid_id": grid_id,
            "source": "layout",
            "layout": layout,
            "use_meta_db": use_meta_db,
        }

    raise ValueError(
        "No grid source defined in experiment config. Set federation.config.location "
        "for InfDB or federation.config.layout for a local workbook."
    )


def write_grid_to_metadata(
    grid_id: str,
    net_list: list[tuple[str, str]],
    meta_store_path: str,
    use_meta_db: str,
) -> list[str]:
    """Store each net under its grid federate's name, and report those names."""
    from src.infdb_data import _net_table_count

    md_kwargs = {"backend": use_meta_db}
    if use_meta_db == "json":
        md_kwargs["location"] = meta_store_path

    md_mgr = create_metadata_manager(**md_kwargs)
    md_mgr.connect()

    federate_names: list[str] = []
    try:
        for federate_name, net_json_str in net_list:
            bus_count = _net_table_count(net_json_str, "bus")
            data = {
                "grid_id": grid_id,
                "net_json": net_json_str,
                "bus_count": bus_count,
                "load_count": _net_table_count(net_json_str, "load"),
                "ext_grid_count": _net_table_count(net_json_str, "ext_grid"),
            }
            md_mgr.write("custom_metadata", federate_name, data, overwrite=True)
            federate_names.append(federate_name)
            print(f"  Stored {federate_name} ({bus_count} buses)")
    finally:
        md_mgr.disconnect()

    return federate_names


def load_local_layout(grid_id: str, layout: str) -> list[tuple[str, str]]:
    """Load a local Pandapower workbook as the same JSON payload InfDB returns."""
    layout_path = Path("/data/input") / layout
    if not layout_path.is_file():
        raise FileNotFoundError(f"Local grid layout not found: {layout_path}")

    net = pp.from_excel(layout_path)
    print(f"Loaded local grid '{grid_id}' from {layout_path}")
    return [(grid_id, pp.to_json(net))]


def main(
    experiment_path: str | None = None,
    meta_store_path: str | None = None,
) -> None:
    """Resolve the experiment's grid and store it for the rest of the run to read."""
    if experiment_path is None:
        args = parse_args()
        experiment_path = args.experiment
        meta_store_path = args.meta_store

    print("=== infdb data setup resolver ===")
    print(f"Experiment: {experiment_path}")
    print(f"Meta store: {meta_store_path}")

    # 1. Extract grid source from experiment.yml
    grid_config = extract_grid_config(experiment_path)
    grid_id = grid_config["grid_id"]
    use_meta_db = grid_config["use_meta_db"]
    if grid_config["source"] == "layout":
        net_list = load_local_layout(grid_id, grid_config["layout"])
    else:
        location = grid_config["location"]
        print(f"Grid '{grid_id}': {len(location)} location query(ies)")

        # 2. Connect to InfDB and resolve queries
        config_path, temp_config_dir = prepare_infdb_config()
        infdb = InfDB(tool_name="infdb", config_path=config_path)
        log = infdb.get_logger()
        log.info("Starting data setup data resolution")

        try:
            net_list = resolve_grid_queries(
                infdb=infdb, log=log, grid_id=grid_id, queries=location
            )
            print(f"Resolved {len(net_list)} grid(s) from InfDB")
            if not net_list:
                query_summary = ", ".join([f"PLZ {q['plz']}" for q in location])
                raise ValueError(
                    f"InfDB returned no grids for location queries: {query_summary}. "
                    f"Check that PLZ codes exist in pylovo.grid_result table."
                )
        except Exception as e:
            log.error(f"Failed to resolve grid queries: {e}")
            infdb.stop_logger()
            if temp_config_dir is not None:
                shutil.rmtree(temp_config_dir)
            raise

        infdb.stop_logger()
        if temp_config_dir is not None:
            shutil.rmtree(temp_config_dir)

    # 3. Write nets to CST metadata store
    print(f"Writing grids to CST metadata store (backend={use_meta_db})...")
    federate_names = write_grid_to_metadata(
        grid_id,
        net_list,
        meta_store_path,
        use_meta_db,
    )

    print(
        f"=== data setup complete: {len(federate_names)} grid federate(s) resolved ==="
    )


if __name__ == "__main__":
    main()
