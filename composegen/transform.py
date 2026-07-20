# composegen/transform.py
from __future__ import annotations

import copy
import os
import yaml
import pandas as pd
from datetime import datetime

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from omegaconf import DictConfig, OmegaConf
from treelib import Tree

from extract import ExtractedConfig


ConfigMode = Literal["legacy", "tree"]


@dataclass
class TransformedConfig:
    mode: ConfigMode
    conf: DictConfig | None = None
    tree: Tree | None = None
    general_cfg: dict[str, Any] | None = None
    output_path: Any = None
    data_input_path: Any = None
    config_path: str | None = None


SUPPORTED_TREE_CLASSES = {
    "grid",
    "house",
    "load",
    "pv",
    "battery",
    "hems",
    "controller",
    "recorder",
    "empty",
}


# ---------------------------------------------------------------------------
# Legacy transform
# ---------------------------------------------------------------------------

def _get_nodes_for_fed(conf: DictConfig, fed_key: str) -> list[int]:
    num_nodes = int(conf.federates.grid.num_nodes)
    fed_cfg = conf.federates.get(fed_key)

    if fed_cfg is None:
        return list(range(num_nodes))

    placement = fed_cfg.get("placement", None)

    if placement is None:
        return list(range(num_nodes))

    return [int(x) for x in placement]


def _transform_legacy_config(extracted: ExtractedConfig) -> TransformedConfig:
    conf: DictConfig = extracted.raw_config


    num_nodes = int(extracted.metadata["num_nodes"])

    for fed_key, fed_cfg in conf.federates.items():
        if "name" not in fed_cfg or not fed_cfg.name:
            fed_cfg.name = fed_key

        if "build_folder" not in fed_cfg or not fed_cfg.build_folder:
            fed_cfg.build_folder = f"federates/{fed_cfg.name}"

    conf.federates.grid.num_nodes = num_nodes

    for fed_key, fed_cfg in conf.federates.items():
        if fed_key in {"broker", "grid", "recorder"}:
            fed_cfg.num_instances = 1

        elif fed_key in {"house", "controller", "house_player"}:
            fed_cfg.num_instances = len(_get_nodes_for_fed(conf, fed_key))

        else:
            fed_cfg.num_instances = int(fed_cfg.get("num_instances", 1))

    total = 0

    for fed_key, fed_cfg in conf.federates.items():
        if fed_key == "broker":
            continue

        total += int(fed_cfg.get("num_instances", 1))

    conf.federates.broker.total_federates = int(total)

    print(f"Total federates excluding broker: {total}")

    OmegaConf.resolve(conf)

    return TransformedConfig(
        mode="legacy",
        conf=conf,
        output_path=extracted.output_path,
        data_input_path=extracted.data_input_path,
        config_path=str(extracted.config_path),
    )


# ---------------------------------------------------------------------------
# Tree/CST transform
# ---------------------------------------------------------------------------

def _clone_subtree_with_new_root(
    template: Tree,
    old_root_id: str,
    new_root_id: str,
    placement_bus: int,
) -> Tree:
    new_tree = Tree()
    id_map: dict[str, str] = {}

    for old_id in template.expand_tree(mode=Tree.DEPTH):
        if old_id == old_root_id:
            new_id = new_root_id
        elif old_id.startswith(old_root_id):
            new_id = old_id.replace(old_root_id, new_root_id, 1)
        else:
            new_id = f"{new_root_id}/{old_id}"

        id_map[old_id] = new_id

    for old_id in template.expand_tree(mode=Tree.DEPTH):
        old_node = template.get_node(old_id)
        new_id = id_map[old_id]

        parent_node = template.parent(old_id)
        new_parent = id_map[parent_node.identifier] if parent_node else None

        new_data = copy.deepcopy(old_node.data)

        if old_id == old_root_id:
            new_data["placement"] = placement_bus
            new_data["bus"] = placement_bus

        new_tree.create_node(
            tag=old_node.tag,
            identifier=new_id,
            parent=new_parent,
            data=new_data,
        )

    return new_tree


def expand_grid_nodes(tree: Tree, grid_nodes: dict[str, list[int] | None]) -> Tree:
    for grid_id, buses in grid_nodes.items():
        if not tree.contains(grid_id):
            continue

        # Location-based grids are expanded later in load.py by using metadata.
        if buses is None:
            continue

        explicit_placements: dict[int, Tree] = {}
        fill_templates: list[Tree] = []

        children = list(tree.children(grid_id))

        for child in children:
            placement = child.data.get("placement")
            template_subtree = tree.remove_subtree(child.identifier)

            if isinstance(placement, int):
                placement = [placement]

            if isinstance(placement, list):
                for p in placement:
                    p = int(p)

                    if p in explicit_placements:
                        raise ValueError(
                            f"Bus {p} in grid '{grid_id}' is claimed by multiple federates."
                        )

                    if p not in buses:
                        raise ValueError(
                            f"Federate '{child.tag}' is placed on bus {p}, "
                            f"but this bus does not exist in grid '{grid_id}'."
                        )

                    explicit_placements[p] = template_subtree

            elif placement == "fill":
                fill_templates.append(template_subtree)

            else:
                raise ValueError(
                    f"Federate '{child.tag}' requires valid placement. "
                    f"Use an int, list[int], or 'fill'."
                )

        for bus in buses:
            new_root_id = f"{grid_id}/bus_{bus}"

            if bus in explicit_placements:
                template = explicit_placements[bus]

            elif fill_templates:
                template = fill_templates[0]

            else:
                tree.create_node(
                    tag=f"Empty Slot {bus}",
                    identifier=new_root_id,
                    parent=grid_id,
                    data={
                        "type": "empty",
                        "class": "empty",
                        "bus": bus,
                        "placement": bus,
                    },
                )
                continue

            subtree_to_paste = _clone_subtree_with_new_root(
                template=template,
                old_root_id=template.root,
                new_root_id=new_root_id,
                placement_bus=bus,
            )

            tree.paste(grid_id, subtree_to_paste)

    return tree


