"""Terminal UI for exploring a YAML file as a node graph.

Run with:
    python tools/yaml_graph_tui.py config/experiment-MV-LV.yml
"""

from __future__ import annotations

from dataclasses import dataclass, field
import argparse
import curses
import locale
from pathlib import Path
import sys
from typing import Any

import yaml


locale.setlocale(locale.LC_ALL, "")

BOX_PADDING = 0
MIN_BOX_WIDTH = 8
HORIZONTAL_GAP = 2
VERTICAL_GAP = 3
DEFAULT_CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"
TOP_MARGIN = 1


@dataclass
class GraphNode:
    """A YAML value represented as a graph node."""

    node_id: str
    key: str
    label: str
    kind: str
    value_preview: str
    depth: int
    path: str
    inline_fields: list[str] = field(default_factory=list)
    children: list["GraphNode"] = field(default_factory=list)
    expanded: bool = False


@dataclass(frozen=True)
class NodeBox:
    """Screen-space box for one graph node."""

    node_id: str
    x: int
    y: int
    width: int
    height: int
    title: str
    lines: list[str]
    depth: int
    frame_type: str


@dataclass(frozen=True)
class Edge:
    """Connection between two laid-out nodes."""

    parent_id: str
    child_id: str
    start_x: int
    start_y: int
    end_x: int
    end_y: int
    parallel_index: int = 0
    parallel_count: int = 1
    thick: bool = False


@dataclass(frozen=True)
class GraphLayout:
    """Laid-out graph primitives."""

    boxes: dict[str, NodeBox]
    edges: list[Edge]
    node_order: list[str]
    canvas_width: int
    canvas_height: int


def format_scalar(value: Any, max_length: int = 28) -> str:
    """Return a compact scalar preview for terminal display."""
    if value is None:
        text = "null"
    elif isinstance(value, bool):
        text = "true" if value else "false"
    else:
        text = str(value)

    if len(text) <= max_length:
        return text
    return f"{text[: max_length - 3]}..."


def classify_node(value: Any) -> str:
    """Classify a YAML value for display."""
    if isinstance(value, dict):
        return "mapping"
    if isinstance(value, list):
        return "sequence"
    return "scalar"


def build_graph(
    value: Any, key: str = "root", depth: int = 0, path: str = "root"
) -> GraphNode:
    """Convert nested YAML data into a tree of graph nodes."""
    kind = classify_node(value)
    if kind == "mapping":
        label = f"{key} {{}}"
        preview = f"{len(value)} key(s)"
    elif kind == "sequence":
        label = f"{key} []"
        preview = f"{len(value)} item(s)"
    else:
        label = key
        preview = format_scalar(value)

    node = GraphNode(
        node_id=path,
        key=key,
        label=label,
        kind=kind,
        value_preview=preview,
        depth=depth,
        path=path,
    )

    if isinstance(value, dict):
        for child_key, child_value in value.items():
            if depth == 0 and child_key == "federation" and isinstance(child_value, dict):
                child_path = f"{path}.{child_key}"
                node.children.append(
                    build_graph(
                        child_value,
                        key=str(child_key),
                        depth=depth + 1,
                        path=child_path,
                    )
                )
                continue
            if child_key == "sub_federates" and isinstance(child_value, list):
                for index, child_value_item in enumerate(child_value):
                    child_key_name = f"[{index}]"
                    child_path = f"{path}.sub_federates{child_key_name}"
                    node.children.append(
                        build_graph(
                            child_value_item,
                            key=child_key_name,
                            depth=depth + 1,
                            path=child_path,
                        )
                    )
                continue
            inline_value(node, str(child_key), child_value)
    elif isinstance(value, list):
        for index, child_value in enumerate(value):
            inline_value(node, f"[{index}]", child_value)

    return node


