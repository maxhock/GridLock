"""Small Streamlit dashboard for running and inspecting GridLock experiments.

Run with:
    streamlit run tools/gridlock_dashboard.py
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from html import escape
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import time
from typing import Any

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import yaml

try:
    import pydeck as pdk
except ImportError:  # pragma: no cover - handled in UI
    pdk = None

try:
    import networkx as nx
except ImportError:  # pragma: no cover - handled in UI
    nx = None

try:
    import pgeocode
except ImportError:  # pragma: no cover - handled in UI
    pgeocode = None

try:
    from pymongo import MongoClient
except ImportError:  # pragma: no cover - handled in UI
    MongoClient = None

try:
    import psycopg
    from psycopg import sql
except ImportError:  # pragma: no cover - handled in UI
    psycopg = None
    sql = None


REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = REPO_ROOT / "config"
GENERATED_DIR = REPO_ROOT / "generated"
RUN_SCRIPT = REPO_ROOT / "run.sh"
RUN_LOG = GENERATED_DIR / "streamlit-run.log"
PREFLIGHT_ENV_PATH = Path(os.getenv("PREFLIGHT_ENV_FILE", CONFIG_DIR / "preflight.env"))
DEFAULT_EXPERIMENT = CONFIG_DIR / "experiment-LV.yml"
TIME_COLUMN_CANDIDATES = ("sim_time", "time", "timestamp", "t")
VALUE_COLUMN_CANDIDATES = ("value", "data_value", "val")
MAX_REASONABLE_POWER = 1e9


@dataclass(frozen=True)
class DbConfig:
    postgres_host: str
    postgres_port: int
    postgres_db: str
    postgres_user: str
    postgres_password: str
    mongo_uri: str
    mongo_db: str
    mongo_user: str | None
    mongo_password: str | None


def render_header(cfg: dict[str, Any]) -> None:
    """Render the title block."""
    location_count = len(location_entries(cfg))
    st.markdown(
        f"""
        <section class="gridlock-hero">
            <h1>GridLock Control Room</h1>
            <p>
                Launch the experiment stack, inspect resolved CST topology, and review
                transformer-side power behavior for <strong>{experiment_name(cfg)}</strong>.
            </p>
            <div class="gridlock-chip-row">
                <span class="gridlock-chip">Experiment: {experiment_name(cfg)}</span>
                <span class="gridlock-chip">Schema: {experiment_schema(cfg)}</span>
                <span class="gridlock-chip">Location Queries: {location_count}</span>
            </div>
        </section>
        """,
        unsafe_allow_html=True,
    )


def load_env_file(env_path: Path) -> dict[str, str]:
    """Parse a simple KEY=VALUE env file without mutating process env."""
    values: dict[str, str] = {}
    if not env_path.exists():
        return values

    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def load_db_config() -> DbConfig:
    """Load CST DB connection settings from the preflight env file."""
    env = load_env_file(PREFLIGHT_ENV_PATH)
    mongo_host = env.get("CST_MONGO_HOST", "mongodb://localhost")
    mongo_uri = mongo_host
    if ":" not in mongo_host.rsplit("/", 1)[-1]:
        mongo_uri = f"{mongo_host}:{env.get('CST_MONGO_PORT', '27017')}"

    return DbConfig(
        postgres_host=env.get("CST_POSTGRES_HOST", "localhost"),
        postgres_port=int(env.get("CST_POSTGRES_PORT", "5432")),
        postgres_db=env.get("CST_POSTGRES_DB", "copper"),
        postgres_user=env.get("CST_POSTGRES_USER", "worker"),
        postgres_password=env.get("CST_POSTGRES_PASSWORD", "worker"),
        mongo_uri=mongo_uri,
        mongo_db=env.get("CST_MONGO_DB", "copper"),
        mongo_user=env.get("CST_MONGO_ROOT_USERNAME"),
        mongo_password=env.get("CST_MONGO_ROOT_PASSWORD"),
    )


def available_experiment_files() -> list[Path]:
    """Return experiment files in config/, preferring the current default."""
    files = sorted(set(CONFIG_DIR.glob("experiment*.yml")) | set(CONFIG_DIR.glob("experiment*.yaml")))
    if DEFAULT_EXPERIMENT in files:
        files.remove(DEFAULT_EXPERIMENT)
        files.insert(0, DEFAULT_EXPERIMENT)
    return files


def read_experiment(path: Path) -> dict[str, Any]:
    """Read a YAML experiment config."""
    with path.open() as handle:
        return yaml.safe_load(handle) or {}


def experiment_name(cfg: dict[str, Any]) -> str:
    """Return the experiment display name."""
    general = cfg.get("general", {})
    return str(general.get("name", "GridLock"))


def experiment_schema(cfg: dict[str, Any]) -> str:
    """Return the CST analysis schema name for the experiment."""
    return f"{experiment_name(cfg)}Analysis"


def root_grid_id(cfg: dict[str, Any]) -> str:
    """Return the top-level grid identifier from the experiment."""
    federation = cfg.get("federation", {})
    return str(federation.get("id", "grid"))


def location_entries(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the location query entries from the selected experiment."""
    federation = cfg.get("federation", {})
    config = federation.get("config", {})
    location = config.get("location", [])
    return location if isinstance(location, list) else []


