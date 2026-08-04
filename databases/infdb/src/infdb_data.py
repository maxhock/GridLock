"""Queries against InfDB's pylovo schema, turning location queries into named nets."""

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
    """Fetch every grid InfDB holds for a postcode, narrowed by cluster ids if given."""
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
    """Count a net's buses, loads or ext_grids straight from its JSON.

    Read out of the serialised form so this container needs no pandapower dependency.
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
    """Name every net a location query resolves to `{grid_id}_{plz}_{kcid}_{bcid}`.

    That name is the grid federate's name, and the key its net is stored under, which is
    how composegen finds the nets a postcode-only query expanded into. The grid JSON
    is passed through untouched rather than deserialised, so this module needs no
    pandapower.
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

    if not net_list:
        query_details = []
        for query in queries:
            plz = query["plz"]
            kcid = query.get("kcid")
            bcid = query.get("bcid")
            if kcid is not None and bcid is not None:
                query_details.append(f"PLZ {plz}, KCID {kcid}, BCID {bcid}")
            else:
                query_details.append(f"PLZ {plz}")
        
        raise ValueError(
            f"No grids found in InfDB pylovo.grid_result for location queries: "
            f"{', '.join(query_details)}. "
            f"Verify PLZ codes exist in the database."
        )

    return net_list


def get_demand_timeseries(
    infdb,
    log,
    plz: int,
    profile_type: str = "H0",
) -> pd.DataFrame:
    """Fetch a standard load profile for a postcode.

    Stub: returns an empty frame with the intended columns until InfDB has a timeseries
    table. Nothing calls it - load profiles come from CSVs in `data/input/` today.
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

