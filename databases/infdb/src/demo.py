"""Examples of connecting to and querying InfDB. Not used by any stage of a run."""

import os

import geopandas as gpd
from sqlalchemy import create_engine


def sql_demo(infdb):
    """Rebuild the output schema by running the `sql/` scripts in order.

    Scripts run alphabetically, so they are prefixed `01_`, `02_`, and may use
    `{input_schema}` / `{output_schema}` placeholders. No `sql/` directory exists
    in this repo, so calling this as it stands would fail.
    """
    # Schema configuration
    format_params = {
        "input_schema": infdb.get_config_value(
            [infdb.get_toolname(), "data", "input_schema"]
        ),
        "output_schema": infdb.get_config_value(
            [infdb.get_toolname(), "data", "output_schema"]
        ),
    }

    # Drop output schema if exists for development purposes
    infdb.connect().execute_query(
        "DROP SCHEMA IF EXISTS {output_schema} CASCADE".format(**format_params)
    )

    # Execute sql scripts
    infdb.get_logger().info("Running SQL scripts ...")
    SQL_DIR = os.path.join("sql")  # add subfolders here if needed
    infdb.connect().execute_sql_files(SQL_DIR, format_params=format_params)


def database_demo(infdb):
    """Read building geometries into a GeoDataFrame through the InfDB client."""
    engine = infdb.get_db_engine()
    sql = "SELECT * FROM opendata.buildings_lod2"
    gdf_buildings = gpd.read_postgis(sql, engine)
    gdf_buildings.head()

    return gdf_buildings


def database_demo_sqlalchemy(infdb):
    """Run the same query straight through SQLAlchemy.

    Discouraged: bypassing the client also bypasses the InfDB config.
    """
    # Database connection parameters
    user = "infdb_user"
    password = "infdb"
    host = "ds1.need.energy"
    port = "54328"
    db = "infdb"

    # or get parameters from infDB config
    infdb.get_db_parameters_dict()
    user = infdb.get_db_parameters_dict().get("user")
    password = infdb.get_db_parameters_dict().get("password")
    host = infdb.get_db_parameters_dict().get("host")
    port = infdb.get_db_parameters_dict().get("port")
    db = infdb.get_db_parameters_dict().get("database")

    db_connection_url = f"postgresql://{user}:{password}@{host}:{port}/{db}"

    engine = create_engine(db_connection_url)
    sql = "SELECT * FROM opendata.buildings_lod2"
    gdf_buildings = gpd.read_postgis(sql, engine)
    gdf_buildings.head()

    return gdf_buildings


def get_env_variables(infdb):
    """Read the environment variables the InfDB tool was configured with."""
    return infdb.get_env_variables_dict()