def expected_grid_ids(cfg: dict[str, Any]) -> list[str]:
    """Derive resolved grid ids directly from the selected experiment config."""
    grid_id = root_grid_id(cfg)
    resolved: list[str] = []
    for entry in location_entries(cfg):
        plz = entry.get("plz")
        kcid = entry.get("kcid")
        bcid = entry.get("bcid")
        if plz is None:
            continue
        if kcid is not None and bcid is not None:
            resolved.append(f"{grid_id}_{plz}_{kcid}_{bcid}")
    return resolved


def parse_selected_grid_identity(
    selected_grid: str,
    base_grid_id: str,
) -> tuple[str | None, str | None, str | None]:
    """Extract plz/kcid/bcid from a resolved grid name if possible."""
    prefix = f"{base_grid_id}_"
    if not selected_grid.startswith(prefix):
        return None, None, None

    suffix = selected_grid[len(prefix) :]
    parts = suffix.split("_")
    if len(parts) >= 3:
        return parts[0], parts[1], parts[2]
    if len(parts) == 1:
        return parts[0], None, None
    return None, None, None


def match_location_entry(
    selected_grid: str,
    base_grid_id: str,
    cfg: dict[str, Any],
) -> dict[str, Any] | None:
    """Match the selected resolved grid back to its experiment.yml location entry."""
    selected_plz, selected_kcid, selected_bcid = parse_selected_grid_identity(
        selected_grid, base_grid_id
    )
    for entry in location_entries(cfg):
        plz = entry.get("plz")
        kcid = entry.get("kcid")
        bcid = entry.get("bcid")
        if plz is None:
            continue
        if str(plz) != str(selected_plz):
            continue
        if kcid is not None and str(kcid) != str(selected_kcid):
            continue
        if bcid is not None and str(bcid) != str(selected_bcid):
            continue
        return entry
    return None


def resolve_marker(entry: dict[str, Any] | None) -> pd.DataFrame:
    """Build a one-row DataFrame compatible with st.map."""
    if not entry:
        return pd.DataFrame()

    latitude = entry.get("lat") or entry.get("latitude")
    longitude = entry.get("lon") or entry.get("lng") or entry.get("longitude")

    location_value = entry.get("location")
    if (latitude is None or longitude is None) and isinstance(location_value, list):
        if len(location_value) >= 2:
            latitude = location_value[0] if latitude is None else latitude
            longitude = location_value[1] if longitude is None else longitude

    if latitude is not None and longitude is not None:
        return pd.DataFrame(
            [
                {
                    "lat": float(latitude),
                    "lon": float(longitude),
                    "label": f"PLZ {entry.get('plz', 'n/a')}",
                }
            ]
        )

    plz = entry.get("plz")
    if plz is None or pgeocode is None:
        return pd.DataFrame()

    nomi = pgeocode.Nominatim("de")
    result = nomi.query_postal_code(str(plz))
    if result is None or pd.isna(result.latitude) or pd.isna(result.longitude):
        return pd.DataFrame()

    return pd.DataFrame(
        [
            {
                "lat": float(result.latitude),
                "lon": float(result.longitude),
                "label": f"PLZ {plz}",
            }
        ]
    )


def mongo_client(db_cfg: DbConfig) -> Any | None:
    """Create a Mongo client if the dependency is available."""
    if MongoClient is None:
        return None
    if db_cfg.mongo_user and db_cfg.mongo_password:
        uri = db_cfg.mongo_uri.replace(
            "mongodb://",
            f"mongodb://{db_cfg.mongo_user}:{db_cfg.mongo_password}@",
            1,
        )
    else:
        uri = db_cfg.mongo_uri
    return MongoClient(uri, serverSelectionTimeoutMS=3000)


@st.cache_data(ttl=5)
def fetch_grid_metadata(db_cfg: DbConfig) -> pd.DataFrame:
    """Read grid metadata rows from CST Mongo custom_metadata."""
    client = mongo_client(db_cfg)
    if client is None:
        return pd.DataFrame()

    try:
        docs = list(
            client[db_cfg.mongo_db]["custom_metadata"].find(
                {"net_json": {"$exists": True}},
                {"grid_id": 1, "bus_count": 1, "load_count": 1},
            )
        )
    except Exception:
        return pd.DataFrame()
    finally:
        client.close()

    rows: list[dict[str, Any]] = []
    for doc in docs:
        rows.append(
            {
                "resolved_grid_id": str(doc.get("cst_007", doc.get("_id", ""))),
                "grid_id": doc.get("grid_id"),
                "bus_count": doc.get("bus_count"),
                "load_count": doc.get("load_count"),
            }
        )
    return pd.DataFrame(rows)


def postgres_connect(db_cfg: DbConfig):
    """Open a Postgres connection if psycopg is available."""
    if psycopg is None:
        return None
    return psycopg.connect(
        host=db_cfg.postgres_host,
        port=db_cfg.postgres_port,
        dbname=db_cfg.postgres_db,
        user=db_cfg.postgres_user,
        password=db_cfg.postgres_password,
    )


@st.cache_data(ttl=5)
def fetch_topology_data(
    db_cfg: DbConfig,
    experiment: str,
    selected_grid: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load graph nodes and edges for one grid from Postgres."""
    connection = postgres_connect(db_cfg)
    if connection is None:
        return pd.DataFrame(), pd.DataFrame()

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, title, subtitle, color, highlighted, node_radius,
                       detail__kind, detail__bus_index, detail__degree
                FROM grid_topology_nodes
                WHERE experiment_name = %s AND grid_id = %s
                ORDER BY id
                """,
                (experiment, selected_grid),
            )
            nodes = pd.DataFrame(
                cursor.fetchall(),
                columns=[
                    "id",
                    "title",
                    "subtitle",
                    "color",
                    "highlighted",
                    "node_radius",
                    "kind",
                    "bus_index",
                    "degree",
                ],
            )
            cursor.execute(
                """
                SELECT id, source, target, color, thickness, detail__element_type
                FROM grid_topology_edges
                WHERE experiment_name = %s AND grid_id = %s
                ORDER BY id
                """,
                (experiment, selected_grid),
            )
            edges = pd.DataFrame(
                cursor.fetchall(),
                columns=["id", "source", "target", "color", "thickness", "element_type"],
            )
    except Exception:
        return pd.DataFrame(), pd.DataFrame()
    finally:
        connection.close()

    return nodes, edges