def inline_value(node: GraphNode, key: str, value: Any, prefix: str | None = None) -> None:
    """Fold arbitrary nested values into one node as dotted inline fields."""
    field_name = f"{prefix}.{key}" if prefix else key
    kind = classify_node(value)

    if kind == "scalar":
        node.inline_fields.append(
            f"{field_name}: {format_scalar(value, max_length=20)}"
        )
        return

    if kind == "sequence":
        if all(classify_node(item) == "scalar" for item in value):
            preview = ", ".join(format_scalar(item, max_length=10) for item in value)
            node.inline_fields.append(f"{field_name}: [{preview}]")
            return
        for index, item in enumerate(value):
            inline_value(node, f"[{index}]", item, prefix=field_name)
        return

    for child_key, child_value in value.items():
        inline_value(node, str(child_key), child_value, prefix=field_name)


def simplify_root(root: GraphNode) -> GraphNode:
    """Return the top federate node and promote ids into titles."""
    federation_nodes = [child for child in root.children if child.key == "federation"]
    if federation_nodes:
        apply_node_titles(federation_nodes[0])
        return federation_nodes[0]

    apply_node_titles(root)
    return root


def apply_node_titles(node: GraphNode) -> None:
    """Use the inline name field as the block title when available."""
    name_field = next(
        (field for field in node.inline_fields if field.startswith("name: ")),
        None,
    )
    id_field = next((field for field in node.inline_fields if field.startswith("id: ")), None)
    if name_field is not None:
        node.label = name_field.split(": ", 1)[1]
    elif id_field is not None:
        node.label = id_field.split(": ", 1)[1]

    for child in node.children:
        apply_node_titles(child)


def semantic_node_type(node: GraphNode) -> str:
    """Return a user-facing node type derived from inline fields."""
    class_field = next(
        (field for field in node.inline_fields if field.startswith("class: ")),
        None,
    )
    if class_field is not None:
        return class_field.split(": ", 1)[1]

    if node.key == "federation":
        return "federation"
    if node.key.startswith("["):
        return "federate"
    return node.kind


def preferred_config_field(node: GraphNode) -> str | None:
    """Return the most informative single config line for a node."""
    node_type = semantic_node_type(node)
    preferred_prefixes = ["config.layout"] if node_type == "grid" else ["config.placement"]

    for prefix in preferred_prefixes:
        field = next((item for item in node.inline_fields if item.startswith(f"{prefix}: ")), None)
        if field is not None:
            return field

    return next((item for item in node.inline_fields if item.startswith("config.")), None)


def config_fields(node: GraphNode) -> list[str]:
    """Return all config fields for a node."""
    return [field for field in node.inline_fields if field.startswith("config.")]


def display_config_field(field: str) -> str:
    """Hide the leading config prefix for node display."""
    return field.removeprefix("config.")


def placement_field(node: GraphNode) -> str | None:
    """Return the placement field for a node if present."""
    return next(
        (field for field in node.inline_fields if field.startswith("config.placement: ")),
        None,
    )


def edge_style_for_child(node: GraphNode) -> tuple[int, bool]:
    """Derive edge multiplicity and thickness from the child placement."""
    field = placement_field(node)
    if field is None:
        return 1, False

    placement = field.split(": ", 1)[1].strip()
    if placement == "fill":
        return 1, True
    if placement.startswith("[") and placement.endswith("]") and "," in placement:
        return 2, False
    return 1, False


def flatten_visible_nodes(root: GraphNode) -> list[GraphNode]:
    """Return all topology nodes in display order."""
    visible: list[GraphNode] = []

    def visit(node: GraphNode) -> None:
        """Collect a node, then everything below it."""
        visible.append(node)
        if node.children:
            for child in node.children:
                visit(child)

    visit(root)
    return visible


def node_summary(node: GraphNode) -> str:
    """Return a compact detail line for the footer pane."""
    return (
        f"path={node.path} kind={node.kind} "
        f"children={len(node.children)} fields={len(node.inline_fields)}"
    )


