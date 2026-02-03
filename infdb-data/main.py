"""
Main entry point for the infdb-data tool.
Handles InfDB initialization, database connection, logging, and execution.
"""

# Import packages
import os
import pandapower as pp

from infdb import InfDB
from src import infdb_data


def main():
    """
    Initializes InfDB handler, sets up logging, connects to the database,
    and runs the functions. Handles exceptions and logs errors.
    """

    # Initialize InfDB handler
    infdb = InfDB(tool_name="infdb-data", config_path="configs")

    # Start message
    log = infdb.get_logger()
    log.info(f"Starting {infdb.get_toolname()} tool")

    plz = infdb.get_config_value([infdb.get_toolname(), "data", "plz"])
    kcid = infdb.get_config_value([infdb.get_toolname(), "data", "kcid"])
    bcid = infdb.get_config_value([infdb.get_toolname(), "data", "bcid"])
    output_dir = infdb.get_config_value([infdb.get_toolname(), "data", "output_dir"])

    try:
        # ===========================================================
        # Start your added python code in folder "src"
        # ===========================================================
        log.info("Start pylovo grid import")
        net_df = infdb_data.get_pylovo_grid(infdb=infdb, plz=plz)
        log.info(f"Retrieved {len(net_df)} grid(s) for PLZ {plz}")

        # Save grids to JSON files (filtered by kcid/bcid if specified)
        infdb_data.save_pylovo_grids(
            infdb=infdb,
            net_df=net_df,
            output_dir=output_dir,
            plz=plz,
            kcid=kcid,
            bcid=bcid
        )

    except Exception as e:
        log.error(f"Something went wrong: {str(e)}")
        infdb.stop_logger()
        raise e

if __name__ == "__main__":
    main()