@st.cache_data(ttl=5)
def fetch_topology_grid_ids(db_cfg: DbConfig, experiment: str) -> list[str]:
    """List grid ids available in the topology export for one experiment."""
    connection = postgres_connect(db_cfg)
    if connection is None:
        return []

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT DISTINCT grid_id
                FROM grid_topology_nodes
                WHERE experiment_name = %s
                ORDER BY grid_id
                """,
                (experiment,),
            )
            rows = cursor.fetchall()
    except Exception:
        return []
    finally:
        connection.close()

    return [row[0] for row in rows]


def existing_hdt_tables(connection: Any, schema_name: str) -> list[str]:
    """Return available CST timeseries tables in the experiment schema."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = %s AND table_name LIKE 'hdt_%'
            ORDER BY table_name
            """,
            (schema_name,),
        )
        return [row[0] for row in cursor.fetchall()]


def table_columns(connection: Any, schema_name: str, table_name: str) -> list[str]:
    """Return ordered column names for a table."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = %s AND table_name = %s
            ORDER BY ordinal_position
            """,
            (schema_name, table_name),
        )
        return [row[0] for row in cursor.fetchall()]


def choose_column(columns: list[str], candidates: tuple[str, ...]) -> str | None:
    """Pick the first matching column from a candidate list."""
    lowered = {column.lower(): column for column in columns}
    for candidate in candidates:
        if candidate in lowered:
            return lowered[candidate]
    return None


@st.cache_data(ttl=5)
def fetch_timeseries_catalog(db_cfg: DbConfig, schema_name: str) -> pd.DataFrame:
    """Inspect the logged CST tables and list available data streams."""
    connection = postgres_connect(db_cfg)
    if connection is None or sql is None:
        return pd.DataFrame()

    try:
        tables = existing_hdt_tables(connection, schema_name)
        rows: list[dict[str, Any]] = []

        for table_name in tables:
            columns = table_columns(connection, schema_name, table_name)
            if "data_name" not in columns or "federate" not in columns:
                continue
            query = sql.SQL(
                """
                SELECT federate, data_name, COUNT(*) AS sample_count
                FROM {}.{}
                GROUP BY federate, data_name
                ORDER BY federate, data_name
                """
            ).format(sql.Identifier(schema_name), sql.Identifier(table_name))
            with connection.cursor() as cursor:
                cursor.execute(query)
                for federate, data_name, sample_count in cursor.fetchall():
                    rows.append(
                        {
                            "table_name": table_name,
                            "federate": federate,
                            "data_name": data_name,
                            "sample_count": sample_count,
                        }
                    )
    except Exception:
        return pd.DataFrame()
    finally:
        connection.close()

    return pd.DataFrame(rows)


@st.cache_data(ttl=5)
def fetch_transformer_proxy_series(
    db_cfg: DbConfig,
    schema_name: str,
    selected_grid: str,
) -> pd.DataFrame:
    """Aggregate load-player power as a transformer-interface proxy series.

    The current CST logger output can contain repeated samples and invalid
    sentinel values such as ``-1e49``. This query keeps only plausible power
    magnitudes and collapses duplicate ``(sim_time, data_name)`` rows before
    summing the selected grid's load channels.
    """
    connection = postgres_connect(db_cfg)
    if connection is None or sql is None:
        return pd.DataFrame()

    try:
        pattern_prefixes = [
            selected_grid,
            selected_grid.replace(".", "/"),
            selected_grid.split(".")[0],
            selected_grid.split(".")[0].replace(".", "/"),
        ]
        seen_prefixes: set[str] = set()
        unique_prefixes = []
        for prefix in pattern_prefixes:
            if prefix not in seen_prefixes:
                seen_prefixes.add(prefix)
                unique_prefixes.append(prefix)

        tables = existing_hdt_tables(connection, schema_name)
        for table_name in tables:
            columns = table_columns(connection, schema_name, table_name)
            if "data_name" not in columns or "federate" not in columns:
                continue

            time_col = choose_column(columns, TIME_COLUMN_CANDIDATES)
            value_col = choose_column(columns, VALUE_COLUMN_CANDIDATES)
            if time_col is None or value_col is None:
                continue

            for prefix in unique_prefixes:
                active_pattern = f"{prefix}/%/active_power"
                reactive_pattern = f"{prefix}/%/reactive_power"
                query = sql.SQL(
                    """
                    WITH clean AS (
                        SELECT {time_col} AS sample_time,
                               data_name,
                               MAX({value_col}) AS value
                        FROM {schema_name}.{table_name}
                        WHERE (data_name LIKE %s OR data_name LIKE %s)
                          AND ABS({value_col}) < %s
                        GROUP BY {time_col}, data_name
                    )
                    SELECT sample_time,
                           SUM(CASE WHEN data_name LIKE %s THEN value ELSE 0 END) AS active_power_w,
                           SUM(CASE WHEN data_name LIKE %s THEN value ELSE 0 END) AS reactive_power_var
                    FROM clean
                    GROUP BY sample_time
                    ORDER BY sample_time
                    """
                ).format(
                    time_col=sql.Identifier(time_col),
                    value_col=sql.Identifier(value_col),
                    schema_name=sql.Identifier(schema_name),
                    table_name=sql.Identifier(table_name),
                )
                with connection.cursor() as cursor:
                    cursor.execute(
                        query,
                        (
                            active_pattern,
                            reactive_pattern,
                            MAX_REASONABLE_POWER,
                            active_pattern,
                            reactive_pattern,
                        ),
                    )
                    rows = cursor.fetchall()
                if not rows:
                    continue

                frame = pd.DataFrame(
                    rows,
                    columns=["sample_time", "active_power_w", "reactive_power_var"],
                )
                frame["apparent_power_va"] = (
                    frame["active_power_w"] ** 2 + frame["reactive_power_var"] ** 2
                ).pow(0.5)
                return frame
    except Exception:
        return pd.DataFrame()
    finally:
        connection.close()

    return pd.DataFrame()


def topology_figure(nodes: pd.DataFrame, edges: pd.DataFrame) -> go.Figure:
    """Build a simple topology graph visualization with a spring layout."""
    if nodes.empty or edges.empty or nx is None:
        return go.Figure()

    graph = nx.Graph()
    for row in nodes.itertuples(index=False):
        graph.add_node(
            row.id,
            title=row.title,
            subtitle=row.subtitle,
            color=row.color,
            radius=row.node_radius,
        )
    for row in edges.itertuples(index=False):
        graph.add_edge(
            row.source,
            row.target,
            color=row.color,
            thickness=row.thickness,
            element_type=row.element_type,
        )

    positions = nx.spring_layout(graph, seed=19, k=max(0.25, 1.5 / math.sqrt(len(graph.nodes))))

    edge_x: list[float] = []
    edge_y: list[float] = []
    for source, target in graph.edges():
        x0, y0 = positions[source]
        x1, y1 = positions[target]
        edge_x.extend([x0, x1, None])
        edge_y.extend([y0, y1, None])

    node_x = [positions[node_id][0] for node_id in graph.nodes]
    node_y = [positions[node_id][1] for node_id in graph.nodes]
    hover_text = [
        f"{graph.nodes[node_id]['title']}<br>{graph.nodes[node_id]['subtitle']}"
        for node_id in graph.nodes
    ]
    node_color = [graph.nodes[node_id]["color"] for node_id in graph.nodes]
    node_size = [4 for _ in graph.nodes]

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=edge_x,
            y=edge_y,
            mode="lines",
            line={"color": "#94a3b8", "width": 1.5},
            hoverinfo="skip",
            name="connections",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=node_x,
            y=node_y,
            mode="markers",
            hovertext=hover_text,
            hoverinfo="text",
            marker={
                "size": node_size,
                "color": node_color,
                "line": {"width": 1, "color": "#0f172a"},
            },
            name="buses",
        )
    )
    fig.update_layout(
        margin={"l": 16, "r": 16, "t": 16, "b": 16},
        xaxis={"visible": False},
        yaxis={"visible": False},
        plot_bgcolor="rgba(255,255,255,0.55)",
        paper_bgcolor="rgba(0,0,0,0)",
        showlegend=False,
    )
    return fig


def timeseries_figure(series: pd.DataFrame) -> go.Figure:
    """Build the transformer-interface timeseries chart."""
    fig = go.Figure()
    if series.empty:
        return fig

    fig.add_trace(
        go.Scatter(
            x=series["sample_time"],
            y=series["active_power_w"],
            mode="lines",
            name="Active power [W]",
            line={"color": "#0f766e"},
        )
    )
    fig.add_trace(
        go.Scatter(
            x=series["sample_time"],
            y=series["reactive_power_var"],
            mode="lines",
            name="Reactive power [VAr]",
            line={"color": "#b45309"},
        )
    )
    fig.add_trace(
        go.Scatter(
            x=series["sample_time"],
            y=series["apparent_power_va"],
            mode="lines",
            name="Apparent power [VA]",
            line={"color": "#1d4ed8", "dash": "dot"},
        )
    )
    fig.update_layout(
        margin={"l": 16, "r": 16, "t": 16, "b": 16},
        xaxis_title="Simulation time",
        yaxis_title="Power",
        hovermode="x unified",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(255,255,255,0.55)",
        legend={"orientation": "h", "y": 1.08, "x": 0},
    )
    return fig


def geographic_layout(
    nodes: pd.DataFrame,
    edges: pd.DataFrame,
    marker_df: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Project the topology graph onto a local geographic view around the marker."""
    if nodes.empty or edges.empty or marker_df.empty or nx is None:
        return pd.DataFrame(), pd.DataFrame()

    center_lat = float(marker_df.iloc[0]["lat"])
    center_lon = float(marker_df.iloc[0]["lon"])

    graph = nx.Graph()
    for row in nodes.itertuples(index=False):
        graph.add_node(
            row.id,
            title=row.title,
            color=row.color,
            radius=row.node_radius,
            kind=row.kind,
        )
    for row in edges.itertuples(index=False):
        graph.add_edge(row.source, row.target, color=row.color, element_type=row.element_type)

    positions = nx.spring_layout(graph, seed=23, k=max(0.3, 1.6 / math.sqrt(len(graph.nodes))))
    scale = min(0.018, 0.006 + len(graph.nodes) * 0.00012)
    lon_scale = scale / max(math.cos(math.radians(center_lat)), 0.2)
    lat_scale = scale

    mapped_nodes: list[dict[str, Any]] = []
    for node_id, attrs in graph.nodes(data=True):
        x, y = positions[node_id]
        mapped_nodes.append(
            {
                "id": node_id,
                "title": attrs["title"],
                "kind": attrs["kind"],
                "lat": center_lat + (y * lat_scale),
                "lon": center_lon + (x * lon_scale),
                "color": attrs["color"],
                "radius": 5,
            }
        )

    node_frame = pd.DataFrame(mapped_nodes)
    if node_frame.empty:
        return pd.DataFrame(), pd.DataFrame()

    edge_frame = edges.merge(
        node_frame[["id", "lat", "lon"]].rename(
            columns={"id": "source", "lat": "source_lat", "lon": "source_lon"}
        ),
        on="source",
        how="left",
    ).merge(
        node_frame[["id", "lat", "lon"]].rename(
            columns={"id": "target", "lat": "target_lat", "lon": "target_lon"}
        ),
        on="target",
        how="left",
    )
    edge_frame["source_position"] = edge_frame.apply(
        lambda row: [row["source_lon"], row["source_lat"]],
        axis=1,
    )
    edge_frame["target_position"] = edge_frame.apply(
        lambda row: [row["target_lon"], row["target_lat"]],
        axis=1,
    )
    return node_frame, edge_frame


