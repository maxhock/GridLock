# composegen/extract.py
from __future__ import annotations

import yaml
import pandas as pd

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from omegaconf import DictConfig, OmegaConf
from treelib import Tree


ConfigMode = Literal["legacy", "tree"]


@dataclass
class ExtractedConfig:
    mode: ConfigMode
    raw_config: Any
    config_path: Path
    data_input_path: Path
    output_path: Path
    tree: Tree | None = None
    grid_nodes: dict[str, list[int] | None] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Legacy composegen extraction
# ---------------------------------------------------------------------------

def get_num_nodes(grid_file_path: Path) -> int:
    if not grid_file_path.exists():
        raise FileNotFoundError(f"Grid file not found: {grid_file_path}")

    df = pd.read_excel(grid_file_path, sheet_name="load", header=0)
    n = int(len(df))

    if n <= 0:
        raise ValueError(f"Invalid num_nodes={n} from {grid_file_path}")

    return n


def _extract_legacy_config(
    config_path: Path,
    data_input_path: Path,
    output_path: Path,
) -> ExtractedConfig:
    conf: DictConfig = OmegaConf.load(config_path)

    if "federates" not in conf:
        raise ValueError("Legacy config must contain top-level key 'federates'.")

    if "grid" not in conf.federates:
        raise ValueError("Legacy config must contain 'federates.grid'.")


    grid_file = conf.federates.grid.grid_file
    num_nodes = get_num_nodes(data_input_path / grid_file)

    print(f"Grid has {num_nodes} nodes from {grid_file}")

    return ExtractedConfig(
        mode="legacy",
        raw_config=conf,
        config_path=config_path,
        data_input_path=data_input_path,
        output_path=output_path,
        metadata={
            "num_nodes": num_nodes,
            "grid_file": grid_file,
        },
    )


# ---------------------------------------------------------------------------
# Tree/CST extraction
# ---------------------------------------------------------------------------

def validate_location_queries(location: list, grid_id: str) -> None:
    if not isinstance(location, list):
        raise ValueError(
            f"Location for grid '{grid_id}' must be a list of query dicts, "
            f"got {type(location).__name__}."
        )

    for i, entry in enumerate(location):
        if not isinstance(entry, dict):
            raise ValueError(
                f"Location entry {i} for grid '{grid_id}' must be a dict, "
                f"got {type(entry).__name__}."
            )

        if "plz" not in entry:
            raise ValueError(
                f"Location entry {i} for grid '{grid_id}' is missing required key 'plz'."
            )

        has_kcid = "kcid" in entry
        has_bcid = "bcid" in entry

        if has_kcid != has_bcid:
            raise ValueError(
                f"Location entry {i} for grid '{grid_id}': "
                f"'kcid' and 'bcid' must both be specified or both omitted."
            )


def add_to_tree(tree: Tree, node_dict: dict, parent: str | None = None) -> None:
    node_id = f"{parent}.{node_dict.get('id')}" if parent else node_dict.get("id")

    if not node_id:
        raise ValueError("Each federation node requires an 'id'.")

    node_class = node_dict.get("class")

    node_tag = node_dict.get("name", node_id)

    node_config = node_dict.get("config", {}).copy()
    node_config["class"] = node_class
    node_config["type"] = node_config.get("type", "value")

    node_config = {
        key: (value if value != "" else None)
        for key, value in node_config.items()
    }

    tree.create_node(
        tag=node_tag,
        identifier=node_id,
        parent=parent,
        data=node_config,
    )

    for sub in node_dict.get("sub_federates", []):
        add_to_tree(tree, sub, parent=node_id)


def _read_layout_buses(layout_path: Path) -> list[int]:
    if not layout_path.exists():
        raise FileNotFoundError(f"Grid layout file not found: {layout_path}")

    df = pd.read_excel(layout_path, sheet_name="load")

    if "bus" in df.columns:
        return [int(x) for x in df["bus"].tolist()]

    return list(range(len(df)))


def _extract_tree_config(
    config_path: Path,
    data_input_path: Path,
    output_path: Path,
) -> ExtractedConfig:
    print(f"Loading tree-based federation config from {config_path}")

    with open(config_path, "r") as f:
        cfg = yaml.safe_load(f)

    if "federation" not in cfg:
        raise ValueError("Tree config must contain top-level key 'federation'.")

    tree = Tree()
    add_to_tree(tree, cfg["federation"])

    grid_ids = [
        node_id
        for node_id in tree.expand_tree(
            filter=lambda node: node.data.get("class") == "grid"
        )
    ]

    grid_nodes: dict[str, list[int] | None] = {}

    for grid_id in grid_ids:
        grid_node = tree.get_node(grid_id)
        layout = grid_node.data.get("layout")
        location = grid_node.data.get("location")

        if layout and location:
            raise ValueError(
                f"Grid '{grid_id}' defines both 'layout' and 'location'. "
                f"Please define only one."
            )

        if layout:
            layout_path = data_input_path / layout
            buses = _read_layout_buses(layout_path)
            grid_nodes[grid_id] = buses
            print(f"Loaded {len(buses)} buses for grid '{grid_id}' from {layout_path}")

        elif location:
            validate_location_queries(location, grid_id)
            grid_nodes[grid_id] = None
            print(f"Grid '{grid_id}' uses InfDB location queries: {location}")

        else:
            raise ValueError(
                f"Grid '{grid_id}' must define either 'layout' or 'location'."
            )

    return ExtractedConfig(
        mode="tree",
        raw_config=cfg,
        config_path=config_path,
        data_input_path=data_input_path,
        output_path=output_path,
        tree=tree,
        grid_nodes=grid_nodes,
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def extract(
    config_path: Path,
    data_input_path: Path,
    output_path: Path,
) -> ExtractedConfig:
    with open(config_path, "r") as f:
        raw = yaml.safe_load(f)

    if "federates" in raw:
        return _extract_legacy_config(
            config_path=config_path,
            data_input_path=data_input_path,
            output_path=output_path,
        )

    if "federation" in raw:
        return _extract_tree_config(
            config_path=config_path,
            data_input_path=data_input_path,
            output_path=output_path,
        )

    raise ValueError(
        "Unknown config format. Expected top-level key 'federates' "
        "for legacy composegen mode or 'federation' for tree/CST mode."
    )