def render_plain_tree(root: GraphNode) -> str:
    """Render the YAML graph as a plain-text tree."""
    lines: list[str] = []

    def visit(node: GraphNode, prefix_parts: list[bool]) -> None:
        """Emit one line per node, drawing the branch characters from its ancestry."""
        connector = ""
        if prefix_parts:
            leading = "".join(
                "│  " if has_more else "   " for has_more in prefix_parts[:-1]
            )
            connector = leading + ("├─ " if prefix_parts[-1] else "└─ ")
        branch_icon = "v" if node.children and node.expanded else ">" if node.children else "*"
        line = f"{connector}{branch_icon} {node.label}"
        line += f" [{node.value_preview}]"
        lines.append(line)
        for field in node.inline_fields:
            lines.append(f"{connector}   · {field}")
        if node.children and node.expanded:
            last_index = len(node.children) - 1
            for index, child in enumerate(node.children):
                visit(child, [*prefix_parts, index != last_index])

    visit(root, [])
    return "\n".join(lines)


def load_yaml_graph(path: Path) -> GraphNode:
    """Load a YAML file and convert it into a graph root node."""
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    return simplify_root(build_graph(data, key=path.name, depth=0, path=path.name))


def discover_yaml_files(path: Path) -> list[Path]:
    """Return YAML files for a file or directory input."""
    if path.is_file():
        return [path]
    if path.is_dir():
        return sorted(list(path.glob("*.yml")) + list(path.glob("*.yaml")))
    return []


def choose_yaml_file(files: list[Path]) -> Path:
    """Prompt the user to choose one YAML file from a directory listing."""
    if not files:
        raise FileNotFoundError("No YAML files found.")
    if len(files) == 1:
        return files[0]

    print("Available YAML files:")
    for index, file_path in enumerate(files, start=1):
        print(f"  {index}. {file_path}")

    while True:
        selection = input(f"Select a file [1-{len(files)}]: ").strip()
        if not selection:
            return files[0]
        if selection.isdigit():
            selected_index = int(selection)
            if 1 <= selected_index <= len(files):
                return files[selected_index - 1]
        print("Please enter a valid number.")


def move_selection(current_index: int, delta: int, visible_count: int) -> int:
    """Move the selection while staying inside the visible node range."""
    if visible_count <= 0:
        return 0
    return max(0, min(current_index + delta, visible_count - 1))


def toggle_selected_node(visible: list[GraphNode], selected_index: int) -> None:
    """Expand or collapse config details on the selected node."""
    if not visible:
        return
    node = visible[selected_index]
    if config_fields(node):
        node.expanded = not node.expanded


def measure_node_box(node: GraphNode) -> tuple[int, int, str, str]:
    """Return the box dimensions and text lines for one node."""
    title = node.label
    if node.expanded:
        visible_lines = [
            format_scalar(display_config_field(field), max_length=20)
            for field in config_fields(node)[:4]
        ]
        if len(config_fields(node)) > 4:
            visible_lines[-1] = f"... +{len(config_fields(node)) - 3} more"
    else:
        visible_lines = []
    content_width = max(
        len(title),
        *(len(line) for line in visible_lines) if visible_lines else [0],
        MIN_BOX_WIDTH - 2,
    )
    width = content_width + BOX_PADDING * 2 + 2
    height = 3 if not visible_lines else len(visible_lines) + 3
    return width, height, title, "\n".join(visible_lines)


def frame_characters(frame_type: str) -> tuple[str, str, str]:
    """Return border characters for a semantic node type."""
    if frame_type == "grid":
        return ("#", "=", "#")
    if frame_type in {"house", "load"}:
        return ("/", "-", "/")
    if frame_type in {"battery", "pv", "hems"}:
        return ("*", "-", "*")
    return ("+", "-", "|")


def legend_lines() -> list[str]:
    """Return compact legend text for frames and connector styles."""
    return [
        "Frames",
        "#=# grid",
        "/-/ house/load",
        "*-* battery/pv/hems",
        "+-| other",
        "Lines",
        "- single",
        "= double placement",
        "# thick fill",
    ]