def decode_net_table(net_json_raw: str, table_name: str) -> tuple[list[str], list[Any], list[list[Any]]]:
    """Decode one serialized pandapower table from net_json."""
    net = json.loads(net_json_raw)
    table = json.loads(net["_object"][table_name]["_object"])
    return table["columns"], table["index"], table["data"]


def parse_geo_value(raw_geo: Any) -> dict[str, Any] | None:
    """Parse a serialized GeoJSON-like string from pandapower geo columns."""
    if raw_geo in (None, "", "null"):
        return None
    if isinstance(raw_geo, dict):
        return raw_geo
    if isinstance(raw_geo, str):
        try:
            return json.loads(raw_geo)
        except json.JSONDecodeError:
            return None
    return None


@st.cache_data(ttl=5)
def fetch_grid_geodata(
    db_cfg: DbConfig,
    selected_grid: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Read actual bus and line coordinates from CST Mongo net_json."""
    client = mongo_client(db_cfg)
    if client is None:
        return pd.DataFrame(), pd.DataFrame()

    try:
        doc = client[db_cfg.mongo_db]["custom_metadata"].find_one({"cst_007": selected_grid})
        if not doc or "net_json" not in doc:
            return pd.DataFrame(), pd.DataFrame()

        net_json_raw = doc["net_json"]
        if not isinstance(net_json_raw, str):
            net_json_raw = json.dumps(net_json_raw)

        bus_columns, bus_indices, bus_data = decode_net_table(net_json_raw, "bus")
        line_columns, line_indices, line_data = decode_net_table(net_json_raw, "line")
    except Exception:
        return pd.DataFrame(), pd.DataFrame()
    finally:
        client.close()

    bus_name_idx = bus_columns.index("name") if "name" in bus_columns else None
    bus_geo_idx = bus_columns.index("geo") if "geo" in bus_columns else None
    line_name_idx = line_columns.index("name") if "name" in line_columns else None
    line_geo_idx = line_columns.index("geo") if "geo" in line_columns else None

    bus_rows: list[dict[str, Any]] = []
    if bus_geo_idx is not None:
        for bus_index, row in zip(bus_indices, bus_data):
            geo = parse_geo_value(row[bus_geo_idx])
            if not geo or geo.get("type") != "Point":
                continue
            coords = geo.get("coordinates", [])
            if len(coords) < 2:
                continue
            bus_rows.append(
                {
                    "bus_index": int(bus_index),
                    "title": row[bus_name_idx] if bus_name_idx is not None else f"bus {bus_index}",
                    "lon": float(coords[0]),
                    "lat": float(coords[1]),
                    "radius": 5,
                }
            )

    line_rows: list[dict[str, Any]] = []
    if line_geo_idx is not None:
        for line_index, row in zip(line_indices, line_data):
            geo = parse_geo_value(row[line_geo_idx])
            if not geo or geo.get("type") != "LineString":
                continue
            coords = geo.get("coordinates", [])
            if len(coords) < 2:
                continue
            line_rows.append(
                {
                    "line_index": int(line_index),
                    "title": row[line_name_idx] if line_name_idx is not None else f"line {line_index}",
                    "path": [[float(lon), float(lat)] for lon, lat in coords],
                }
            )

    return pd.DataFrame(bus_rows), pd.DataFrame(line_rows)


def hex_to_rgb(color: str) -> list[int]:
    """Convert a hex color into an RGB list for pydeck."""
    color = color.lstrip("#")
    if len(color) != 6:
        return [15, 23, 42]
    return [int(color[i : i + 2], 16) for i in (0, 2, 4)]


def format_config_value(value: Any) -> str:
    """Format nested config values compactly for node labels."""
    if isinstance(value, dict):
        parts = [f"{key}: {format_config_value(inner)}" for key, inner in value.items()]
        return "{ " + ", ".join(parts) + " }"
    if isinstance(value, list):
        return "[" + ", ".join(format_config_value(item) for item in value) + "]"
    return str(value)


def config_node_dot_label(node: dict[str, Any]) -> str:
    """Build an HTML-like Graphviz label for one config node."""
    rows = [
        (
            f'<TR><TD BGCOLOR="#082f49"><FONT COLOR="white"><B>{escape(str(node.get("name", node.get("id", "node"))))}</B></FONT></TD></TR>'
        ),
        (
            f'<TR><TD ALIGN="LEFT" BGCOLOR="#ecfeff"><FONT COLOR="#0f172a"><B>type</B>: {escape(str(node.get("class", "unknown")))}</FONT></TD></TR>'
        ),
        (
            f'<TR><TD ALIGN="LEFT"><FONT COLOR="#334155"><B>id</B>: {escape(str(node.get("id", "")))}</FONT></TD></TR>'
        ),
    ]

    for key, value in (node.get("config") or {}).items():
        rows.append(
            "<TR><TD ALIGN=\"LEFT\">"
            f"<FONT COLOR=\"#334155\"><B>{escape(str(key))}</B>: {escape(format_config_value(value))}</FONT>"
            "</TD></TR>"
        )

    return (
        '<<TABLE BORDER="0" CELLBORDER="1" CELLSPACING="0" CELLPADDING="8" COLOR="#cbd5e1" BGCOLOR="white">'
        + "".join(rows)
        + "</TABLE>>"
    )


def append_config_node_lines(
    lines: list[str],
    node: dict[str, Any],
    parent_dot_id: str | None,
    path: str,
) -> None:
    """Recursively append Graphviz nodes and edges for the config tree."""
    dot_id = path.replace(".", "_").replace("-", "_")
    lines.append(
        f'{dot_id} [shape=box style="rounded,filled" fillcolor="white" color="#94a3b8" '
        f'label={config_node_dot_label(node)}];'
    )
    if parent_dot_id is not None:
        lines.append(f"{parent_dot_id} -> {dot_id};")

    for index, child in enumerate(node.get("sub_federates", []) or []):
        child_path = f"{path}_{index}"
        append_config_node_lines(lines, child, dot_id, child_path)


def build_config_tree_dot(cfg: dict[str, Any]) -> str:
    """Render the experiment federation subtree as a Graphviz dot graph."""
    federation = cfg.get("federation")
    if not isinstance(federation, dict):
        return "digraph { empty [label=\"No federation config found.\"]; }"

    lines = [
        "digraph ConfigTree {",
        'graph [rankdir=TB, bgcolor="transparent", nodesep="0.45", ranksep="0.75"];',
        'node [fontname="Helvetica"];',
        'edge [color="#94a3b8", penwidth=1.4, arrowsize=0.7];',
    ]
    append_config_node_lines(lines, federation, None, "root")
    lines.append("}")
    return "\n".join(lines)


def map_deck(
    marker_df: pd.DataFrame,
    map_nodes: pd.DataFrame,
    map_edges: pd.DataFrame,
) -> Any | None:
    """Build a pydeck map showing the selected grid around its location marker."""
    if pdk is None or (marker_df.empty and map_nodes.empty):
        return None

    if not map_nodes.empty:
        center_lat = float(map_nodes["lat"].mean())
        center_lon = float(map_nodes["lon"].mean())
    else:
        center_lat = float(marker_df.iloc[0]["lat"])
        center_lon = float(marker_df.iloc[0]["lon"])
    layers: list[Any] = [
        pdk.Layer(
            "ScatterplotLayer",
            data=marker_df,
            get_position="[lon, lat]",
            get_fill_color=[185, 28, 28, 210],
            get_radius=8,
            radius_units="pixels",
            radius_min_pixels=8,
            pickable=True,
        )
    ]

    if not map_edges.empty:
        layers.append(
            pdk.Layer(
                "PathLayer",
                data=map_edges,
                get_path="path",
                get_color=[15, 23, 42, 150],
                get_width=3,
                pickable=True,
            )
        )
    if not map_nodes.empty:
        node_rows = map_nodes.copy()
        if "color" not in node_rows.columns:
            node_rows["color"] = "#2563eb"
        node_rows["fill_color"] = node_rows["color"].apply(hex_to_rgb)
        layers.append(
            pdk.Layer(
                "ScatterplotLayer",
                data=node_rows,
                get_position="[lon, lat]",
                get_fill_color="fill_color",
                get_radius="radius",
                radius_units="pixels",
                radius_min_pixels=5,
                get_line_color=[255, 255, 255, 220],
                line_width_min_pixels=1,
                stroked=True,
                pickable=True,
            )
        )

    return pdk.Deck(
        map_provider="carto",
        map_style="light_no_labels",
        initial_view_state=pdk.ViewState(
            latitude=center_lat,
            longitude=center_lon,
            zoom=11,
            pitch=0,
            bearing=0,
        ),
        layers=layers,
        height=520,
        tooltip={"text": "{title}"},
    )


def process_is_running(process: subprocess.Popen[str] | None) -> bool:
    """Return whether the current experiment process is still running."""
    return process is not None and process.poll() is None


def start_run(experiment_path: Path) -> None:
    """Launch run.sh in the repository root and stream output to a log file."""
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    st.cache_data.clear()
    st.session_state.pop("resolved_grid", None)
    st.session_state["run_cancel_requested_at"] = None
    RUN_LOG.write_text("")
    with RUN_LOG.open("w") as log_file:
        process = subprocess.Popen(
            ["bash", str(RUN_SCRIPT), str(experiment_path)],
            cwd=REPO_ROOT,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )

    st.session_state["run_process"] = process
    st.session_state["run_started_at"] = datetime.now().isoformat(timespec="seconds")


def cancel_run() -> None:
    """Request termination of the current experiment run and its process group."""
    process = st.session_state.get("run_process")
    if not process_is_running(process):
        return

    st.session_state["run_cancel_requested_at"] = datetime.now().isoformat(timespec="seconds")
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except OSError:
        process.terminate()


def read_run_log() -> str:
    """Return the current run log content."""
    if not RUN_LOG.exists():
        return ""
    return RUN_LOG.read_text()


def visible_run_log() -> str:
    """Return only log content relevant to the current dashboard session."""
    if st.session_state.get("run_started_at") is None:
        return ""
    return read_run_log()


def dependency_messages() -> list[str]:
    """Collect missing optional Python dependencies for the dashboard."""
    missing: list[str] = []
    if psycopg is None:
        missing.append("`psycopg` is not installed, so Postgres data cannot be queried.")
    if MongoClient is None:
        missing.append("`pymongo` is not installed, so CST metadata in Mongo cannot be queried.")
    if nx is None:
        missing.append("`networkx` is not installed, so topology rendering is disabled.")
    if pdk is None:
        missing.append("`pydeck` is not installed, so the grid-on-map view is disabled.")
    if pgeocode is None:
        missing.append("`pgeocode` is not installed, so PLZ map markers need explicit lat/lon in YAML.")
    return missing


def should_expect_live_connections() -> bool:
    """Return whether the UI should expect CST services to be reachable."""
    if process_is_running(st.session_state.get("run_process")):
        return True
    return st.session_state.get("run_started_at") is not None


def postgres_ready(db_cfg: DbConfig) -> bool:
    """Check whether Postgres is reachable with the configured credentials."""
    if psycopg is None:
        return False
    try:
        with psycopg.connect(
            host=db_cfg.postgres_host,
            port=db_cfg.postgres_port,
            dbname=db_cfg.postgres_db,
            user=db_cfg.postgres_user,
            password=db_cfg.postgres_password,
            connect_timeout=2,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
        return True
    except Exception:
        return False


def mongo_ready(db_cfg: DbConfig) -> bool:
    """Check whether Mongo is reachable with the configured credentials."""
    client = mongo_client(db_cfg)
    if client is None:
        return False
    try:
        client.admin.command("ping")
        return True
    except Exception:
        return False
    finally:
        client.close()


def db_status(db_cfg: DbConfig) -> tuple[str, str]:
    """Return a UI status and message for the preflight databases."""
    if not should_expect_live_connections():
        return "idle", "Waiting for `run.sh` to start the preflight services."

    postgres_is_ready = postgres_ready(db_cfg)
    mongo_is_ready = mongo_ready(db_cfg)

    if postgres_is_ready and mongo_is_ready:
        return "ready", "Postgres and Mongo are reachable."
    if process_is_running(st.session_state.get("run_process")):
        return "starting", "Waiting for Postgres and Mongo to become reachable."
    return "unreachable", "Expected CST databases are not reachable from the dashboard."


def main() -> None:
    """Render the GridLock experiment dashboard."""
    st.set_page_config(page_title="GridLock Dashboard", layout="wide")

    experiment_files = available_experiment_files()
    if not experiment_files:
        st.error("No experiment YAML files were found under config/.")
        return

    selected_experiment_path = st.sidebar.selectbox(
        "Experiment file",
        options=experiment_files,
        format_func=lambda path: path.name,
    )
    cfg = read_experiment(selected_experiment_path)
    db_cfg = load_db_config()
    render_header(cfg)

    if "run_process" not in st.session_state:
        st.session_state["run_process"] = None
    if "run_started_at" not in st.session_state:
        st.session_state["run_started_at"] = None
        if RUN_LOG.exists():
            RUN_LOG.write_text("")
    if "run_cancel_requested_at" not in st.session_state:
        st.session_state["run_cancel_requested_at"] = None

    run_process = st.session_state.get("run_process")
    running = process_is_running(run_process)
    cancel_requested_at = st.session_state.get("run_cancel_requested_at")

    left, middle, right = st.columns([1, 1, 2])
    with left:
        st.metric("Experiment", experiment_name(cfg))
    with middle:
        st.metric("Analysis Schema", experiment_schema(cfg))
    with right:
        start_col, cancel_col = st.columns(2)
        with start_col:
            if st.button("Start run.sh", disabled=running, type="primary", width="stretch"):
                start_run(selected_experiment_path)
                time.sleep(2)
                st.rerun()
        with cancel_col:
            if st.button("Cancel run", disabled=not running, width="stretch"):
                cancel_run()
                time.sleep(1)
                st.rerun()
        if st.session_state.get("run_started_at"):
            st.caption(f"Last start: {st.session_state['run_started_at']}")
        if cancel_requested_at and running:
            st.caption(f"Cancel requested: {cancel_requested_at}")
        st.caption("Running from repository root with output written to `generated/streamlit-run.log`.")
        st.caption(f"DB config source: `{PREFLIGHT_ENV_PATH.relative_to(REPO_ROOT)}`")

    if running:
        if cancel_requested_at:
            st.warning("Cancellation requested. Waiting for `run.sh` to stop.")
        else:
            st.info("Experiment run is active.")
    elif st.session_state.get("run_started_at"):
        return_code = run_process.poll() if run_process is not None else "unknown"
        if cancel_requested_at:
            st.info("Last experiment run was cancelled.")
        else:
            st.info(f"Last experiment run finished with exit code {return_code}.")

    for message in dependency_messages():
        st.warning(message)

    expect_connections = should_expect_live_connections()
    status, status_message = db_status(db_cfg)
    if status == "idle":
        st.info(status_message)
    elif status == "starting":
        st.info(status_message)
    elif status == "ready":
        st.success(status_message)
    else:
        st.warning(status_message)

    st.markdown('<div class="gridlock-card">', unsafe_allow_html=True)
    st.subheader("Config Tree")
    st.graphviz_chart(build_config_tree_dot(cfg))
    st.markdown("</div>", unsafe_allow_html=True)

    topology_grid_ids: list[str] = []
    metadata_df = pd.DataFrame()
    if status == "ready":
        topology_grid_ids = fetch_topology_grid_ids(db_cfg, experiment_name(cfg))
        metadata_df = fetch_grid_metadata(db_cfg)
    elif not expect_connections:
        st.info("Database-backed views stay idle until you start `run.sh` from this dashboard.")

    selected_grid_options = expected_grid_ids(cfg)
    if not selected_grid_options:
        selected_grid_options = topology_grid_ids
    if not selected_grid_options and not metadata_df.empty:
        root_id = root_grid_id(cfg)
        selected_grid_options = sorted(
            metadata_df.loc[metadata_df["grid_id"] == root_id, "resolved_grid_id"].dropna().tolist()
        )

    if not selected_grid_options:
        if status in {"starting", "unreachable"}:
            st.warning(
                "No resolved grids are available yet. Wait for the preflight databases and experiment export to complete, then refresh."
            )
        return

    selected_grid = st.sidebar.selectbox("Resolved grid", selected_grid_options, key="resolved_grid")
    location_entry = match_location_entry(selected_grid, root_grid_id(cfg), cfg)
    marker_df = resolve_marker(location_entry)
    nodes = pd.DataFrame()
    edges = pd.DataFrame()
    transformer_series = pd.DataFrame()
    catalog = pd.DataFrame()
    if status == "ready":
        nodes, edges = fetch_topology_data(db_cfg, experiment_name(cfg), selected_grid)
        transformer_series = fetch_transformer_proxy_series(
            db_cfg,
            experiment_schema(cfg),
            selected_grid,
        )
        catalog = fetch_timeseries_catalog(db_cfg, experiment_schema(cfg))

    if not metadata_df.empty:
        grid_meta = metadata_df.loc[metadata_df["resolved_grid_id"] == selected_grid]
        if not grid_meta.empty:
            a, b, c = st.columns(3)
            with a:
                st.metric("Resolved Grid", selected_grid)
            with b:
                st.metric("Buses", int(grid_meta.iloc[0]["bus_count"]))
            with c:
                st.metric("Loads", int(grid_meta.iloc[0]["load_count"]))

    map_nodes = pd.DataFrame()
    map_edges = pd.DataFrame()
    if status == "ready":
        map_nodes, map_edges = fetch_grid_geodata(db_cfg, selected_grid)
        if not map_nodes.empty and not nodes.empty and "bus_index" in map_nodes.columns:
            map_nodes = map_nodes.merge(
                nodes[["bus_index", "color", "kind"]],
                on="bus_index",
                how="left",
            )
            map_nodes["color"] = map_nodes["color"].fillna("#2563eb")
            map_nodes["kind"] = map_nodes["kind"].fillna("bus")
    if map_nodes.empty and not marker_df.empty:
        map_nodes, map_edges = geographic_layout(nodes, edges, marker_df)

    st.markdown('<div class="gridlock-card">', unsafe_allow_html=True)
    st.subheader("Grid On Map")
    if marker_df.empty and map_nodes.empty:
        st.info("No coordinates were resolved from the selected experiment entry.")
    elif pdk is None:
        fallback_map = marker_df if not marker_df.empty else map_nodes[["lat", "lon"]]
        st.map(fallback_map, size=40, color="#b91c1c", zoom=7)
        st.caption("Install `pydeck` to see the grid linework overlay.")
    else:
        deck = map_deck(marker_df, map_nodes, map_edges)
        if deck is not None:
            st.pydeck_chart(deck, width="stretch")
        if not map_nodes.empty and "bus_index" in map_nodes.columns:
            st.caption("Using actual bus and line coordinates stored in the CST metadata net JSON.")
        else:
            st.caption(
                "The overlaid grid shape is a schematic projection around the selected experiment location, not surveyed GIS geometry."
            )
        st.caption(
            f"Experiment query: {location_entry}"
            if location_entry
            else "Marker inferred from the selected grid."
        )
    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown('<div class="gridlock-card">', unsafe_allow_html=True)
    st.subheader("Transformer Interface Power")
    st.caption(
        "This chart sums the selected grid's downstream active/reactive power streams from the CST timeseries tables."
    )
    if transformer_series.empty:
        st.info("No matching active/reactive power streams were found in the analysis schema.")
    else:
        st.plotly_chart(timeseries_figure(transformer_series), width="stretch")
        st.dataframe(transformer_series.tail(20), width="stretch")
    st.markdown("</div>", unsafe_allow_html=True)

    with st.expander("Available logged data streams"):
        if catalog.empty:
            st.info("No CST timeseries catalog could be read from Postgres.")
        else:
            st.dataframe(catalog, width="stretch")

    with st.expander("Run log", expanded=running):
        log_content = visible_run_log()
        st.code(log_content[-12000:] if log_content else "No run output yet.", language="text")


if __name__ == "__main__":
    main()