def add_pub_sub(node, topic: str, unit: str, kind: str = "publication") -> None:
    key = "publications" if kind == "publication" else "subscriptions"

    if key not in node.data:
        node.data[key] = {}

    node.data[key][topic] = unit


def validate_tree(tree: Tree) -> None:
    validation_errors: list[str] = []

    for node in tree.all_nodes():
        data = node.data
        node_class = data.get("class")


        if not node_class:
            validation_errors.append(
                f"[Structure] Node '{node.tag}' ({node.identifier}) is missing 'class'."
            )
            continue

        if node_class not in SUPPORTED_TREE_CLASSES:
            validation_errors.append(
                f"[Structure] Node '{node.tag}' ({node.identifier}) has unsupported "
                f"class '{node_class}'. Supported classes: {sorted(SUPPORTED_TREE_CLASSES)}"
            )
            continue

        if node_class == "grid":
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

        elif node_class == "load":
            if not data.get("electrical_load") and not data.get("heat_load"):
                validation_errors.append(
                    f"[Load] Node '{node.tag}' ({node.identifier}) requires "
                    f"'electrical_load' or 'heat_load'."
                )

        elif node_class == "house":
            if not data.get("model"):
                validation_errors.append(
                    f"[House] Node '{node.tag}' ({node.identifier}) requires 'model'."
                )

        elif node_class == "pv":
            if not data.get("max_production"):
                validation_errors.append(
                    f"[PV] Node '{node.tag}' ({node.identifier}) requires "
                    f"'max_production'."
                )

            if not data.get("capacity"):
                validation_errors.append(
                    f"[PV] Node '{node.tag}' ({node.identifier}) requires 'capacity'."
                )

        elif node_class == "battery":
            if not data.get("capacity"):
                validation_errors.append(
                    f"[Battery] Node '{node.tag}' ({node.identifier}) requires 'capacity'."
                )

            if not data.get("power"):
                validation_errors.append(
                    f"[Battery] Node '{node.tag}' ({node.identifier}) requires 'power'."
                )

        elif node_class == "hems":
            if not data.get("control_strategy"):
                validation_errors.append(
                    f"[HEMS] Node '{node.tag}' ({node.identifier}) requires "
                    f"'control_strategy'."
                )

    if validation_errors:
        print("Configuration invalid:")

        for error in validation_errors:
            print(f" - {error}")

        raise ValueError("Configuration validation failed.")


def validate_timeseries_coverage(
    tree: Tree, general_cfg: dict, data_input_path: Path
) -> None:
    """Ensure every load timeseries CSV covers the full simulation duration.

    Without this, a load-player federate silently holds its last known
    value once the CSV runs out, producing a flat/stale load for the
    remainder of the run instead of failing.
    """
    start_time = general_cfg.get("start_time") or 0
    end_time = general_cfg.get("end_time")

    if not isinstance(start_time, (int, float)) or not isinstance(end_time, (int, float)):
        return

    duration = float(end_time) - float(start_time)
    validation_errors: list[str] = []

    for node in tree.all_nodes():
        data = node.data
        if data.get("class") != "load":
            continue

        for field in ("electrical_load", "heat_load"):
            csv_name = data.get(field)
            if not csv_name or not str(csv_name).endswith(".csv"):
                continue

            csv_path = data_input_path / csv_name
            if not csv_path.exists():
                validation_errors.append(
                    f"[Timeseries] Node '{node.tag}' ({node.identifier}): "
                    f"'{field}' file '{csv_path}' not found."
                )
                continue

            timestamps = pd.read_csv(csv_path, usecols=["timestamp"])["timestamp"]

            if timestamps.dtype == object:
                timestamps = pd.to_datetime(timestamps).astype("int64") // 10**9

            first_ts = float(timestamps.min())
            if first_ts > 1_000_000:
                timestamps = timestamps - first_ts

            covered = float(timestamps.max())

            if covered < duration:
                validation_errors.append(
                    f"[Timeseries] Node '{node.tag}' ({node.identifier}): "
                    f"'{field}' file '{csv_name}' only covers {covered:.0f}s "
                    f"but the experiment runs for {duration:.0f}s "
                    f"(start_time={start_time}, end_time={end_time}). Provide a "
                    f"longer timeseries or shorten the experiment duration."
                )

    if validation_errors:
        print("Timeseries coverage invalid:")

        for error in validation_errors:
            print(f" - {error}")

        raise ValueError("Timeseries coverage validation failed.")