def draw_legend(
    stdscr: Any,
    origin_x: int,
    origin_y: int,
    width: int,
    height: int,
) -> None:
    """Draw a compact legend box."""
    lines = legend_lines()
    if width < 12 or height < len(lines) + 2:
        return

    safe_addnstr(stdscr, origin_y, origin_x, "+" + "-" * (width - 2) + "+", width)
    for row in range(1, height - 1):
        safe_addnstr(stdscr, origin_y + row, origin_x, "|" + " " * (width - 2) + "|", width)
    safe_addnstr(stdscr, origin_y + height - 1, origin_x, "+" + "-" * (width - 2) + "+", width)

    for index, line in enumerate(lines, start=1):
        if index >= height - 1:
            break
        attr = curses.A_BOLD if line in {"Frames", "Lines"} else curses.A_DIM
        safe_addnstr(stdscr, origin_y + index, origin_x + 1, line, width - 2, attr)


def subtree_span(node: GraphNode) -> int:
    """Return the horizontal span required by this node subtree."""
    width, _, _, _ = measure_node_box(node)
    if not node.children:
        return width
    child_spans = [subtree_span(child) for child in node.children]
    children_width = sum(child_spans) + HORIZONTAL_GAP * (len(child_spans) - 1)
    return max(width, children_width)


def layout_graph(root: GraphNode) -> GraphLayout:
    """Compute a compact top-down node graph layout."""
    boxes: dict[str, NodeBox] = {}
    edges: list[Edge] = []
    node_order: list[str] = []
    next_x_by_depth: dict[int, int] = {}
    child_ids_by_node: dict[str, list[str]] = {}

    def shift_subtree(node: GraphNode, delta_x: int) -> None:
        """Move a node and its subtree sideways, keeping them together."""
        if delta_x == 0:
            return
        box = boxes[node.node_id]
        boxes[node.node_id] = NodeBox(
            node_id=box.node_id,
            x=box.x + delta_x,
            y=box.y,
            width=box.width,
            height=box.height,
            title=box.title,
            lines=box.lines,
            depth=box.depth,
            frame_type=box.frame_type,
        )
        for child in child_ids_by_node.get(node.node_id, []):
            shift_subtree_by_id(child, delta_x)

    def shift_subtree_by_id(node_id: str, delta_x: int) -> None:
        """Same shift, reached by id - children are only known by id during layout."""
        if delta_x == 0:
            return
        box = boxes[node_id]
        boxes[node_id] = NodeBox(
            node_id=box.node_id,
            x=box.x + delta_x,
            y=box.y,
            width=box.width,
            height=box.height,
            title=box.title,
            lines=box.lines,
            depth=box.depth,
            frame_type=box.frame_type,
        )
        for child_id in child_ids_by_node.get(node_id, []):
            shift_subtree_by_id(child_id, delta_x)

    def place(node: GraphNode, left_x: int, top_y: int) -> int:
        """Lay a node out above its children and return the x it is centred on."""
        width, height, title, detail = measure_node_box(node)
        node_y = top_y
        lines = detail.splitlines() if detail else []
        child_ids_by_node[node.node_id] = [child.node_id for child in node.children]

        if node.children:
            child_y = top_y + height + VERTICAL_GAP
            child_centers: list[int] = []
            for child in node.children:
                child_centers.append(place(child, 0, child_y))
            desired_x = int(round((child_centers[0] + child_centers[-1]) / 2 - width / 2))
        else:
            desired_x = next_x_by_depth.get(node.depth, 0)

        node_x = max(desired_x, next_x_by_depth.get(node.depth, 0))
        boxes[node.node_id] = NodeBox(
            node_id=node.node_id,
            x=node_x,
            y=node_y,
            width=width,
            height=height,
            title=title,
            lines=lines,
            depth=node.depth,
            frame_type=semantic_node_type(node),
        )
        node_order.append(node.node_id)
        next_x_by_depth[node.depth] = node_x + width + HORIZONTAL_GAP

        if node.children:
            if node_x > desired_x:
                shift = node_x - desired_x
                for child in node.children:
                    shift_subtree_by_id(child.node_id, shift)

            parent_center_x = node_x + width // 2
            parent_bottom_y = node_y + height - 1
            for child in node.children:
                child_box = boxes[child.node_id]
                child_center_x = child_box.x + child_box.width // 2
                parallel_count, thick = edge_style_for_child(child)
                for parallel_index in range(parallel_count):
                    edges.append(
                        Edge(
                            parent_id=node.node_id,
                            child_id=child.node_id,
                            start_x=parent_center_x,
                            start_y=parent_bottom_y,
                            end_x=child_center_x,
                            end_y=child_box.y,
                            parallel_index=parallel_index,
                            parallel_count=parallel_count,
                            thick=thick,
                        )
                    )

        return node_x + width // 2

    place(root, 0, TOP_MARGIN)
    canvas_width = max((box.x + box.width for box in boxes.values()), default=0) + 2
    canvas_height = max((box.y + box.height for box in boxes.values()), default=0) + 2
    return GraphLayout(
        boxes=boxes,
        edges=edges,
        node_order=node_order,
        canvas_width=canvas_width,
        canvas_height=canvas_height,
    )


