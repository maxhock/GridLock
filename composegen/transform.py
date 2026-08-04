"""ETL stage 2: validate the extracted config and wire the federates' HELICS topics."""

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
    """A validated federation, ready for `load.py` to write out."""

    mode: ConfigMode
    conf: DictConfig | None = None
    tree: Tree | None = None
    general_cfg: dict[str, Any] | None = None
    output_path: Any = None
    data_input_path: Any = None
    config_path: str | None = None


# Steps of load/PV/price forecast the HEMS optimises over. composegen owns this
# number: it decides how much exogenous data a house with a HEMS needs, and it
# is passed to the controller federate on the command line so the two cannot
# disagree. federates/controller/main.py only falls back to its own default
# when run by hand.
MPC_FORECAST_HORIZON_STEPS = 24

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
    """Resolve a legacy federate's placement, defaulting to every node in the grid."""
    num_nodes = int(conf.federates.grid.num_nodes)
    fed_cfg = conf.federates.get(fed_key)

    if fed_cfg is None:
        return list(range(num_nodes))

    placement = fed_cfg.get("placement", None)

    if placement is None:
        return list(range(num_nodes))

    return [int(x) for x in placement]


def _transform_legacy_config(extracted: ExtractedConfig) -> TransformedConfig:
    """Fill in the defaults and instance counts a legacy config leaves implicit."""
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
    """Copy a federate subtree onto one bus, rewriting every id under a new root."""
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
    """Replicate each grid child onto the buses it is placed on.

    Dead code: `extract` sets every `grid_nodes` entry to `None`, so the loop below
    always skips. Placement is resolved on load indices in `load.py` - change it there.
    """
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
    """Record one HELICS topic and its unit on a tree node."""
    key = "publications" if kind == "publication" else "subscriptions"

    if key not in node.data:
        node.data[key] = {}

    node.data[key][topic] = unit


