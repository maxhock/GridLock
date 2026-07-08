# composegen/load.py
from __future__ import annotations

import json
import traceback
from pathlib import Path
from typing import Any

import yaml

from omegaconf import DictConfig
from treelib import Tree

from transform import TransformedConfig
from cosim_toolbox.dbms import create_metadata_manager


RUNNER_FEDERATES = {
    "grid",
    "house",
    "controller",
    "house_player",
}

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
    num_nodes = int(conf.federates.grid.num_nodes)
    fed_cfg = conf.federates.get(fed_key)

    if fed_cfg is None:
        return list(range(num_nodes))

    placement = fed_cfg.get("placement", None)

    if placement is None:
        return list(range(num_nodes))

    return [int(x) for x in placement]


def create_broker_runner(conf: DictConfig, output_path: Path) -> None:
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
    nodes = _get_nodes_for_fed(conf, "house")
    stop_time = float(conf.general.end_time - conf.general.start_time)
    dt = int(conf.general.time_step)
    config_file = conf.federates.house.get("config_file", "/config/house_config.yaml")

    federates = []

    for i in nodes:
        federates.append(
            {
                "directory": "/app",
                "exec": (
                    f"python house/main.py "
                    f"--name=house_{i} "
                    f"--config={config_file} "
                    f"--stop_time={stop_time} "
                    f"--dt={dt}"
                ),
                "host": "localhost",
                "name": f"house_{i}",
            }
        )

    _runner_write(output_path, "house_federation", federates)


def create_controller_runner(conf: DictConfig, output_path: Path) -> None:
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
    conf = transformed.conf
    output_dir = Path(transformed.output_path)

    if conf is None:
        raise ValueError("Legacy load expected transformed.conf, got None.")

    # Generate and store run metadata for legacy mode
    from transform import generate_run_metadata
    
    general_cfg = {
        "name": conf.federates.grid.get("name", "GridLock"),
        "start_time": str(conf.general.get("start_time", "0")),
        "end_time": str(conf.general.get("end_time", "0")),
        "use_meta_db": "json",
        "use_data_db": "postgres",
    }
    
    run_metadata = generate_run_metadata(
        experiment_path=transformed.config_path,
        general_cfg=general_cfg
    )
    
    scenario_name = run_metadata["scenario_name"]
    
    # CST expects ISO 8601 format for start_time and stop_time (wall-clock time, not simulation time)
    # Simulation times (e.g., 0, 86400) need to be converted to ISO 8601
    start_time_val = general_cfg.get("start_time", 0)
    end_time_val = general_cfg.get("end_time", 0)
    
    # Use a base date and add simulation seconds to get wall-clock ISO 8601
    from datetime import datetime, timedelta, timezone
    base_date = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    
    if isinstance(start_time_val, (int, float)):
        start_time_iso = (base_date + timedelta(seconds=float(start_time_val))).isoformat().replace("+00:00", "")
    else:
        start_time_iso = str(start_time_val)
        
    if isinstance(end_time_val, (int, float)):
        end_time_iso = (base_date + timedelta(seconds=float(end_time_val))).isoformat().replace("+00:00", "")
    else:
        end_time_iso = str(end_time_val)
    
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
    from cosim_toolbox.dbms import create_metadata_manager

    kwargs = {"backend": use_meta_db}

    if use_meta_db == "json":
        kwargs["location"] = meta_store_path

    return create_metadata_manager(**kwargs)


def map_params_to_class(federate_class: str) -> dict:
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
        "house_player": {
            "image": "house_player",
            "command": "python3 main.py",
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
            "image": "controller",
            "command": "python3 main.py",
        },
        "controller": {
            "image": "controller",
            "command": "python3 main.py",
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


def normalize_federation_keys(
    federation_name: str,
    use_meta_db: str,
    meta_store_path: str = "generated",
) -> None:
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
    with _create_metadata_manager(use_meta_db, meta_store_path) as mgr:
        meta_data = mgr.read("custom_metadata", grid_fed_name)

    if not meta_data:
        print(
            f"Warning: custom_metadata '{grid_fed_name}' not found "
            f"in backend '{use_meta_db}'."
        )
        return []

    net_json_raw = meta_data.get("net_json")
    net_json_str = (
        json.dumps(net_json_raw)
        if isinstance(net_json_raw, dict)
        else net_json_raw
    )

    if not net_json_str:
        print(f"Warning: no net_json in custom_metadata '{grid_fed_name}'.")
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
    placement: Any,
    all_loads: list[tuple[int, str, int]],
) -> list[int]:
    valid_indices = {idx for idx, _, _ in all_loads}

    if placement == "fill":
        return [idx for idx, _, _ in all_loads]

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


def _add_group(
    federation,
    group_name: str,
    pub_fed: str,
    sub_fed: str,
    dtype: str = "double",
    unit: str = "W",
) -> None:
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


def _wire_grid_child(
    federation,
    grid_fed_name: str,
    child_fed_name: str,
    child_local_id: str,
    child_class: str,
    load_indices: list[int] | None = None,
) -> list[str]:
    child_pub_keys: list[str] = []


    if child_class == "load" and load_indices is not None:
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

        if child_class in ("house", "hems", "controller"):
            _add_group(
                federation,
                f"{child_local_id}/control",
                grid_fed_name,
                child_fed_name,
                "string",
                "json",
            )

    return child_pub_keys


def _resolve_timeseries_path(child_data: dict) -> str:
    electrical_load = child_data.get("electrical_load")

    if not electrical_load:
        return ""

    if str(electrical_load).endswith(".csv"):
        return f"/data/input/{electrical_load}"

    return "/data/input/sample_house.csv"


