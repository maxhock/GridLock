import os
import geopandas as gpd
from sqlalchemy import create_engine

def get_pylovo_grid(infdb, plz: int) -> gpd.GeoDataFrame:
    """
    Pylovo grid querying using InfDB client.

    Retrieves pandapower grid json from the pylovo schema using
    the InfDB database engine and loads it into a GeoDataFrame.

    Args:
        infdb: InfDB client instance with database connection.
        plz: postcode to query the pylovo grid for.

    Returns:
        GeoDataFrame: Buildings with heat demand data and geometry.
    """
    # engine = infdb.get_db_engine()
    sql = f"SELECT grid FROM pylovo.grid_result WHERE plz={plz}"
    with infdb.connect() as db:
        net = db.execute_query(sql)

    return net
