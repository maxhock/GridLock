"""InfDB data access functions for the data setup resolver.

Provides query helpers to fetch pandapower grids from the InfDB
pylovo schema and resolve experiment location queries into named
(federate_name, net) tuples.
"""

import json
import pandas as pd
from typing import Optional


def get_pylovo_grid(
    infdb,
    log,
    plz: int,
    kcid: Optional[int] = None,
    bcid: Optional[int] = None,
) -> pd.DataFrame:
    """Query pandapower grids from InfDB pylovo schema.

    Args:
        infdb: InfDB client instance.
        log: Logger instance.
        plz: Postcode to query.
        kcid: Optional k-means cluster ID filter.
        bcid: Optional building cluster ID filter.

    Returns:
        DataFrame with columns [kcid, bcid, grid] where grid
        is a JSON string representing a pandapower net.
    """
    sql = f"SELECT kcid, bcid, grid FROM pylovo.grid_result WHERE plz={plz}"
    if kcid is not None and bcid is not None:
        sql += f" AND kcid={kcid} AND bcid={bcid}"

    with infdb.connect() as db:
        result = db.execute_query(sql)

    if isinstance(result, list):
        net_df = pd.DataFrame(result, columns=["kcid", "bcid", "grid"])
    else:
        net_df = result

    log.debug(f"PLZ {plz}: {len(net_df)} grid(s) returned")
    return net_df


def _net_table_count(net_json: dict | str, table: str) -> int:
    """Count rows in a pandapower DataFrame table from its serialised JSON.

    Pandapower serialises each DataFrame as
    ``{"_object": {"<table>": {"_object": "<json-encoded DataFrame>"}}}``.
    We read the index length directly without importing pandapower.

    Args:
        net_json: Pandapower net serialised as a dict or JSON string.
        table: Table name (e.g. 'bus', 'load', 'ext_grid').

    Returns:
        Number of rows, or 0 if the table is absent or empty.
    """
    d = json.loads(net_json) if isinstance(net_json, str) else net_json
    inner = d.get("_object", {}).get(table, {}).get("_object", None)
    if not inner:
        return 0
    df = json.loads(inner) if isinstance(inner, str) else inner
    return len(df.get("index", []))


def resolve_grid_queries(
    infdb,
    log,
    grid_id: str,
    queries: list[dict],
) -> list[tuple[str, str]]:
    """Resolve location queries into named pandapower net JSON strings.

    Each query dict must contain 'plz' and may optionally contain
    'kcid' and 'bcid' for further filtering. Federate names follow
    the pattern ``{grid_id}_{plz}_{kcid}_{bcid}``.

    The raw grid JSON from InfDB is passed through without deserialising
    into a pandapower object, avoiding the heavy pandapower dependency.

    Args:
        infdb: InfDB client instance.
        log: Logger instance.
        grid_id: Base identifier prefix (e.g. "lv-grid").
        queries: List of query dicts from experiment.yml location.

    Returns:
        List of (federate_name, net_json_str) tuples.
    """
    net_list: list[tuple[str, str]] = []
    seen_federate_names: set[str] = set()

    for query in queries:
        plz = query["plz"]
        kcid = query.get("kcid")
        bcid = query.get("bcid")

        net_df = get_pylovo_grid(
            infdb=infdb, log=log, plz=plz, kcid=kcid, bcid=bcid
        )
        log.info(
            f"Query plz={plz}"
            + (f", kcid={kcid}, bcid={bcid}" if kcid is not None else "")
            + f" → {len(net_df)} grid(s)"
        )

        for _, row in net_df.iterrows():
            net_json_str = json.dumps(row["grid"])

            row_kcid = int(row["kcid"])
            row_bcid = int(row["bcid"])
            federate_name = f"{grid_id}_{plz}_{row_kcid}_{row_bcid}"
            if federate_name in seen_federate_names:
                raise ValueError(
                    f"Duplicate resolved federate name '{federate_name}'. "
                    "Check location query result uniqueness for (plz, kcid, bcid)."
                )
            seen_federate_names.add(federate_name)

            bus_count = _net_table_count(row["grid"], "bus")
            net_list.append((federate_name, net_json_str))
            log.info(f"  Resolved {federate_name} ({bus_count} buses)")

    return net_list


def get_demand_timeseries(
    infdb,
    log,
    plz: int,
    profile_type: str = "H0",
) -> pd.DataFrame:
    """Query annual demand timeseries from InfDB for a given location.

    TODO: Implement actual InfDB query once the timeseries table is
    available.  For now this is a placeholder that returns an empty
    DataFrame with the expected schema.

    The returned DataFrame has one row per timestep (e.g. 15-min
    resolution, 35 040 rows for one year).  Columns:

    * ``timestamp`` – UNIX epoch seconds (int)
    * ``active_power_kw`` – electrical demand in kW (float)
    * ``reactive_power_kvar`` – reactive demand in kVAr (float)

    Args:
        infdb: InfDB client instance.
        log: Logger instance.
        plz: Postcode to query timeseries for.
        profile_type: Standard load profile identifier (e.g. "H0", "H25").

    Returns:
        DataFrame with columns [timestamp, active_power_kw, reactive_power_kvar].
    """
    # Placeholder SQL – adapt once the InfDB timeseries table exists
    # sql = (
    #     f"SELECT timestamp, active_power_kw, reactive_power_kvar "
    #     f"FROM demand.timeseries "
    #     f"WHERE plz={plz} AND profile_type='{profile_type}' "
    #     f"ORDER BY timestamp"
    # )
    # with infdb.connect() as db:
    #     result = db.execute_query(sql)

    log.warning(
        f"get_demand_timeseries is a stub – returning empty DataFrame "
        f"(plz={plz}, profile_type={profile_type})"
    )
    return pd.DataFrame(columns=["timestamp", "active_power_kw", "reactive_power_kvar"])

