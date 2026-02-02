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

    try:
        # ===========================================================
        # Start your added python code in folder "src"
        # ===========================================================
        log.info("Start pylovo grid import")
        net = infdb_data.get_pylovo_grid(infdb=infdb, plz=plz)
        pp.to_json(net, f"pylovo_grid_plz_{plz}.json")

    except Exception as e:
        log.error(f"Something went wrong: {str(e)}")
        infdb.stop_logger()
        raise e

if __name__ == "__main__":
    main()