def viewport_for_selection(
    layout: GraphLayout,
    selected_id: str,
    current_x: int,
    current_y: int,
    width: int,
    height: int,
) -> tuple[int, int]:
    """Shift the viewport just enough to keep the selected node visible."""
    box = layout.boxes[selected_id]
    next_x = current_x
    next_y = current_y

    if box.x < next_x:
        next_x = box.x
    elif box.x + box.width > next_x + width:
        next_x = box.x + box.width - width

    if box.y < next_y:
        next_y = box.y
    elif box.y + box.height > next_y + height:
        next_y = box.y + box.height - height

    return max(0, next_x), max(0, next_y)


def safe_addnstr(
    stdscr: Any, y: int, x: int, text: str, width: int, attr: int = 0
) -> None:
    """Write a line without crashing on narrow terminal edge cases."""
    if width <= 0 or y < 0 or x < 0:
        return
    try:
        stdscr.addnstr(y, x, text.ljust(width), width, attr)
    except curses.error:
        return


def safe_addch(stdscr: Any, y: int, x: int, char: str, attr: int = 0) -> None:
    """Draw one character if it fits in the viewport."""
    if y < 0 or x < 0:
        return
    try:
        stdscr.addch(y, x, char, attr)
    except curses.error:
        return


def draw_box(
    stdscr: Any,
    box: NodeBox,
    viewport_x: int,
    viewport_y: int,
    origin_y: int,
    viewport_width: int,
    viewport_height: int,
    selected: bool,
) -> None:
    """Draw a node box inside the viewport."""
    screen_x = box.x - viewport_x
    screen_y = box.y - viewport_y
    attr = curses.A_BOLD | (curses.A_STANDOUT if selected else 0)

    if screen_y + box.height <= 0 or screen_x + box.width <= 0:
        return
    if screen_y >= viewport_height or screen_x >= viewport_width:
        return

    corner, horizontal, vertical = frame_characters(box.frame_type)
    top = corner + horizontal * (box.width - 2) + corner
    bottom = corner + horizontal * (box.width - 2) + corner

    safe_addnstr(stdscr, origin_y + screen_y, screen_x, top, box.width, attr)
    for offset in range(1, box.height - 1):
        safe_addnstr(
            stdscr,
            origin_y + screen_y + offset,
            screen_x,
            vertical + " " * (box.width - 2) + vertical,
            box.width,
            attr,
        )
    safe_addnstr(stdscr, origin_y + screen_y + box.height - 1, screen_x, bottom, box.width, attr)

    safe_addnstr(
        stdscr,
        origin_y + screen_y + 1,
        screen_x + 1,
        box.title[: box.width - 2].center(box.width - 2),
        box.width - 2,
        attr,
    )
    for index, line in enumerate(box.lines, start=2):
        if index >= box.height - 1:
            break
        safe_addnstr(
            stdscr,
            origin_y + screen_y + index,
            screen_x + 1,
            line[: box.width - 2].ljust(box.width - 2),
            box.width - 2,
            attr,
        )


