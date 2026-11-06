"""Expand `placement: filename` grids into one grid node per layout file.

A grid whose layout is ``<dir>/<name>.json`` keeps its sub-grids in the folder
``<dir>/<name>/``, one file per parent load: ``load_<idx>.json`` hangs on the
parent's pandapower load ``<idx>``. A child grid declared with
``placement: filename`` stands for all of them, so a federation of hundreds of
LV areas is written as one node per level instead of one node per area.

Each file becomes an ordinary grid node with that file as its ``layout`` and
``<idx>`` as its int ``placement``, named ``<id>_<idx>`` the way a placed house
or grid is named after its load. Everything after this expansion - the
placement checks, cloning, wiring - sees only plain nodes.

Both the infdb stage and composegen expand the tree and must agree on which
grids exist, so this module is the one copy of the rule. The infdb image gets
it through a Compose build context; keep it free of third-party imports.
"""

from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any

FILENAME_PLACEMENT = "filename"
_CHILD_FILE = re.compile(r"^load_(0|[1-9]\d*)\.json$")
_TRAILING_INDEX = re.compile(r"_\d+$")


def expand_filename_placements(
    federation: dict[str, Any], data_input_path: Path
) -> dict[str, Any]:
    """Return a copy of the federation with every filename-placed grid expanded.

    ``data_input_path`` is the directory layouts are relative to. The input is
    not modified, so the experiment as written stays available for provenance.
    """
    return _expand_node(federation, data_input_path)


def _expand_node(node: dict[str, Any], data_input_path: Path) -> dict[str, Any]:
    """Copy one node, expanding the filename-placed grids among its children."""
    layout = (node.get("config") or {}).get("layout")
    children = [
        expanded
        for child in node.get("sub_federates") or []
        for expanded in _expand_child(child, node, layout, data_input_path)
    ]
    expanded_node = dict(node)
    if "sub_federates" in node:
        expanded_node["sub_federates"] = children
    return expanded_node


def _expand_child(
    child: dict[str, Any],
    parent: dict[str, Any],
    parent_layout: str | None,
    data_input_path: Path,
) -> list[dict[str, Any]]:
    """One child as declared, or one grid per file if it is placed by filename."""
    config = child.get("config") or {}
    if config.get("placement") != FILENAME_PLACEMENT:
        return [_expand_node(child, data_input_path)]

    child_id = child.get("id")
    _check_filename_child(child_id, child, config, parent, parent_layout)
    folder = Path(str(parent_layout)).with_suffix("")
    base_id = _TRAILING_INDEX.sub("", str(child_id))

    grids = []
    for load_idx, file_name in _child_files(child_id, data_input_path / folder):
        grid = copy.deepcopy(child)
        grid["id"] = f"{base_id}_{load_idx}"
        grid["config"] = {
            **config,
            "layout": (folder / file_name).as_posix(),
            "placement": load_idx,
        }
        grids.append(_expand_node(grid, data_input_path))
    return grids


def _check_filename_child(
    child_id: Any,
    child: dict[str, Any],
    config: dict[str, Any],
    parent: dict[str, Any],
    parent_layout: str | None,
) -> None:
    """Refuse a filename placement that has no folder to read or would be ignored."""
    where = f"'{child_id}' (placement: {FILENAME_PLACEMENT})"
    if not child_id:
        # Every expanded grid is named after it; without one they would all
        # become "None_<idx>" instead of failing like any other id-less node.
        raise ValueError("Each federation node requires an 'id'.")
    if child.get("class") != "grid":
        raise ValueError(
            f"{where} is a '{child.get('class')}'; only a grid takes its sub-grids "
            "from a folder."
        )
    if "layout" in config or "location" in config:
        raise ValueError(
            f"{where} sets its own source; its layouts are the load_<idx>.json "
            "files in the folder named after its parent's layout, so drop it."
        )
    if parent.get("class") not in (None, "grid") or not parent_layout:
        raise ValueError(
            f"{where} needs a parent grid with a local 'layout': the folder of "
            "sub-grids is named after that file."
        )


def _child_files(child_id: Any, folder: Path) -> list[tuple[int, str]]:
    """``(load index, file name)`` for every sub-grid file, by load index.

    Any other file in the folder is refused rather than skipped: a misspelt
    ``load_12.jsn`` would otherwise leave that load quietly empty. Folders are
    left alone, since they hold the next level of sub-grids.
    """
    if not folder.is_dir():
        raise FileNotFoundError(
            f"'{child_id}' is placed by filename, but its folder {folder} does "
            "not exist or is not a folder."
        )
    files: list[tuple[int, str]] = []
    strays: list[str] = []
    for path in folder.iterdir():
        if path.is_dir():
            continue
        match = _CHILD_FILE.match(path.name)
        if match:
            files.append((int(match.group(1)), path.name))
        else:
            strays.append(path.name)
    if strays:
        raise ValueError(
            f"{folder} holds files that are not load_<idx>.json sub-grids of "
            f"'{child_id}': {sorted(strays)}."
        )
    if not files:
        raise ValueError(f"{folder} holds no load_<idx>.json for '{child_id}'.")
    return sorted(files)
