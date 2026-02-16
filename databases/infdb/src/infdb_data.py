"""InfDB data access functions for the data setup resolver.

Provides query helpers to fetch pandapower grids from the InfDB
pylovo schema and resolve experiment location queries into named
(federate_name, net) tuples.
"""

import pandas as pd
import pandapower as pp
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


def resolve_grid_queries(
    infdb,
    log,
    grid_id: str,
    queries: list[dict],
) -> list[tuple[str, pp.pandapowerNet]]:
    """Resolve location queries into named pandapower nets.

    Each query dict must contain 'plz' and may optionally contain
    'kcid' and 'bcid' for further filtering.  Federate names follow
    the pattern ``{grid_id}_{plz}_{sequential_index}``.

    Args:
        infdb: InfDB client instance.
        log: Logger instance.
        grid_id: Base identifier prefix (e.g. "lv-grid").
        queries: List of query dicts from experiment.yml location.

    Returns:
        List of (federate_name, pandapower_net) tuples.
    """
    net_list: list[tuple[str, pp.pandapowerNet]] = []
    counters: dict[int, int] = {}  # plz -> next index

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

        if plz not in counters:
            counters[plz] = 0

        for _, row in net_df.iterrows():
            net = pp.from_json_string(row["grid"])
            idx = counters[plz]
            federate_name = f"{grid_id}_{plz}_{idx}"
            net_list.append((federate_name, net))
            log.info(f"  Resolved {federate_name} ({len(net.bus)} buses)")
            counters[plz] = idx + 1

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