def process_general_config(general_cfg: dict) -> dict:
    if "end_time" not in general_cfg or general_cfg["end_time"] is None:
        raise ValueError("[General] Missing required field 'end_time'.")

    if not general_cfg.get("start_time"):
        general_cfg["start_time"] = "2023-01-01T00:00:00"
    elif isinstance(general_cfg["start_time"], (int, float)):
        base_ts = pd.Timestamp("2023-01-01T00:00:00")
        general_cfg["start_time"] = (base_ts + pd.Timedelta(seconds=float(general_cfg["start_time"]))).isoformat()

    if "time_step" not in general_cfg or general_cfg["time_step"] is None:
        general_cfg["time_step"] = 1.0

    start_time = general_cfg["start_time"]
    end_time = general_cfg["end_time"]

    try:
        start_ts = pd.Timestamp(start_time)

        if isinstance(end_time, (int, float)):
            end_ts = start_ts + pd.Timedelta(seconds=float(end_time))
            general_cfg["end_time"] = end_ts.isoformat()

        elif isinstance(end_time, str):
            pd.Timestamp(end_time)

        else:
            raise ValueError(f"End time format not recognized: {end_time}")

    except Exception as exc:
        raise ValueError(f"Timestamp logic failed: {exc}") from exc

    return general_cfg


def wire_pub_sub(tree: Tree) -> None:
    for node in tree.all_nodes():
        node.data.pop("publications", None)
        node.data.pop("subscriptions", None)

    for parent_node in tree.all_nodes():
        parent_class = parent_node.data.get("class")
        children = tree.children(parent_node.identifier)

        for child_node in children:
            child_class = child_node.data.get("class")

            if parent_class == "grid":
                voltage_topic = f"{child_node.identifier}/voltage"

                add_pub_sub(parent_node, voltage_topic, "V", "publication")
                add_pub_sub(child_node, voltage_topic, "V", "subscription")

                if child_class in ["house", "load", "battery", "pv", "grid"]:
                    p_topic = f"{child_node.identifier}/active_power"
                    q_topic = f"{child_node.identifier}/reactive_power"

                    add_pub_sub(parent_node, p_topic, "W", "subscription")
                    add_pub_sub(parent_node, q_topic, "VAr", "subscription")

                    add_pub_sub(child_node, p_topic, "W", "publication")
                    add_pub_sub(child_node, q_topic, "VAr", "publication")

                if child_class in ["house", "hems", "controller"]:
                    control_topic = f"{child_node.identifier}/control"

                    add_pub_sub(parent_node, control_topic, "json", "publication")
                    add_pub_sub(child_node, control_topic, "json", "subscription")

            elif parent_class == "house":
                if child_class in ["pv", "battery", "hems", "controller"]:
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


def _transform_tree_config(extracted: ExtractedConfig) -> TransformedConfig:
    if extracted.tree is None:
        raise ValueError("Tree config expected, but extracted.tree is None.")

    tree = extracted.tree

    validate_tree(tree)
    expand_grid_nodes(tree, extracted.grid_nodes)

    general_cfg = extracted.raw_config.get("general", {})
    validate_timeseries_coverage(tree, general_cfg, extracted.data_input_path)
    general_cfg = process_general_config(general_cfg)

    wire_pub_sub(tree)

    return TransformedConfig(
        mode="tree",
        tree=tree,
        general_cfg=general_cfg,
        output_path=extracted.output_path,
        data_input_path=extracted.data_input_path,
        config_path=str(extracted.config_path),
    )


# ---------------------------------------------------------------------------
# Run metadata generation
# ---------------------------------------------------------------------------

def get_git_commit() -> str:
    """Get git commit hash from environment variable."""
    return os.getenv("GIT_COMMIT", "unknown")


def generate_run_metadata(experiment_path: str, general_cfg: dict) -> dict:
    """Generate metadata for this run."""
    now = datetime.now()
    timestamp_iso = now.strftime("%Y%m%d_%H%M%S")
    timestamp_unix = int(now.timestamp())
    
    # Read full experiment file as raw YAML string
    with open(experiment_path, 'r') as f:
        experiment_yaml_raw = f.read()
    
    analysis_name = general_cfg.get('name', 'GridLock')
    scenario_name = f"{analysis_name}_{timestamp_iso}"
    
    return {
        "timestamp_iso": timestamp_iso,
        "timestamp_unix": timestamp_unix,
        "scenario_name": scenario_name,
        "analysis": analysis_name,
        "git_commit": get_git_commit(),
        "experiment_path": experiment_path,
        "experiment_yaml_raw": experiment_yaml_raw,
    }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def transform(extracted: ExtractedConfig) -> TransformedConfig:
    if extracted.mode == "legacy":
        return _transform_legacy_config(extracted)

    if extracted.mode == "tree":
        return _transform_tree_config(extracted)

    raise ValueError(f"Unsupported config mode: {extracted.mode}")
