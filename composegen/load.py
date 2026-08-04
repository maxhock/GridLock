"""ETL stage 3: write the CST federation and `generated/docker-compose.yaml`."""

from __future__ import annotations

import json
import re
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

from omegaconf import DictConfig
from treelib import Tree

from transform import MPC_FORECAST_HORIZON_STEPS, TransformedConfig
from cosim_toolbox.dbms import create_metadata_manager


RUNNER_FEDERATES = {
    "grid",
    "house",
    "controller",
    "house_player",
}

# Classes that occupy a pandapower load index rather than sitting on a bare
# bus. The grid identifies incoming power by the `load_<idx>` segment of the
# HELICS key, so anything that injects or draws power has to be placed here to
# reach the power flow at all. Generation is no exception - a PV plant is a
# negative load, and a battery is a load of either sign.
LOAD_PLACED_CLASSES = {"load", "house", "pv", "battery"}

# Classes that can be placed in a grid but have no federate of their own yet.
# Their physics currently lives inside the house simulator, and
# `map_params_to_class` maps them to the *house* image, so generating one
# would start a house simulation with no exogenous dataset and fail at
# startup with an unrelated message.
UNIMPLEMENTED_GRID_CHILD_CLASSES = {"pv", "battery"}

DEFAULT_COMMAND_TEMPLATES = {
    "broker": "helics_broker --federates={total_federates} --name={name} --ipv4",
    "recorder": (
        "helics_recorder "
        "--name={name} "
        "--capture={target} "
        "--output={output_file} "
        "--broker={broker}"
    ),
}


# ---------------------------------------------------------------------------
# Legacy composegen output
# ---------------------------------------------------------------------------

def _runner_write(path: Path, name: str, federates: list[dict]) -> None:
    """Write one legacy `helics run` runner file."""
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w") as f:
        json.dump(
            {
                "name": name,
                "federates": federates,
            },
            f,
            indent=2,
        )

    print(f"Generated {path} with {len(federates)} federate(s)")


def _get_nodes_for_fed(conf: DictConfig, fed_key: str) -> list[int]:
    """Resolve a legacy federate's placement, defaulting to every node in the grid."""
    num_nodes = int(conf.federates.grid.num_nodes)
    fed_cfg = conf.federates.get(fed_key)

    if fed_cfg is None:
        return list(range(num_nodes))

    placement = fed_cfg.get("placement", None)

    if placement is None:
        return list(range(num_nodes))

    return [int(x) for x in placement]


def create_broker_runner(conf: DictConfig, output_path: Path) -> None:
    """Write the legacy runner starting the broker for the expected federate count."""
    total = int(conf.federates.broker.total_federates)
    broker_name = conf.federates.broker.name

    cmd = DEFAULT_COMMAND_TEMPLATES["broker"].format(
        total_federates=total,
        name=broker_name,
    )

    federates = [
        {
            "directory": "/app",
            "exec": cmd,
            "host": "localhost",
            "name": broker_name,
        }
    ]

    _runner_write(output_path, "broker_runner", federates)


def create_grid_runner(conf: DictConfig, output_path: Path) -> None:
    """Write the legacy runner for the single grid federate."""
    grid_file = conf.federates.grid.grid_file

    federates = [
        {
            "directory": "/app",
            "exec": f"python main.py --grid_file={grid_file}",
            "host": "localhost",
            "name": conf.federates.grid.name,
        }
    ]

    _runner_write(output_path, "grid_federation", federates)


