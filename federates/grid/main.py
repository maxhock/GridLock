"""Main entry point for the grid federate.

Each grid federate is launched as its own container/process by
docker-compose.  It reads its pandapower net from the CST metadata
store (custom collection "grid_data"), where the infdb-data
pre-flight container wrote it.

Usage:
    python main.py --scenario TestGridScenario --federate_name lv-grid_91301_0
"""

import argparse

import pandapower as pp
from cosim_toolbox.dbms import create_metadata_manager
from src.grid import GridFederate


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments.

    Returns:
        Namespace with scenario (str) and federate_name (str).
    """
    parser = argparse.ArgumentParser(description="Grid federate using CST")
    parser.add_argument(
        "--scenario",
        type=str,
        required=True,
        help="CST scenario name (e.g. TestGridScenario)",
    )
    parser.add_argument(
        "--federate_name",
        type=str,
        required=True,
        help="Federate name matching meta_store entry (e.g. lv-grid_91301_0)",
    )
    args, _ = parser.parse_known_args()
    return args


def load_net_from_metadata(federate_name: str) -> pp.pandapowerNet:
    """Load a pandapower net from the CST metadata store.

    The infdb-data pre-flight container writes each grid's JSON
    representation into a custom "grid_data" collection, keyed
    by the federate name.

    Args:
        federate_name: Name of the federate (matches metadata key).

    Returns:
        pandapower network object.

    Raises:
        FileNotFoundError: If no grid_data entry exists for this name.
    """
    md_mgr = create_metadata_manager(backend="json", location="meta_store")
    md_mgr.connect()
    try:
        data = md_mgr.read("grid_data", federate_name)
    finally:
        md_mgr.disconnect()

    if not data:
        raise FileNotFoundError(
            f"No grid data found in metadata store for '{federate_name}'. "
            f"Ensure infdb-data pre-flight has run."
        )

    net = pp.from_json_string(data["net_json"])
    print(f"Loaded net for {federate_name}: {len(net.bus)} buses, {len(net.load)} loads")
    return net


def run_grid_federate(
    federate_name: str,
    net: pp.pandapowerNet,
    scenario_name: str,
) -> None:
    """Run a single GridFederate lifecycle.

    CST's create_federate() reads the federation/scenario JSONs
    from meta_store to configure HELICS pub/sub, period, etc.

    Args:
        federate_name: Unique HELICS federate name.
        net: pandapower network object for this grid.
        scenario_name: CST scenario name to look up in meta_store.
    """
    federate = GridFederate(federate_name, net)
    try:
        federate.create_federate(scenario_name=scenario_name)
        federate.run_cosim_loop()
    finally:
        federate.destroy_federate()


def main(
    scenario_name: str | None = None,
    federate_name: str | None = None,
) -> None:
    """Load net from CST metadata store and run grid federate.

    Args:
        scenario_name: CST scenario name. If None, parsed from CLI.
        federate_name: Federate name. If None, parsed from CLI.
    """
    if scenario_name is None:
        args = parse_args()
        scenario_name = args.scenario
        federate_name = args.federate_name

    net = load_net_from_metadata(federate_name)
    run_grid_federate(federate_name, net, scenario_name)


if __name__ == "__main__":
    main()
