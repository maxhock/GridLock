"""Grid resolver: turns an experiment's grid sources into nets in the CST metadata store.

Every `class: grid` node of the federation tree gets its net written to
`custom_metadata` under the node's dotted id (`mv-grid_0.lv-grid_0` for an LV grid
nested under an MV grid), so this is the only stage needing InfDB access -
federates read the net back from CST.
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

from grid_files import expand_filename_placements
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


def _node_id(node: dict, parent: str | None) -> str:
    """Build a node's dotted id exactly the way composegen does.

    Pinned to the rule in `composegen/extract.py:add_to_tree`:
    `node_id = f"{parent}.{node_dict.get('id')}" if parent else node_dict.get("id")`
    composegen reads each net back under this id, so the two must not drift. The
    root's id defaults to "grid" (as this stage always did); composegen has no such
    default, which only matters for a root without an id.
    """
    node_id = node.get("id", "grid" if parent is None else None)
    if not node_id:
        raise ValueError("Each federation node requires an 'id'.")
    return f"{parent}.{node_id}" if parent else node_id


def _collect_grid_nodes(
    node: dict, parent: str | None = None, parent_is_grid: bool = True
) -> list[tuple[str, dict]]:
    """Walk the federation tree and return `(dotted id, node)` for every grid.

    The root is always taken as a grid, as before. composegen only finds grids whose
    ancestors are all grids, so a grid under any other class would get a net here and
    never be wired; that is raised instead of resolved.
    """
    node_id = _node_id(node, parent)
    is_grid = parent is None or node.get("class") == "grid"
    if is_grid and not parent_is_grid:
        raise ValueError(
            f"Grid '{node_id}' sits under a federate that is not a grid. composegen "
            "only wires grids nested under grids, so this one would never run."
        )

    grids = [(node_id, node)] if is_grid else []
    for sub in node.get("sub_federates") or []:
        grids.extend(_collect_grid_nodes(sub, node_id, is_grid))
    return grids


def _grid_source(grid_id: str, config: dict, is_root: bool) -> dict:
    """Pick the single source (`layout` or `location`) a grid declares.

    Returns a dict tagged by `source`: `"infdb"` carries `location`, `"layout"`
    carries `layout`; both carry `grid_id`.
    """
    location = config.get("location")
    layout = config.get("layout")

    if location and layout:
        raise ValueError(
            f"Grid '{grid_id}' must define either 'location' or 'layout', not both."
        )

    if layout:
        return {"grid_id": grid_id, "source": "layout", "layout": layout}

    if location:
        if not is_root:
            raise NotImplementedError(
                f"Nested grid '{grid_id}' uses 'location', which is not supported: an "
                "InfDB location query returns an unknown number of LV feeders with no "
                "mapping onto its parent grid's loads. Give nested grids a 'layout' "
                "for now; auto-placement from InfDB is a later phase (issue #65)."
            )
        return {"grid_id": grid_id, "source": "infdb", "location": location}

    raise ValueError(
        f"No grid source defined for grid '{grid_id}'. Set its config.location "
        "for InfDB or its config.layout for a local workbook."
    )


def extract_grid_config(experiment_path: str) -> list[dict]:
    """Read where every grid in the federation tree comes from.

    Returns one dict per grid, root first, tagged by `source` (`"infdb"` carries
    `location`, `"layout"` carries `layout`). Each carries `grid_id` - the dotted
    tree id the net is stored under - and `use_meta_db`. Only the root may use
    `location`; nested grids need a `layout`.
    """
    path = Path(experiment_path)
    if not path.exists():
        raise FileNotFoundError(f"Experiment config not found: {experiment_path}")

    with open(path) as f:
        cfg = yaml.safe_load(f)

    use_meta_db = cfg.get("general", {}).get("use_meta_db", "json")
    federation = expand_filename_placements(cfg.get("federation", {}), LAYOUT_DIR)
    grid_nodes = _collect_grid_nodes(federation)

    ids = [grid_id for grid_id, _ in grid_nodes]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise ValueError(f"Duplicate grid id(s) in the federation tree: {duplicates}")

    return [
        {
            **_grid_source(grid_id, node.get("config") or {}, is_root=index == 0),
            "use_meta_db": use_meta_db,
        }
        for index, (grid_id, node) in enumerate(grid_nodes)
    ]


def write_grid_to_metadata(
    grids: dict[str, list[tuple[str, str]]],
    meta_store_path: str,
    use_meta_db: str,
) -> list[str]:
    """Store each net under its federate name, and report those names.

    `grids` maps a grid's id to the `(federate name, net JSON)` pairs it resolved to:
    one pair for a layout, one per feeder for an InfDB location.
    """
    from src.infdb_data import _net_table_count

    md_kwargs = {"backend": use_meta_db}
    if use_meta_db == "json":
        md_kwargs["location"] = meta_store_path

    md_mgr = create_metadata_manager(**md_kwargs)
    md_mgr.connect()

    federate_names: list[str] = []
    try:
        for grid_id, net_list in grids.items():
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


LAYOUT_DIR = Path("/data/input")

# Kept in step with LAYOUT_SUFFIXES in composegen/extract.py, which rejects an
# unsupported suffix before anything runs (the two images share no code).
_LAYOUT_READERS = {".xlsx": pp.from_excel, ".json": pp.from_json}


def _read_layout(layout_path: Path) -> pp.pandapowerNet:
    """Read a pandapower net from a workbook (`.xlsx`) or a JSON export (`.json`)."""
    reader = _LAYOUT_READERS.get(layout_path.suffix.lower())
    if reader is None:
        raise ValueError(
            f"Unsupported grid layout '{layout_path.name}': expected one of "
            f"{', '.join(_LAYOUT_READERS)}."
        )
    return reader(layout_path)


def load_local_layout(grid_id: str, layout: str) -> list[tuple[str, str]]:
    """Load a local pandapower net as the same JSON payload InfDB returns."""
    layout_path = LAYOUT_DIR / layout
    if not layout_path.is_file():
        raise FileNotFoundError(f"Local grid layout not found: {layout_path}")

    net = _read_layout(layout_path)
    print(f"Loaded local grid '{grid_id}' from {layout_path}")
    return [(grid_id, pp.to_json(net))]


def resolve_location_grid(grid_id: str, location: list[dict]) -> list[tuple[str, str]]:
    """Resolve a grid's InfDB location queries into `(federate name, net JSON)` pairs."""
    print(f"Grid '{grid_id}': {len(location)} location query(ies)")

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
        raise
    finally:
        infdb.stop_logger()
        if temp_config_dir is not None:
            shutil.rmtree(temp_config_dir)

    return net_list