def create_grid_config(conf: DictConfig, output_path: Path) -> None:
    """Write the legacy grid federate's HELICS settings, including its timing flags."""
    grid_config = {
        "name": conf.federates.grid.name,
        "loglevel": conf.general.get("loglevel", "warning"),
        "coreType": "zmq",
        "period": float(conf.general.time_step),
        "offset": 0.0,
        "max_cosim_duration": float(conf.general.end_time),
        "broker": conf.federates.broker.name,
        "uninterruptible": False,
        "terminate_on_error": True,
        "wait_for_current_time_update": True,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w") as f:
        json.dump(grid_config, f, indent=2)

    print(f"Generated {output_path}")


def create_house_runner(conf: DictConfig, output_path: Path) -> None:
    """Write the legacy runner holding one house instance per placed node."""
    nodes = _get_nodes_for_fed(conf, "house")
    config_file = conf.federates.house.get("config_file", "/config/house_config.yaml")
    scenario_name = conf.federates.grid.get("name", "GridLock")

    federates = []

    for i in nodes:
        federates.append(
            {
                "directory": "/app",
                "exec": (
                    f"python house/main.py "
                    f"--scenario={scenario_name}Scenario "
                    f"--federate_name=house_{i} "
                    f"--config={config_file}"
                ),
                "host": "localhost",
                "name": f"house_{i}",
            }
        )

    _runner_write(output_path, "house_federation", federates)


def create_controller_runner(conf: DictConfig, output_path: Path) -> None:
    """Write the legacy controller runner, following the houses' placement."""
    if (
        conf.federates.controller.get("placement", None) is None
        and "house" in conf.federates
        and conf.federates.house.get("placement", None) is not None
    ):
        nodes = [int(x) for x in conf.federates.house.placement]
    else:
        nodes = _get_nodes_for_fed(conf, "controller")

    stop_time = float(conf.general.end_time - conf.general.start_time)
    dt = int(conf.general.time_step)

    federates = []

    for i in nodes:
        federates.append(
            {
                "directory": "/app",
                "exec": (
                    f"python controller/main.py "
                    f"--name=controller_{i} "
                    f"--stop_time={stop_time} "
                    f"--dt={dt}"
                ),
                "host": "localhost",
                "name": f"controller_{i}",
            }
        )

    _runner_write(output_path, "controller_federation", federates)


def create_house_player_runner(conf: DictConfig, output_path: Path) -> None:
    """Write the legacy runner holding one load-player instance per placed node."""
    nodes = _get_nodes_for_fed(conf, "house_player")
    stop_time = float(conf.general.end_time - conf.general.start_time)
    dt = int(conf.general.time_step)

    federates = []

    for i in nodes:
        federates.append(
            {
                "directory": "/app",
                "exec": (
                    f"python house_player/main.py "
                    f"--name=house_player_{i} "
                    f"--stop_time={stop_time} "
                    f"--dt={dt}"
                ),
                "host": "localhost",
                "name": f"house_player_{i}",
            }
        )

    _runner_write(output_path, "house_player_federation", federates)


def create_recorder_runner(conf: DictConfig, output_path: Path) -> None:
    """Write the legacy runner for a `helics_recorder` capturing one target federate."""
    target = conf.federates.recorder.get("target", "grid")
    output_file = conf.federates.recorder.get(
        "output_file",
        f"/data/output/{target}.log",
    )
    broker = conf.federates.broker.name
    name = conf.federates.recorder.name

    cmd = DEFAULT_COMMAND_TEMPLATES["recorder"].format(
        name=name,
        target=target,
        output_file=output_file,
        broker=broker,
    )

    federates = [
        {
            "directory": "/app",
            "exec": cmd,
            "host": "localhost",
            "name": name,
        }
    ]

    _runner_write(output_path, "recorder_federation", federates)


def create_docker_compose(conf: DictConfig, output_path: Path) -> None:
    """Write the legacy compose file, one service per federate class.

    Note that `run.sh` never brings this file up - stage 4 only ever starts
    `generated/docker-compose.yaml`, which `monkeypatch.define_yaml` writes.
    """
    compose = {
        "networks": {
            "helics-net": {
                "driver": "bridge",
            }
        },
        "services": {},
    }

    broker_name = conf.federates.broker.name

    for fed_key, fed_cfg in conf.federates.items():

        name = fed_cfg.name
        build_folder = fed_cfg.build_folder

        service = {
            "container_name": name,
            "build": {
                "context": "${PWD}",
                "dockerfile": f"{build_folder}/Dockerfile",
            },
            "working_dir": "/app",
            "volumes": [
                "${PWD}/data:/data",
                "${PWD}/config:/config:ro",
                "${PWD}/config/tmp:/config/tmp:ro",
                "${PWD}/examples:/app/examples:ro",
            ],
            "networks": ["helics-net"],
            "environment": {
                "HELICS_BROKER": broker_name,
                "PYTHONPATH": "/app",
            },
        }

        if fed_key != "broker":
            service["depends_on"] = [broker_name]

        if fed_key in RUNNER_FEDERATES:
            service["command"] = (
                f"helics run --path=/config/tmp/{fed_key}_runner.json --no-log-files"
            )

        elif fed_key == "broker":
            service["command"] = DEFAULT_COMMAND_TEMPLATES["broker"].format(
                total_federates=int(conf.federates.broker.total_federates),
                name=broker_name,
            )

        elif "command" in fed_cfg:
            service["command"] = fed_cfg.command

        compose["services"][name] = service

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w") as f:
        yaml.dump(compose, f, default_flow_style=False, sort_keys=False)

    print(f"Generated docker-compose.yml at {output_path}")


def load_legacy_outputs(transformed: TransformedConfig) -> None:
    """Emit the legacy artefacts: runner files, grid config and a compose file."""
    conf = transformed.conf
    output_dir = Path(transformed.output_path)

    if conf is None:
        raise ValueError("Legacy load expected transformed.conf, got None.")

    # Generate and store run metadata for legacy mode
    from transform import generate_run_metadata
    
    # Legacy configs are not run through `process_general_config`, so the
    # times here are still the raw numeric offsets from the YAML. Keep them
    # numeric: `_to_iso_wallclock` passes strings through untouched, so
    # stringifying them first would store "0"/"82800" instead of wall clock.
    general_cfg = {
        "name": conf.federates.grid.get("name", "GridLock"),
        "start_time": conf.general.get("start_time") or 0,
        "end_time": conf.general.get("end_time") or 0,
        "use_meta_db": "json",
        "use_data_db": "postgres",
    }
    
    run_metadata = generate_run_metadata(
        experiment_path=transformed.config_path,
        general_cfg=general_cfg
    )
    
    scenario_name = run_metadata["scenario_name"]
    
    start_time_iso = _to_iso_wallclock(general_cfg.get("start_time", 0))
    end_time_iso = _to_iso_wallclock(general_cfg.get("end_time", 0))

    scenario_metadata = {
        "analysis": run_metadata["analysis"],
        "federation": f"{run_metadata['analysis']}Federation",
        "start_time": start_time_iso,
        "stop_time": end_time_iso,
        "docker": True,
        "cst_007": scenario_name,
        "run_timestamp": run_metadata["timestamp_iso"],
        "run_timestamp_unix": run_metadata["timestamp_unix"],
        "git_commit": run_metadata["git_commit"],
        "experiment_path": run_metadata["experiment_path"],
        "experiment_yaml_raw": run_metadata["experiment_yaml_raw"],
    }
    
    # Write to metadata store (JSON backend for legacy mode)
    md_mgr = create_metadata_manager(backend="json", location=str(output_dir))
    md_mgr.connect()
    try:
        md_mgr.writer.write_scenario(scenario_name, scenario_metadata)
        print(f"Stored run metadata for scenario: {scenario_name}")
    except Exception as e:
        md_mgr.disconnect()
        raise RuntimeError(f"Failed to store run metadata: {e}")
    md_mgr.disconnect()
    
    output_dir.mkdir(parents=True, exist_ok=True)

    create_docker_compose(conf, output_dir / "docker-compose.yml")

    create_broker_runner(conf, output_dir / "broker_runner.json")
    create_grid_runner(conf, output_dir / "grid_runner.json")
    create_grid_config(conf, output_dir / "grid_config.json")

    if "house" in conf.federates:
        create_house_runner(conf, output_dir / "house_runner.json")

    if "controller" in conf.federates:
        create_controller_runner(conf, output_dir / "controller_runner.json")

    if "house_player" in conf.federates:
        create_house_player_runner(conf, output_dir / "house_player_runner.json")

    if "recorder" in conf.federates:
        create_recorder_runner(conf, output_dir / "recorder_runner.json")

    print("Legacy composegen output generated successfully.")


# ---------------------------------------------------------------------------
# Tree/CST output
# ---------------------------------------------------------------------------

def _create_metadata_manager(
    use_meta_db: str,
    meta_store_path: str = "generated",
):
    """Open the CST metadata store, pointing `json` at the output directory."""
    from cosim_toolbox.dbms import create_metadata_manager

    kwargs = {"backend": use_meta_db}

    if use_meta_db == "json":
        kwargs["location"] = meta_store_path

    return create_metadata_manager(**kwargs)


def map_params_to_class(federate_class: str) -> dict:
    """Map a config `class` to the Docker image and entry command that implement it."""
    mapping = {
        "grid": {
            "image": "grid",
            "command": "python3 main.py",
        },
        "house": {
            "image": "house",
            "command": "python3 house/main.py",
        },
        "load": {
            "image": "house_player",
            "command": "python3 main.py",
        },
        "house_player": {
            "image": "house_player",
            "command": "python3 main.py",
        },
        "pv": {
            "image": "house",
            "command": "python3 house/main.py",
        },
        "battery": {
            "image": "house",
            "command": "python3 house/main.py",
        },
        "hems": {
            "image": "controller",
            "command": "python3 controller/main.py",
        },
        "controller": {
            "image": "controller",
            "command": "python3 controller/main.py",
        },
        "recorder": {
            "image": "recorder",
            "command": "helics_recorder",
        },
    }


    return mapping.get(
        federate_class,
        {
            "image": "cosim-cst:latest",
            "command": "python3 main.py",
        },
    )


def _to_iso_wallclock(value: Any) -> str:
    """Render a simulation time as the ISO 8601 wall-clock string CST expects.

    ``transform.process_general_config`` already converts numeric simulation
    times into ISO strings using a 2023-01-01 base, so in practice the value
    arrives here as a string. Numeric values are converted against the same
    base so both paths agree on the epoch.
    """
    if isinstance(value, (int, float)):
        base_date = datetime(2023, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        return (
            (base_date + timedelta(seconds=float(value)))
            .isoformat()
            .replace("+00:00", "")
        )

    return str(value)


def annotate_scenario_with_run_metadata(
    scenario_name: str,
    run_metadata: dict,
    use_meta_db: str,
    meta_store_path: str = "generated",
) -> None:
    """Merge run provenance into the scenario document CST just wrote.

    ``FederationConfig.write_config`` writes the scenario document with
    ``overwrite=True`` and only the fields CST needs (analysis, federation,
    start/stop time, docker), so anything written beforehand is lost. This
    reads that document back and adds the provenance that makes a stored run
    reproducible: git commit, source experiment path, and the full experiment
    YAML as it was at run time.
    """
    with _create_metadata_manager(use_meta_db, meta_store_path) as mgr:
        scenario_doc = mgr.read_scenario(scenario_name) or {}
        scenario_doc.pop("_id", None)

        scenario_doc.update(
            {
                "run_timestamp": run_metadata["timestamp_iso"],
                "run_timestamp_unix": run_metadata["timestamp_unix"],
                "git_commit": run_metadata["git_commit"],
                "experiment_path": run_metadata["experiment_path"],
                "experiment_yaml_raw": run_metadata["experiment_yaml_raw"],
            }
        )

        mgr.write_scenario(scenario_name, scenario_doc, overwrite=True)

    print(f"Run recorded as scenario '{scenario_name}'.")


def normalize_federation_keys(
    federation_name: str,
    use_meta_db: str,
    meta_store_path: str = "generated",
) -> None:
    """Rewrite the dots CST puts in HELICS keys as the slashes federates parse."""
    with _create_metadata_manager(use_meta_db, meta_store_path) as mgr:
        config = mgr.read_federation(federation_name)

        if not config:
            print(
                f"Warning: Federation '{federation_name}' not found "
                f"in metadata backend '{use_meta_db}'."
            )
            return

        if "federation" in config:
            for _fed_name, fed_config in config["federation"].items():
                helics_cfg = fed_config.get("HELICS_config")

                if not helics_cfg:
                    continue

                for pub in helics_cfg.get("publications", []):
                    if "key" in pub:
                        pub["key"] = pub["key"].replace(".", "/")

                for sub in helics_cfg.get("subscriptions", []):
                    if "key" in sub:
                        sub["key"] = sub["key"].replace(".", "/")

        mgr.write_federation(federation_name, config, overwrite=True)

    print(
        f"Normalized HELICS keys in federation '{federation_name}' "
        f"using backend '{use_meta_db}'."
    )


def discover_grid_federates(
    grid_id: str,
    use_meta_db: str,
    meta_store_path: str = "generated",
    location: list[dict] | None = None,
) -> list[str]:
    """Find the grid federates the infdb stage created for a location query.

    A query giving only a `plz` can resolve to many nets, so those are found by scanning
    `custom_metadata` for the prefix rather than being named up front.
    """
    if not location:
        print(f"Warning: No location list provided for grid '{grid_id}'.")
        return []

    names: list[str] = []
    needs_scan: list[str] = []

    for entry in location:
        plz = entry.get("plz")
        kcid = entry.get("kcid")
        bcid = entry.get("bcid")

        if plz is None:
            print(
                f"Warning: Location entry {entry!r} for grid '{grid_id}' "
                f"is missing 'plz'. Skipping."
            )
            continue

        if kcid is not None and bcid is not None:
            names.append(f"{grid_id}_{plz}_{kcid}_{bcid}")
        else:
            needs_scan.append(f"{grid_id}_{plz}_")

    if needs_scan:
        with _create_metadata_manager(use_meta_db, meta_store_path) as mgr:
            all_keys = mgr.list_items("custom_metadata")

        for prefix in needs_scan:
            matched = [k for k in all_keys if k.startswith(prefix)]

            if not matched:
                print(
                    f"Warning: No custom_metadata entries found for prefix '{prefix}'. "
                    f"Was the InfDB step executed?"
                )

            names.extend(matched)

    return names


def _read_load_list(
    grid_fed_name: str,
    use_meta_db: str,
    meta_store_path: str = "generated",
) -> list[tuple[int, str, int]]:
    """Read a grid's `(load index, name, bus)` triples out of the net infdb stored."""
    with _create_metadata_manager(use_meta_db, meta_store_path) as mgr:
        meta_data = mgr.read("custom_metadata", grid_fed_name)

    # A missing or contentless entry is a defect in the infdb stage, not an
    # empty grid. Warning and returning [] let the caller wire its children on
    # keys the grid ignores, so the whole federation ran with every load at
    # 0 W and reported success - the same failure mode as B1, reached from a
    # different direction.
    if not meta_data:
        raise ValueError(
            f"No custom_metadata entry '{grid_fed_name}' in backend "
            f"'{use_meta_db}'. The grid's pandapower net is written there by "
            f"the infdb stage; re-run it before composegen."
        )

    net_json_raw = meta_data.get("net_json")
    net_json_str = (
        json.dumps(net_json_raw)
        if isinstance(net_json_raw, dict)
        else net_json_raw
    )

    if not net_json_str:
        raise ValueError(
            f"custom_metadata entry '{grid_fed_name}' carries no 'net_json', "
            f"so the grid has no pandapower net to place federates on."
        )

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
    placement: Any,
    all_loads: list[tuple[int, str, int]],
    exclude: set[int] | None = None,
) -> list[int]:
    """Turn a `placement` value - an index, a list, or `"fill"` - into load indices."""
    valid_indices = {idx for idx, _, _ in all_loads}

    if placement == "fill":
        exclude = exclude or set()
        return [idx for idx, _, _ in all_loads if idx not in exclude]

    if isinstance(placement, int):
        placement = [placement]

    if isinstance(placement, list):
        for p in placement:
            if p not in valid_indices:
                raise ValueError(
                    f"Placement error: pandapower load index {p} does not exist. "
                    f"Valid indices: {sorted(valid_indices)}"
                )

        return list(placement)

    raise ValueError(f"Unsupported placement value: {placement!r}")


def _resolve_load_placement(
    placement: Any,
    all_loads: list[tuple[int, str, int]],
    child_id: str,
    child_class: str,
    grid_fed_name: str,
    exclude: set[int] | None = None,
) -> list[int]:
    """Resolve a load-placed child's placement, refusing to resolve to nothing.

    ``_resolve_placement`` returns an empty list for ``"fill"`` against a grid
    with no loads, or against one whose loads are all claimed by siblings. The
    caller used to accept that and wire the child on a bare ``active_power``
    key, which the grid ignores - so the federate ran, published, and moved no
    power. Both cases are configuration errors and are named as such here.
    """
    if not all_loads:
        raise ValueError(
            f"'{child_id}' ({child_class}) has to be placed on a pandapower "
            f"load index, but grid '{grid_fed_name}' has no loads in its net."
        )

    load_indices = _resolve_placement(placement, all_loads, exclude=exclude)

    if not load_indices:
        raise ValueError(
            f"Placement {placement!r} for '{child_id}' ({child_class}) in grid "
            f"'{grid_fed_name}' resolved to no pandapower load index. Every "
            f"load in the grid is already claimed by a sibling federate: "
            f"{sorted(exclude or set())}."
        )

    return load_indices


def _report_unclaimed_loads(
    grid_fed_name: str,
    all_loads: list[tuple[int, str, int]],
    claimed: set[int],
) -> None:
    """Name the load indices no federate drives.

    Leaving a connection point empty is supported: the grid federate zeroes
    every static load at import, so an unclaimed index is already a zero-power
    load and needs no dummy federate. But an unclaimed index looks exactly
    like a placement typo, so say which ones they are rather than letting the
    run report a lower total load than expected without comment.
    """
    unclaimed = sorted({idx for idx, _, _ in all_loads} - claimed)

    if not unclaimed:
        return

    shown = unclaimed if len(unclaimed) <= 20 else unclaimed[:20] + ["..."]
    print(
        f"  {len(unclaimed)} of {len(all_loads)} load(s) in '{grid_fed_name}' "
        f"are not driven by any federate and stay at 0 W: {shown}"
    )


def _expand_child_instances(
    child_local_id: str,
    child_class: str,
    load_indices: list[int] | None,
) -> list[tuple[str, list[int] | None]]:
    """Split a grid child into the federate instances it should launch.

    A house federate simulates one building and drives exactly one
    pandapower load, so ``placement: [4, 6]`` becomes two independent house
    federates, each with its own sub-federates and its own single
    ``active_power`` publication. Instances are named after the load they
    drive rather than the declaring node, so ``house_0`` at ``[4, 6]``
    yields ``house_4`` and ``house_6``; the declared trailing index is only
    a placeholder and would otherwise survive as a misleading ``house_0_*``
    prefix. Load indices are unique across explicit placements, so the names
    cannot collide.

    A load player is the opposite case: one federate replays one profile
    across every load it is placed on, so it stays a single instance holding
    all its indices.

    Returns:
        ``(instance_id, load_indices_for_that_instance)`` pairs.
    """
    if child_class != "house":
        return [(child_local_id, load_indices)]

    if not load_indices:
        raise ValueError(
            f"House '{child_local_id}' resolved to no pandapower load index. "
            f"A house must be placed on at least one load."
        )

    base_id = re.sub(r"_\d+$", "", child_local_id)

    return [(f"{base_id}_{idx}", [idx]) for idx in load_indices]


def _add_group(
    federation,
    group_name: str,
    pub_fed: str,
    sub_fed: str,
    dtype: str = "double",
    unit: str = "W",
) -> None:
    """Register one global HELICS key with a single publisher and subscriber."""
    key_format = {
        "src": {
            "from_fed": pub_fed,
            "keys": ["", ""],
            "indices": [],
        },
        "des": [
            {
                "from_fed": pub_fed,
                "to_fed": sub_fed,
                "keys": ["", ""],
                "indices": [],
            }
        ],
    }

    federation.add_group(
        group_name,
        dtype,
        key_format,
        unit=unit,
        globl=True,
    )


def _add_source_only_group(
    federation,
    group_name: str,
    pub_fed: str,
    dtype: str = "double",
    unit: str = "W",
) -> None:
    """Register a global publication with no required subscriber.

    Used for data meant for consumption outside the HELICS federation
    (e.g. an external provider/recorder), where CST's own timeseries
    logging (triggered automatically for any real publication) is the
    point, not a specific in-federation subscriber.
    """
    key_format = {
        "src": {
            "from_fed": pub_fed,
            "keys": ["", ""],
            "indices": [],
        },
    }

    federation.add_group(
        group_name,
        dtype,
        key_format,
        unit=unit,
        globl=True,
    )


def _add_sink_only_group(
    federation,
    group_name: str,
    pub_fed: str,
    sub_fed: str,
    dtype: str = "double",
    unit: str = "W",
) -> None:
    """Subscribe to an already-published global key without re-registering it.

    Used when a second federate needs to read a source-only publication
    (see ``_add_source_only_group``): calling ``federation.add_group`` again
    would register a duplicate publication entry for the same key on the
    publisher's federate config, since it always adds a fresh output. This
    only touches the subscriber's side.
    """
    from cosim_toolbox.sims import HelicsSubGroup

    key_format = {
        "from_fed": pub_fed,
        "keys": ["", ""],
        "indices": [],
    }

    to_config = federation.federates[sub_fed]
    sub_group = HelicsSubGroup(group_name, dtype, key_format, unit=unit)
    to_config.inputs[to_config.unique()] = sub_group


# Every federate runs on the same period with no offset, so the whole
# federation shares one time axis: a simulation step has the same `sim_time`
# in every federate's recorded timeseries, and the run stops exactly at
# `end_time`.
#
# Ordering within a step is a separate concern from the clock. Staggering
# grant times by tree depth (the previous approach) bought the grid a fresh
# read at the cost of moving every federate onto its own timeline - grid
# samples landed at 2, 3602, 7202... and load samples at 1, 3601, 7201...,
# so no two federates ever shared a timestamp, every run overran end_time by
# the offset, and the t=0 record held a solve made before any child had
# published. It also broke down whenever `time_step` approached the
# whole-second offset, and CST rejects sub-second offsets outright because
# HelicsMsg.verify type-checks against an int default.
#
# Ordering is instead handled with HELICS's `wait_for_current_time_update`,
# which holds a federate's grant at time T until every other federate has
# completed T. HELICS rejects a federation where more than one federate sets
# it ("Multiple federates declaring wait_for_current_time flag will result
# in deadlock"), so it is a single federation-wide slot.
#
# The slot goes to the grid, because a power flow computed from last step's
# loads is the error that matters most here. Measured on
# experiment-local-grid, with the slot vs. without:
#   with:     t=3600 -> applies the loads published at 3600
#   without:  t=3600 -> applies the loads published at 0
#
# A federation with several independent grids (a PLZ-only location query can
# resolve to many) cannot give the slot to all of them, so it goes to none
# and every grid lags one step uniformly, rather than one grid silently
# behaving differently from its peers.
#
# What the slot does not buy: the grid publishes after everyone else, so
# voltage reaches its children a step late (currently unread), and the
# house/hems loop keeps a two-step round trip. HELICS iteration
# (helicsFederateRequestTimeIterative) is the change that would remove both
# and lift the multi-grid restriction; it belongs in the CST federate loop.
def _grid_waits_for_current_time(grid_fed_count: int) -> bool:
    """Decide whether the grid may claim the one ordering slot (see above)."""
    return grid_fed_count == 1


def _wire_house_subcomponents(
    federation,
    tree,
    house_node,
    house_fed_name: str,
    scenario_name: str,
    time_step: float,
    handled_nodes: set[str],
) -> str | None:
    """Wire a house's own sub-federates.

    Currently only ``hems`` is a real standalone federate: it's registered
    here and subscribed to the house's ``state`` publication so it can read
    battery SOC / forecasts. ``battery``/``pv`` sub-federates aren't
    implemented as standalone federates yet (that physics still lives
    inside the house's own simulator), so they're marked handled without
    generating a container for them instead of falling through to Phase 2's
    generic wiring, which would otherwise produce a broken device federate.

    Returns the hems federate's name if one was wired, so the caller can
    make it the publisher of the house's ``control`` topic instead of the
    unused grid-sourced one.
    """
    from cosim_toolbox.sims import FederateConfig

    hems_fed_name: str | None = None

    for child in tree.children(house_node.identifier):
        child_class = child.data.get("class")
        child_local_id = child.identifier.split(".")[-1]
        child_fed_name = f"{house_fed_name}.{child_local_id}"

        if child_class == "hems":
            hems_mapped = map_params_to_class("hems")

            hems_fed = FederateConfig(child_fed_name, period=time_step)
            federation.add_federate_config(hems_fed)
            hems_fed.config("image", hems_mapped["image"])
            hems_fed.config("federate_type", "value")
            # --house_federate tells the hems which house it controls, so it
            # can read that house's own exogenous dataset from the metadata
            # store for its forecast. Without it the controller falls back to
            # whatever dataset is hard-coded in its image, which silently
            # diverges from the house as soon as a house names a different
            # `exogenous_data` file.
            # --horizon keeps the controller's MPC window and the coverage
            # composegen validated for the house's exogenous dataset in step
            # (see transform.MPC_FORECAST_HORIZON_STEPS).
            hems_fed.config(
                "command",
                f"{hems_mapped['command']} "
                f"--scenario {scenario_name} --federate_name {child_fed_name} "
                f"--house_federate {house_fed_name} "
                f"--horizon {MPC_FORECAST_HORIZON_STEPS}",
            )

            _add_sink_only_group(
                federation,
                "state",
                house_fed_name,
                child_fed_name,
                "string",
                "json",
            )

            handled_nodes.add(child.identifier)
            hems_fed_name = child_fed_name
            print(f"  Added child federate: {child_fed_name} (hems)")

        elif child_class in ("battery", "pv"):
            handled_nodes.add(child.identifier)
            print(
                f"  Skipping '{child.identifier}' ({child_class}): not yet "
                "implemented as a standalone federate; its physics remains "
                "inside the house simulator."
            )

    return hems_fed_name


def _wire_grid_child(
    federation,
    grid_fed_name: str,
    child_fed_name: str,
    child_local_id: str,
    child_class: str,
    load_indices: list[int] | None = None,
    control_publisher: str | None = None,
) -> list[str]:
    """Register every HELICS key between a grid and one of its children.

    Returns the child's publication keys.
    """
    child_pub_keys: list[str] = []

    # Any child that occupies pandapower load indices - a load player *or* a
    # simulated house - is wired per index. The grid identifies incoming power
    # by the `load_<idx>` segment of the key, so a child that publishes a bare
    # `active_power` instead has its power silently dropped from the power
    # flow and never gets a voltage back.
    if load_indices is not None:
        for idx in load_indices:
            load_id = f"load_{idx}"

            _add_group(
                federation,
                f"{load_id}/active_power",
                child_fed_name,
                grid_fed_name,
                "double",
                "W",
            )

            child_pub_keys.append(
                f"{child_fed_name.replace('.', '/')}/{load_id}/active_power"
            )

            _add_group(
                federation,
                f"{load_id}/reactive_power",
                child_fed_name,
                grid_fed_name,
                "double",
                "VAr",
            )

            child_pub_keys.append(
                f"{child_fed_name.replace('.', '/')}/{load_id}/reactive_power"
            )

            _add_group(
                federation,
                f"{load_id}/voltage",
                grid_fed_name,
                child_fed_name,
                "double",
                "V",
            )

        print(f"    Wired {len(load_indices)} load(s).")

    else:
        _add_group(
            federation,
            f"{child_local_id}/voltage",
            grid_fed_name,
            child_fed_name,
            "double",
            "V",
        )

        if child_class in ("house", "load", "battery", "pv", "grid"):
            _add_group(
                federation,
                "active_power",
                child_fed_name,
                grid_fed_name,
                "double",
                "W",
            )

            child_pub_keys.append(
                f"{child_fed_name.replace('.', '/')}/active_power"
            )

            _add_group(
                federation,
                "reactive_power",
                child_fed_name,
                grid_fed_name,
                "double",
                "VAr",
            )

            child_pub_keys.append(
                f"{child_fed_name.replace('.', '/')}/reactive_power"
            )

    # Control and state are independent of how the child's power is wired:
    # a house needs them whether it sits on pandapower load indices or not.
    if child_class in ("house", "hems", "controller"):
        # A HELICS key is "<publisher federate>/<group name>". When the house's
        # own hems is the publisher, its federate name already carries the
        # house segment, so naming the group "<house>/control" repeated it:
        # local-grid/house_4/hems_0/house_4/control. The group is just
        # "control" in that case.
        #
        # When the grid publishes instead, every child's control topic comes
        # from the same federate, so the child id is what keeps them apart.
        control_group = (
            "control" if control_publisher else f"{child_local_id}/control"
        )

        _add_group(
            federation,
            control_group,
            control_publisher or grid_fed_name,
            child_fed_name,
            "string",
            "json",
        )

    if child_class == "house":
        _add_source_only_group(
            federation,
            "state",
            child_fed_name,
            "string",
            "json",
        )

        child_pub_keys.append(f"{child_fed_name.replace('.', '/')}/state")

    return child_pub_keys


def _resolve_timeseries_path(child_data: dict, federate_name: str) -> str:
    """Map a load federate's ``electrical_load`` to its container path.

    Only CSV files are supported. Anything else - notably the standard load
    profile names ``H0``/``H25`` that the schema accepts and the shipped
    configs use - previously fell back to ``sample_house.csv`` without a
    word, so the run looked successful while replaying a different building
    than the config asked for. ``validate_tree`` rejects those cases up
    front; this raise is the backstop, because there is no correct value to
    substitute here.
    """
    electrical_load = child_data.get("electrical_load")

    if not electrical_load or not str(electrical_load).endswith(".csv"):
        raise ValueError(
            f"Load '{federate_name}' has no usable 'electrical_load' CSV "
            f"(got {electrical_load!r}). Standard load profiles are not "
            f"implemented yet."
        )

    return f"/data/input/{electrical_load}"


def _store_house_exogenous_data(
    child_data: dict,
    child_fed_name: str,
    data_input_path: Path,
    use_meta_db: str,
    meta_store_path: str,
) -> None:
    """Load a house's exogenous dataset CSV and store it in the CST metadata store.

    Houses read this back via ``metadata_manager.read("custom_metadata", ...)``
    at runtime instead of mounting the CSV as a file, so the dataset travels
    through the same CST store as the pandapower net rather than a path arg.
    """
    exogenous_data = child_data.get("exogenous_data")

    if not exogenous_data:
        return

    csv_path = data_input_path / str(exogenous_data)

    if not csv_path.exists():
        raise FileNotFoundError(
            f"Exogenous dataset for '{child_fed_name}' not found: {csv_path}"
        )

    with _create_metadata_manager(use_meta_db, meta_store_path) as mgr:
        mgr.write(
            "custom_metadata",
            child_fed_name,
            {"exogenous_data_csv": csv_path.read_text(), "source_file": str(exogenous_data)},
            overwrite=True,
        )


def _add_generic_tree_pubsub_groups(
    federation,
    tree: Tree,
    handled_nodes: set[str],
) -> None:
    """Register groups for topics `wire_pub_sub` left on unhandled nodes."""
    topic_map: dict[str, dict] = {}

    for node in tree.all_nodes():
        if node.identifier in handled_nodes:
            continue

        data = node.data
        node_type = data.get("type")

        if not node_type or node_type == "empty":
            continue

        for topic, unit in data.get("publications", {}).items():
            dtype = "string" if unit == "json" else "double"

            topic_map.setdefault(
                topic,
                {
                    "publishers": [],
                    "subscribers": [],
                    "unit": unit,
                    "dtype": dtype,
                },
            )

            topic_map[topic]["publishers"].append(node.identifier)

        for topic, unit in data.get("subscriptions", {}).items():
            dtype = "string" if unit == "json" else "double"

            topic_map.setdefault(
                topic,
                {
                    "publishers": [],
                    "subscribers": [],
                    "unit": unit,
                    "dtype": dtype,
                },
            )

            topic_map[topic]["subscribers"].append(node.identifier)

    for topic, info in topic_map.items():
        publishers = info["publishers"]
        subscribers = info["subscribers"]
        unit = info["unit"]
        dtype = info["dtype"]

        if not publishers:
            continue

        for pub_fed in publishers:
            key_format = {
                "src": {
                    "from_fed": pub_fed,
                    "keys": ["", ""],
                    "indices": [],
                },
                "des": [],
            }

            for sub_fed in subscribers:
                key_format["des"].append(
                    {
                        "from_fed": pub_fed,
                        "to_fed": sub_fed,
                        "keys": ["", ""],
                        "indices": [],
                    }
                )

            normalized_topic = topic.replace(".", "/")
            normalized_pub_fed = pub_fed.replace(".", "/")

            group_name = normalized_topic

            if normalized_topic.startswith(normalized_pub_fed + "/"):
                group_name = normalized_topic[len(normalized_pub_fed) + 1:]

            federation.add_group(
                group_name,
                dtype,
                key_format,
                unit=unit,
                globl=True,
            )


def load_tree_cst_outputs(transformed: TransformedConfig) -> None:
    """Build the whole federation: place every federate, wire it, and write it out.

    Phase 1 handles the grids and their children, where placement resolves onto
    pandapower load indices; phase 2 registers whatever the tree holds beyond them.
    """
    try:
        from cosim_toolbox.sims import (
            FederationConfig,
            FederateConfig,
            DockerRunner,
        )

        try:
            from monkeypatch import apply_monkeypatches
            apply_monkeypatches()
        except ImportError:
            print("No monkeypatch module found. Continuing without monkeypatches.")

        tree = transformed.tree
        general_cfg = transformed.general_cfg or {}

        if tree is None:
            raise ValueError("Tree load expected transformed.tree, got None.")

        from transform import generate_run_metadata

        run_metadata = generate_run_metadata(
            experiment_path=transformed.config_path,
            general_cfg=general_cfg,
        )

        # The run's own timestamped name *is* the CST scenario the federates
        # run under, so every timeseries row is tagged with the run that
        # produced it. Using a constant name here (e.g. "TestGridScenario")
        # makes all runs pile into one indistinguishable, ever-growing table.
        scenario_name = run_metadata["scenario_name"]

        use_meta_db = general_cfg.get("use_meta_db", "json")
        meta_store_path = general_cfg.get("meta_store_path", "generated")

        name = general_cfg.get("name", "GridLock")
        use_data_db = general_cfg.get("use_data_db", "postgres")

        federation = FederationConfig(
            scenario_name,
            f"{name}Analysis",
            f"{name}Federation",
            True,
            use_meta_db,
            use_data_db,
        )

        time_step = general_cfg.get("time_step", 1.0)
        handled_nodes: set[str] = set()

        # Resolve every grid's federate names up front: the count decides
        # whether the single wait_for_current_time_update slot can be used.
        grid_federates_by_node: dict[str, list[str]] = {}

        for node in tree.all_nodes():
            if node.data.get("class") != "grid":
                continue

            if not node.data.get("location") and not node.data.get("layout"):
                continue

            if node.data.get("layout"):
                grid_federates_by_node[node.identifier] = [node.identifier]
            else:
                grid_federates_by_node[node.identifier] = discover_grid_federates(
                    node.identifier,
                    use_meta_db,
                    meta_store_path=meta_store_path,
                    location=node.data.get("location"),
                )

        grid_fed_count = sum(len(v) for v in grid_federates_by_node.values())
        grid_waits = _grid_waits_for_current_time(grid_fed_count)

        if not grid_waits and grid_fed_count > 1:
            print(
                f"Note: {grid_fed_count} grid federates in this federation. "
                f"HELICS allows only one federate to wait for the current "
                f"time update, so every grid will run its power flow on the "
                f"previous step's published loads."
            )

        # Phase 1: metadata-backed grids from InfDB or a local layout.
        for node in tree.all_nodes():
            if node.identifier not in grid_federates_by_node:
                continue

            grid_tree_id = node.identifier
            grid_fed_names = grid_federates_by_node[grid_tree_id]

            if not grid_fed_names:
                print(
                    f"Warning: Grid '{grid_tree_id}' has no registered metadata, "
                    f"but no custom_metadata entries were found."
                )
                continue

            children = tree.children(grid_tree_id)
            handled_nodes.add(grid_tree_id)

            for child in children:
                handled_nodes.add(child.identifier)

            grid_mapped = map_params_to_class("grid")

            for fed_name in grid_fed_names:
                fed = FederateConfig(
                    fed_name,
                    period=time_step,
                    wait_for_current_time_update=grid_waits,
                )
                federation.add_federate_config(fed)

                cmd = (
                    f"{grid_mapped['command']} "
                    f"--scenario {scenario_name} "
                    f"--federate_name {fed_name}"
                )

                fed.config("image", grid_mapped["image"])
                fed.config("command", cmd)
                fed.config("federate_type", "value")

                print(f"Added grid federate: {fed_name}")

                all_loads = _read_load_list(
                    fed_name,
                    use_meta_db,
                    meta_store_path=meta_store_path,
                )

                explicit_load_indices: set[int] = set()
                fill_children: list[str] = []
                for child in children:
                    if child.data.get("class") not in LOAD_PLACED_CLASSES:
                        continue
                    placement = child.data.get("placement")
                    if placement == "fill":
                        # 'fill' resolves to every load not claimed by an
                        # explicit placement, so a second one resolves to the
                        # same set and both federates would publish on the
                        # same load keys.
                        fill_children.append(child.identifier)
                        if len(fill_children) > 1:
                            raise ValueError(
                                f"Grid '{fed_name}' has more than one child "
                                f"with placement 'fill' "
                                f"({', '.join(sorted(fill_children))}). Only "
                                f"one federate can fill the remaining loads; "
                                f"give the others explicit load indices."
                            )
                        continue
                    claimed = set(_resolve_placement(placement, all_loads))
                    overlap = explicit_load_indices & claimed
                    if overlap:
                        raise ValueError(
                            f"Load index/indices {sorted(overlap)} in grid "
                            f"'{fed_name}' are claimed by multiple federates."
                        )
                    explicit_load_indices.update(claimed)

                claimed_load_indices: set[int] = set()

                for child in children:
                    child_data = child.data
                    child_class = child_data.get("class")

                    child_mapped = map_params_to_class(child_class)
                    child_local_id = child.identifier.split(".")[-1]

                    load_indices: list[int] | None = None

                    if child_class in LOAD_PLACED_CLASSES:
                        placement = child_data.get("placement")
                        load_indices = _resolve_load_placement(
                            placement,
                            all_loads,
                            child.identifier,
                            child_class,
                            fed_name,
                            exclude=explicit_load_indices,
                        )
                        claimed_load_indices.update(load_indices)

                        print(
                            f"  Resolved placement {placement!r} to "
                            f"{len(load_indices)} load(s)."
                        )

                    if child_class in UNIMPLEMENTED_GRID_CHILD_CLASSES:
                        raise NotImplementedError(
                            f"'{child.identifier}' is a {child_class} placed "
                            f"directly in grid '{fed_name}', but no standalone "
                            f"{child_class} federate exists yet - its physics "
                            f"lives inside the house simulator, and generating "
                            f"one here would start a house image with no "
                            f"exogenous dataset. Declare it as a sub-federate "
                            f"of a house instead."
                        )

                    for instance_id, instance_loads in _expand_child_instances(
                        child_local_id, child_class, load_indices
                    ):
                        child_fed_name = f"{fed_name}.{instance_id}"

                        child_fed = FederateConfig(
                            child_fed_name,
                            period=time_step,
                        )
                        federation.add_federate_config(child_fed)

                        child_fed.config("image", child_mapped["image"])
                        child_fed.config("federate_type", "value")

                        control_publisher = None

                        if child_class == "house":
                            control_publisher = _wire_house_subcomponents(
                                federation,
                                tree,
                                child,
                                child_fed_name,
                                scenario_name,
                                time_step,
                                handled_nodes,
                            )

                        _wire_grid_child(
                            federation,
                            fed_name,
                            child_fed_name,
                            instance_id,
                            child_class,
                            load_indices=instance_loads,
                            control_publisher=control_publisher,
                        )

                        child_cmd = (
                            f"{child_mapped['command']} "
                            f"--scenario {scenario_name} "
                            f"--federate_name {child_fed_name}"
                        )

                        if child_class == "load":
                            ts_path = _resolve_timeseries_path(
                                child_data, child_fed_name
                            )
                            child_cmd += f" --timeseries {ts_path}"

                        if child_class == "house":
                            _store_house_exogenous_data(
                                child_data,
                                child_fed_name,
                                transformed.data_input_path,
                                use_meta_db,
                                meta_store_path,
                            )

                        child_fed.config("command", child_cmd)

                        print(
                            f"  Added child federate: {child_fed_name} "
                            f"({child_class})"
                        )

                _report_unclaimed_loads(fed_name, all_loads, claimed_load_indices)

        # Phase 2: layout-based and already-expanded nodes.
        for node in tree.all_nodes():
            if node.identifier in handled_nodes:
                continue

            data = node.data
            node_class = data.get("class")
            node_type = data.get("type")

            if not node_type or node_type == "empty":
                continue

            fed = FederateConfig(
                node.identifier,
                period=time_step,
                wait_for_current_time_update=(node_class == "grid" and grid_waits),
            )
            federation.add_federate_config(fed)

            mapped = map_params_to_class(node_class)
            command = mapped["command"]

            if mapped["image"] in ["grid", "house_player", "house"]:
                command += f" --scenario {federation.scenario_name} --federate_name {node.identifier}"
                if node.data.get("config"):
                    command += f" --config {node.data['config']}"
            elif mapped["image"] in ["controller"]:
                command += f" --name {node.identifier}"

            fed.config("image", mapped["image"])
            fed.config("command", command)
            fed.config("federate_type", node_type)

        _add_generic_tree_pubsub_groups(federation, tree, handled_nodes)

        federation.define_io()

        start_str = _to_iso_wallclock(general_cfg.get("start_time", 0))
        end_str = _to_iso_wallclock(general_cfg.get("end_time", 0))

        print("Generating CST federation configuration...")

        federation.write_config(start_str, end_str)

        # write_config overwrites the scenario document with only the fields
        # CST itself needs, so run provenance has to be merged in afterwards.
        annotate_scenario_with_run_metadata(
            scenario_name,
            run_metadata,
            use_meta_db,
            meta_store_path=meta_store_path,
        )

        normalize_federation_keys(
            federation.federation_name,
            use_meta_db,
            meta_store_path=meta_store_path,
        )

        DockerRunner.define_yaml(
            federation.scenario_name,
            use_meta_db=use_meta_db,
            use_data_db=use_data_db,
        )

        print("Tree/CST federation configuration generated successfully.")

    except Exception as exc:
        print(f"Error generating tree/CST config: {exc}")
        traceback.print_exc()
        raise


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def load(transformed: TransformedConfig) -> None:
    """Dispatch to the tree or legacy output writer for a transformed config."""
    if transformed.mode == "legacy":
        load_legacy_outputs(transformed)
        return

    if transformed.mode == "tree":
        load_tree_cst_outputs(transformed)
        return

    raise ValueError(f"Unsupported transformed config mode: {transformed.mode}")