def validate_tree(tree: Tree) -> None:
    """Report every missing or unsupported field at once, then refuse to run.

    Collected rather than raised one at a time so a misconfigured experiment is fixed in
    one pass instead of one container start per mistake.
    """
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
            electrical_load = data.get("electrical_load")
            heat_load = data.get("heat_load")

            if not electrical_load and not heat_load:
                validation_errors.append(
                    f"[Load] Node '{node.tag}' ({node.identifier}) requires "
                    f"'electrical_load' or 'heat_load'."
                )

            elif not electrical_load:
                validation_errors.append(
                    f"[Load] Node '{node.tag}' ({node.identifier}) defines only "
                    f"'heat_load'. Heat loads are not implemented yet; give it "
                    f"an 'electrical_load' CSV."
                )

            elif not str(electrical_load).endswith(".csv"):
                validation_errors.append(
                    f"[Load] Node '{node.tag}' ({node.identifier}) requests "
                    f"electrical_load '{electrical_load}'. Standard load "
                    f"profiles are not implemented yet; give it a CSV file in "
                    f"the data input directory."
                )

        elif node_class == "house":
            if not data.get("model"):
                validation_errors.append(
                    f"[House] Node '{node.tag}' ({node.identifier}) requires 'model'."
                )

            if not data.get("exogenous_data"):
                validation_errors.append(
                    f"[House] Node '{node.tag}' ({node.identifier}) requires "
                    f"'exogenous_data'."
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


def _forecast_margin_seconds(tree: Tree, node, time_step: float) -> float:
    """Extra coverage a house needs beyond the run itself, in seconds.

    A house controlled by a HEMS is read past the end of the run: at the last
    simulation step the MPC still asks for a full window of forecast. Without
    the margin the solver is handed a short window, which it cannot use - so
    the requirement belongs here, before anything starts, rather than being
    papered over at runtime.
    """
    if node.data.get("class") != "house":
        return 0.0

    has_controller = any(
        child.data.get("class") in ("hems", "controller")
        for child in tree.children(node.identifier)
    )

    return MPC_FORECAST_HORIZON_STEPS * time_step if has_controller else 0.0


def validate_timeseries_coverage(
    tree: Tree, general_cfg: dict, data_input_path: Path
) -> None:
    """Ensure every timeseries CSV covers everything that will be read from it.

    Without this, a load-player federate silently holds its last known
    value once the CSV runs out, producing a flat/stale load for the
    remainder of the run instead of failing.

    A CSV longer than needed is fine and stays untouched: federates index into
    it by simulation step, so the run simply stops before the surplus.
    """
    start_time = general_cfg.get("start_time") or 0
    end_time = general_cfg.get("end_time")

    if not isinstance(start_time, (int, float)) or not isinstance(end_time, (int, float)):
        return

    duration = float(end_time) - float(start_time)
    # process_general_config has not run yet, so apply the same default it does.
    time_step = float(general_cfg.get("time_step") or 1)
    validation_errors: list[str] = []

    fields_by_class = {
        "load": ("electrical_load", "heat_load"),
        "house": ("exogenous_data",),
    }

    for node in tree.all_nodes():
        data = node.data
        fields = fields_by_class.get(data.get("class"))
        if not fields:
            continue

        margin = _forecast_margin_seconds(tree, node, time_step)
        required = duration + margin

        for field in fields:
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

            if covered < required:
                because = (
                    f"the experiment runs for {duration:.0f}s "
                    f"(start_time={start_time}, end_time={end_time})"
                )

                if margin:
                    because += (
                        f" and its HEMS needs a further {margin:.0f}s "
                        f"({MPC_FORECAST_HORIZON_STEPS} steps of "
                        f"{time_step:.0f}s) of forecast at the last step"
                    )

                validation_errors.append(
                    f"[Timeseries] Node '{node.tag}' ({node.identifier}): "
                    f"'{field}' file '{csv_name}' only covers {covered:.0f}s "
                    f"but {required:.0f}s are needed - {because}. Provide a "
                    f"longer timeseries or shorten the experiment duration."
                )

    if validation_errors:
        print("Timeseries coverage invalid:")

        for error in validation_errors:
            print(f" - {error}")

        raise ValueError("Timeseries coverage validation failed.")


def process_general_config(general_cfg: dict) -> dict:
    """Normalise the `general:` block to ISO timestamps and an integer time step."""
    if "end_time" not in general_cfg or general_cfg["end_time"] is None:
        raise ValueError("[General] Missing required field 'end_time'.")

    if not general_cfg.get("start_time"):
        general_cfg["start_time"] = "2023-01-01T00:00:00"
    elif isinstance(general_cfg["start_time"], (int, float)):
        base_ts = pd.Timestamp("2023-01-01T00:00:00")
        general_cfg["start_time"] = (base_ts + pd.Timedelta(seconds=float(general_cfg["start_time"]))).isoformat()

    if "time_step" not in general_cfg or general_cfg["time_step"] is None:
        general_cfg["time_step"] = 1

    # CST's HelicsMsg.verify type-checks every value against its default with
    # exact type equality, and `period` defaults to the int 1. A float here -
    # including the old 1.0 default used when time_step was omitted - fails
    # deep inside composegen with "Diction type '<class 'float'>' not allowed
    # for period". Whole-number floats are accepted and narrowed; genuinely
    # fractional ones are unsupported, so say so here rather than there.
    time_step = general_cfg["time_step"]

    if isinstance(time_step, float) and time_step.is_integer():
        general_cfg["time_step"] = int(time_step)

    elif not isinstance(time_step, int):
        raise ValueError(
            f"[General] 'time_step' must be a whole number of seconds, got "
            f"{time_step!r}. HELICS periods are configured as integers here, "
            f"so sub-second time steps are not supported."
        )

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
    """Derive every HELICS topic from the parent/child pairs in the tree.

    Topics are named after the child's node id, which is why a federate discovers
    its keys from the CST config at startup instead of building them by name.
    """
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
    """Validate a tree config, normalise its `general:` block, and wire its topics."""
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
    """Dispatch to the tree or legacy transform for an extracted config."""
    if extracted.mode == "legacy":
        return _transform_legacy_config(extracted)

    if extracted.mode == "tree":
        return _transform_tree_config(extracted)

    raise ValueError(f"Unsupported config mode: {extracted.mode}")