def resolve_grid(grid_config: dict) -> list[tuple[str, str]]:
    """Resolve one grid from its source; only `location` needs the InfDB connection."""
    grid_id = grid_config["grid_id"]
    if grid_config["source"] == "layout":
        return load_local_layout(grid_id, grid_config["layout"])
    return resolve_location_grid(grid_id, grid_config["location"])


def main(
    experiment_path: str | None = None,
    meta_store_path: str | None = None,
) -> None:
    """Resolve every grid in the experiment and store them for the run to read."""
    if experiment_path is None:
        args = parse_args()
        experiment_path = args.experiment
        meta_store_path = args.meta_store

    if meta_store_path is None:
        raise ValueError("meta_store_path is required")

    print("=== infdb data setup resolver ===")
    print(f"Experiment: {experiment_path}")
    print(f"Meta store: {meta_store_path}")

    # 1. Extract every grid's source from the experiment's federation tree
    grid_configs = extract_grid_config(experiment_path)
    use_meta_db = grid_configs[0]["use_meta_db"]

    # 2. Resolve each grid (InfDB is only contacted for `location` grids)
    grids = {cfg["grid_id"]: resolve_grid(cfg) for cfg in grid_configs}

    # 3. Write nets to CST metadata store
    print(f"Writing grids to CST metadata store (backend={use_meta_db})...")
    federate_names = write_grid_to_metadata(grids, meta_store_path, use_meta_db)

    print(
        f"=== data setup complete: {len(federate_names)} grid federate(s) resolved ==="
    )


if __name__ == "__main__":
    main()