def _add_generic_tree_pubsub_groups(
    federation,
    tree: Tree,
    handled_nodes: set[str],
) -> None:
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

        # Generate and store run metadata
        from transform import generate_run_metadata
        
        run_metadata = generate_run_metadata(
            experiment_path=transformed.config_path,
            general_cfg=general_cfg
        )
        
        scenario_name = run_metadata["scenario_name"]
        
        # Create scenario metadata for CST
        # CST expects ISO 8601 format for start_time and stop_time (wall-clock time, not simulation time)
        # Simulation times (e.g., 0, 86400) need to be converted to ISO 8601
        start_time_val = general_cfg.get("start_time", 0)
        end_time_val = general_cfg.get("end_time", 0)
        
        # Use a base date and add simulation seconds to get wall-clock ISO 8601
        from datetime import datetime, timedelta, timezone
        base_date = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        
        if isinstance(start_time_val, (int, float)):
            start_time_iso = (base_date + timedelta(seconds=float(start_time_val))).isoformat().replace("+00:00", "")
        else:
            start_time_iso = str(start_time_val)
            
        if isinstance(end_time_val, (int, float)):
            end_time_iso = (base_date + timedelta(seconds=float(end_time_val))).isoformat().replace("+00:00", "")
        else:
            end_time_iso = str(end_time_val)
        
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
        
        # Write to metadata store
        use_meta_db = general_cfg.get("use_meta_db", "json")
        meta_store_path = general_cfg.get("meta_store_path", "generated")
        
        md_kwargs = {"backend": use_meta_db}
        if use_meta_db == "json":
            md_kwargs["location"] = meta_store_path
        
        md_mgr = create_metadata_manager(**md_kwargs)
        md_mgr.connect()
        try:
            md_mgr.writer.write_scenario(scenario_name, scenario_metadata)
            print(f"Stored run metadata for scenario: {scenario_name}")
        except Exception as e:
            md_mgr.disconnect()
            raise RuntimeError(f"Failed to store run metadata: {e}")
        md_mgr.disconnect()
        
        name = general_cfg.get("name", "GridLock")
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
        handled_nodes: set[str] = set()

        # Phase 1: location-based grids.
        for node in tree.all_nodes():
            if node.data.get("class") != "grid":
                continue

            if not node.data.get("location"):
                continue

            grid_tree_id = node.identifier

            grid_fed_names = discover_grid_federates(
                grid_tree_id,
                use_meta_db,
                meta_store_path=meta_store_path,
                location=node.data.get("location"),
            )

            if not grid_fed_names:
                print(
                    f"Warning: Grid '{grid_tree_id}' uses location queries, "
                    f"but no custom_metadata entries were found."
                )
                continue

            children = tree.children(grid_tree_id)
            handled_nodes.add(grid_tree_id)

            for child in children:
                handled_nodes.add(child.identifier)

            grid_mapped = map_params_to_class("grid")

            for fed_name in grid_fed_names:
                fed = FederateConfig(fed_name, period=time_step)
                federation.add_federate_config(fed)

                cmd = (
                    f"{grid_mapped['command']} "
                    f"--scenario {name}Scenario "
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

                    load_indices: list[int] | None = None

                    if child_class == "load" and all_loads:
                        placement = child_data.get("placement")
                        load_indices = _resolve_placement(placement, all_loads)

                        print(
                            f"  Resolved placement {placement!r} to "
                            f"{len(load_indices)} load(s)."
                        )

                    _wire_grid_child(
                        federation,
                        fed_name,
                        child_fed_name,
                        child_local_id,
                        child_class,
                        load_indices=load_indices,
                    )

                    child_cmd = (
                        f"{child_mapped['command']} "
                        f"--scenario {name}Scenario "
                        f"--federate_name {child_fed_name}"
                    )

                    if child_class == "load":
                        ts_path = _resolve_timeseries_path(child_data)

                        if ts_path:
                            child_cmd += f" --timeseries {ts_path}"

                    child_fed.config("command", child_cmd)

                    print(f"  Added child federate: {child_fed_name} ({child_class})")

        # Phase 2: layout-based and already-expanded nodes.
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
            command = mapped["command"]

            if mapped["image"] in ["grid", "house_player"]:
                command += f" --scenario {federation.scenario_name} --federate_name {node.identifier}"
            elif mapped["image"] in ["house", "controller"]:
                command += f" --name {node.identifier}"

            fed.config("image", mapped["image"])
            fed.config("command", command)
            fed.config("federate_type", node_type)

        _add_generic_tree_pubsub_groups(federation, tree, handled_nodes)

        federation.define_io()

        # Convert simulation times to ISO 8601 wall-clock times for CST
        from datetime import datetime, timedelta, timezone
        base_date = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        
        start_time_val = general_cfg.get("start_time", 0)
        end_time_val = general_cfg.get("end_time", 0)
        
        if isinstance(start_time_val, (int, float)):
            start_str = (base_date + timedelta(seconds=float(start_time_val))).isoformat().replace("+00:00", "")
        else:
            start_str = str(start_time_val)
            
        if isinstance(end_time_val, (int, float)):
            end_str = (base_date + timedelta(seconds=float(end_time_val))).isoformat().replace("+00:00", "")
        else:
            end_str = str(end_time_val)

        print("Generating CST federation configuration...")

        federation.write_config(start_str, end_str)

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
    if transformed.mode == "legacy":
        load_legacy_outputs(transformed)
        return

    if transformed.mode == "tree":
        load_tree_cst_outputs(transformed)
        return

    raise ValueError(f"Unsupported transformed config mode: {transformed.mode}")
