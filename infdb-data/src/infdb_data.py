import os
import pandas as pd
import pandapower as pp
from sqlalchemy import create_engine
from typing import Optional


def get_pylovo_grid(infdb, plz: int) -> pd.DataFrame:
    """
    Pylovo grid querying using InfDB client.

    Retrieves pandapower grid json from the pylovo schema using
    the InfDB database engine and returns a DataFrame with multiple rows,
    each containing a grid JSON model.

    Args:
        infdb: InfDB client instance with database connection.
        plz: postcode to query the pylovo grid for.

    Returns:
        DataFrame: Multiple rows with kcid, bcid, and grid JSON data.
    """
    log = infdb.get_logger()
    sql = f"SELECT kcid, bcid, grid FROM pylovo.grid_result WHERE plz={plz}"
    with infdb.connect() as db:
        result = db.execute_query(sql)

    log.debug(f"Query result type: {type(result)}")

    # Convert to DataFrame if it's a list of tuples/dicts
    if isinstance(result, list):
        if len(result) > 0:
            log.debug(f"First row type: {type(result[0])}, value: {result[0] if not isinstance(result[0], str) else 'JSON string'}")
        net_df = pd.DataFrame(result, columns=['kcid', 'bcid', 'grid'])
    else:
        net_df = result

    return net_df


def save_pylovo_grids(infdb, net_df: pd.DataFrame, output_dir: str,
                      plz: int, kcid: Optional[int] = None,
                      bcid: Optional[int] = None) -> None:
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
    log = infdb.get_logger()

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