def draw_edge(
    stdscr: Any,
    edge: Edge,
    viewport_x: int,
    viewport_y: int,
    origin_y: int,
    viewport_width: int,
    viewport_height: int,
) -> None:
    """Draw a connector between two node boxes."""
    offset = 0
    if edge.parallel_count > 1:
        offset = -1 if edge.parallel_index == 0 else 1

    sx = edge.start_x - viewport_x + offset
    sy = edge.start_y - viewport_y
    ex = edge.end_x - viewport_x + offset
    ey = edge.end_y - viewport_y
    mid_y = sy + 2
    vertical_char = "#" if edge.thick else "|"
    horizontal_char = "#" if edge.thick else "-"
    terminal_char = "#" if edge.thick else "v"

    if sy >= 0 and sy < viewport_height and sx >= 0 and sx < viewport_width:
        safe_addch(stdscr, origin_y + sy, sx, "#" if edge.thick else "+")

    vertical_start = sy + 1
    vertical_end = mid_y
    for y in range(min(vertical_start, vertical_end), max(vertical_start, vertical_end) + 1):
        if 0 <= y < viewport_height and 0 <= sx < viewport_width:
            safe_addch(stdscr, origin_y + y, sx, vertical_char)

    for x in range(min(sx, ex), max(sx, ex) + 1):
        if 0 <= mid_y < viewport_height and 0 <= x < viewport_width:
            safe_addch(stdscr, origin_y + mid_y, x, horizontal_char)

    for y in range(min(mid_y, ey - 1), max(mid_y, ey - 1) + 1):
        if 0 <= y < viewport_height and 0 <= ex < viewport_width:
            safe_addch(stdscr, origin_y + y, ex, vertical_char)

    if 0 <= ey < viewport_height and 0 <= ex < viewport_width:
        safe_addch(stdscr, origin_y + ey, ex, terminal_char)


