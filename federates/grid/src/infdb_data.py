"""
Main entry point for the infdb tool.
Handles InfDB initialization, database connection, logging, and execution.
"""

import os
import pandas as pd
import pandapower as pp
from sqlalchemy import create_engine
from typing import List, Optional, Tuple


def get_pylovo_grid(
    infdb, log, plz: Optional[int] = None, kcid: Optional[int] = None, bcid: Optional[int] = None
) -> pd.DataFrame:
    """
    Pylovo grid querying using InfDB client.

    Retrieves pandapower grid json from the pylovo schema using
    the InfDB database engine and returns a DataFrame with multiple rows,
    each containing a grid JSON model.

    If plz is None, it falls back to the InfDB config value.
    If kcid and bcid are given, the query filters to that specific grid.

    Args:
        infdb: InfDB client instance with database connection.
        log: Logger instance.
        plz: Postcode to query. Falls back to config if None.
        kcid: Optional k-means cluster ID to filter.
        bcid: Optional building cluster ID to filter.

    Returns:
        DataFrame: Rows with kcid, bcid, and grid JSON data.
    """
    if plz is None:
        plz = infdb.get_config_value([infdb.get_toolname(), "data", "plz"])

    sql = f"SELECT kcid, bcid, grid FROM pylovo.grid_result WHERE plz={plz}"
    if kcid is not None and bcid is not None:
        sql += f" AND kcid={kcid} AND bcid={bcid}"

    with infdb.connect() as db:
        result = db.execute_query(sql)

    log.debug(f"Query result type: {type(result)}")

    # Convert to DataFrame if it's a list of tuples/dicts
    if isinstance(result, list):
        if len(result) > 0:
            log.debug(
                f"First row type: {type(result[0])}, "
                f"value: {result[0] if not isinstance(result[0], str) else 'JSON string'}"
            )
        net_df = pd.DataFrame(result, columns=["kcid", "bcid", "grid"])
    else:
        net_df = result

    return net_df


def resolve_grid_queries(
    infdb, log, grid_id: str, queries: List[dict]
) -> List[Tuple[str, pp.pandapowerNet]]:
    """
    Resolve a list of InfDB grid queries into named pandapower nets.

    Each query dict must contain 'plz' and optionally 'kcid' and 'bcid'.
    If only plz is given, all grids for that PLZ are returned.
    Federate names follow the pattern: {grid_id}_{plz}_{sequential_index}.

    Args:
        infdb: InfDB client instance.
        log: Logger instance.
        grid_id: Base identifier prefix for federate names (e.g. "lv-grid").
        queries: List of query dicts from grid_queries.json.

    Returns:
        List of (federate_name, pandapower_net) tuples.
    """
    net_list: List[Tuple[str, pp.pandapowerNet]] = []
    counters: dict[int, int] = {}  # plz -> next index

    for query in queries:
        plz = query["plz"]
        kcid = query.get("kcid")
        bcid = query.get("bcid")

        net_df = get_pylovo_grid(infdb=infdb, log=log, plz=plz, kcid=kcid, bcid=bcid)
        log.info(
            f"Query plz={plz}"
            + (f", kcid={kcid}, bcid={bcid}" if kcid is not None else "")
            + f" returned {len(net_df)} grid(s)"
        )

        if plz not in counters:
            counters[plz] = 0

        for _, row in net_df.iterrows():
            net = pp.from_json_string(row["grid"])
            idx = counters[plz]
            federate_name = f"{grid_id}_{plz}_{idx}"
            net_list.append((federate_name, net))
            log.info(f"Prepared {federate_name} ({len(net.bus)} buses)")
            counters[plz] = idx + 1

    return net_list


def save_pylovo_grids(infdb, log, net_df: pd.DataFrame) -> None:
    """
    Save pandapower grid JSON files from DataFrame.

    If kcid and/or bcid are provided, filters and saves only matching grids.
    Otherwise, saves all grids for the given PLZ.

    Args:
        infdb: InfDB client instance with logger.
        net_df: DataFrame with kcid, bcid, and grid JSON columns.
        output_dir: Directory path to save JSON files.
        plz: Postcode for naming files.
        kcid: Optional k-means cluster ID to filter.
        bcid: Optional building cluster ID to filter.
    """

    plz = infdb.get_config_value([infdb.get_toolname(), "data", "plz"])
    kcid = infdb.get_config_value([infdb.get_toolname(), "data", "kcid"])
    bcid = infdb.get_config_value([infdb.get_toolname(), "data", "bcid"])
    output_dir = infdb.get_config_value([infdb.get_toolname(), "data", "output_dir"])

    # Create output directory if it doesn't exist
    os.makedirs(output_dir, exist_ok=True)

    # Filter DataFrame if kcid or bcid are specified
    filtered_df = net_df.copy()
    if kcid is not None:
        filtered_df = filtered_df[filtered_df['kcid'] == kcid]
        log.info(f"Filtered by KCID {kcid}: {len(filtered_df)} grid(s)")
    if bcid is not None:
        filtered_df = filtered_df[filtered_df['bcid'] == bcid]
        log.info(f"Filtered by BCID {bcid}: {len(filtered_df)} grid(s)")

    if len(filtered_df) == 0:
        log.warning(f"No grids found matching the filter criteria")
        return

    # Save each grid as JSON
    for index, row in filtered_df.iterrows():
        kcid_val = row['kcid']
        bcid_val = row['bcid']
        grid_json = row['grid']

        # Load pandapower network from JSON string
        net = pp.from_json_string(grid_json)

        # Create filename
        filename = f"pylovo_grid_plz_{plz}_kcid_{kcid_val}_bcid_{bcid_val}.json"
        filepath = os.path.join(output_dir, filename)

        # Save to JSON file
        pp.to_json(net, filepath)
        log.info(f"Saved grid to {filepath} ({len(net.bus)} buses)")

    log.info(f"Successfully saved {len(filtered_df)} grid(s) to {output_dir}")