def draw_screen(stdscr: Any, root: GraphNode, source_path: Path) -> None:
    """Run the curses event loop."""
    try:
        curses.curs_set(0)
    except curses.error:
        pass
    stdscr.keypad(True)

    selected_index = 0
    viewport_x = 0
    viewport_y = 0

    while True:
        stdscr.erase()
        height, width = stdscr.getmaxyx()
        if height < 10 or width < 40:
            safe_addnstr(
                stdscr,
                0,
                0,
                "Terminal too small for YAML graph UI. Resize or press q to quit.",
                width,
                curses.A_REVERSE,
            )
            stdscr.refresh()
            key = stdscr.getch()
            if key in (ord("q"), 27):
                return
            continue

        header_y = 0
        footer_y = height - 2
        detail_y = height - 1
        canvas_top = 1
        canvas_height = height - 3
        canvas_width = width

        visible = flatten_visible_nodes(root)
        selected_index = max(0, min(selected_index, len(visible) - 1))
        selected_node = visible[selected_index]
        layout = layout_graph(root)
        selected_box = layout.boxes[selected_node.node_id]

        viewport_x, viewport_y = viewport_for_selection(
            layout,
            selected_node.node_id,
            viewport_x,
            viewport_y,
            canvas_width,
            canvas_height,
        )

        title = f" YAML Graph Explorer: {source_path} "
        help_text = "j/k move  h/l collapse-expand config  space toggle  w/a/s/d pan  g/G top-bottom  q quit"
        safe_addnstr(stdscr, header_y, 0, title, width, curses.A_REVERSE)
        safe_addnstr(stdscr, footer_y, 0, help_text, width, curses.A_REVERSE)
        safe_addnstr(stdscr, detail_y, 0, node_summary(selected_node), width, curses.A_DIM)

        for edge in layout.edges:
            draw_edge(
                stdscr,
                edge,
                viewport_x,
                viewport_y,
                canvas_top,
                canvas_width,
                canvas_height,
            )

        for node_id in layout.node_order:
            box = layout.boxes[node_id]
            draw_box(
                stdscr,
                box,
                viewport_x,
                viewport_y,
                canvas_top,
                canvas_width,
                canvas_height,
                selected=node_id == selected_node.node_id,
            )

        legend_width = min(20, width // 4)
        legend_height = min(11, canvas_height)
        draw_legend(
            stdscr,
            width - legend_width,
            canvas_top,
            legend_width,
            legend_height,
        )

        safe_addnstr(
            stdscr,
            canvas_top,
            0,
            f"node {selected_index + 1}/{len(visible)}  canvas {layout.canvas_width}x{layout.canvas_height}  view {viewport_x},{viewport_y}",
            min(width, width - legend_width - 1 if legend_width < width else width),
            curses.A_DIM,
        )

        stdscr.refresh()

        key = stdscr.getch()
        if key in (ord("q"), 27):
            return
        if key in (curses.KEY_UP, ord("k")):
            selected_index = move_selection(selected_index, -1, len(visible))
        elif key in (curses.KEY_DOWN, ord("j")):
            selected_index = move_selection(selected_index, 1, len(visible))
        elif key in (curses.KEY_HOME, ord("g")):
            selected_index = 0
        elif key in (curses.KEY_END, ord("G")):
            selected_index = len(visible) - 1
        elif key in (curses.KEY_RIGHT, ord("l")):
            node = visible[selected_index]
            if config_fields(node) and not node.expanded:
                node.expanded = True
        elif key in (curses.KEY_LEFT, ord("h")):
            node = visible[selected_index]
            if config_fields(node) and node.expanded:
                node.expanded = False
        elif key in (10, 13, ord(" ")):
            toggle_selected_node(visible, selected_index)
        elif key == ord("w"):
            viewport_y = max(0, viewport_y - 3)
        elif key == ord("s"):
            viewport_y = min(max(0, layout.canvas_height - canvas_height), viewport_y + 3)
        elif key == ord("a"):
            viewport_x = max(0, viewport_x - 6)
        elif key == ord("d"):
            viewport_x = min(max(0, layout.canvas_width - canvas_width), viewport_x + 6)
        elif key == ord("e"):
            for node in visible:
                if config_fields(node):
                    node.expanded = True
        elif key == ord("c"):
            node = visible[selected_index]
            node.expanded = False


def run_curses_ui(root: GraphNode, source_path: Path) -> None:
    """Start and stop curses while preserving the original runtime error."""
    stdscr = curses.initscr()
    curses.noecho()
    curses.cbreak()
    try:
        if curses.has_colors():
            curses.start_color()
        draw_screen(stdscr, root, source_path)
    finally:
        try:
            stdscr.keypad(False)
        except curses.error:
            pass
        try:
            curses.echo()
        except curses.error:
            pass
        try:
            curses.nocbreak()
        except curses.error:
            pass
        curses.endwin()


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "yaml_path",
        nargs="?",
        type=Path,
        default=DEFAULT_CONFIG_DIR,
        help="Path to a YAML file or directory to inspect (defaults to config/)",
    )
    parser.add_argument(
        "--plain",
        action="store_true",
        help="Print a plain-text tree instead of starting the curses UI",
    )
    parser.add_argument(
        "--force-curses",
        action="store_true",
        help="Try the interactive curses UI even if terminal detection looks non-interactive",
    )
    return parser.parse_args()


def main() -> int:
    """CLI entrypoint."""
    args = parse_args()
    if not args.yaml_path.exists():
        raise FileNotFoundError(f"YAML path not found: {args.yaml_path}")

    yaml_files = discover_yaml_files(args.yaml_path)
    if not yaml_files:
        raise FileNotFoundError(f"No YAML files found in: {args.yaml_path}")
    selected_yaml = choose_yaml_file(yaml_files)

    root = load_yaml_graph(selected_yaml)
    if args.plain:
        print(render_plain_tree(root))
        return 0

    looks_interactive = sys.stdin.isatty() and sys.stdout.isatty()
    try:
        if not looks_interactive and not args.force_curses:
            raise curses.error("terminal does not look interactive")
        run_curses_ui(root, selected_yaml)
    except curses.error as exc:
        print(render_plain_tree(root))
        print(
            "\nInteractive terminal UI is unavailable in this session. "
            "Open it in a real terminal, or retry with --force-curses if your terminal supports it."
        )
        print(f"Reason: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
