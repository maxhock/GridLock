"""Main entry point for the load-player federate.

Each load-player federate is launched as its own container/process by
docker-compose.  It reads a timeseries CSV and publishes active/reactive
power at each co-simulation time step via CST/HELICS.

Usage:
    python main.py --scenario TestGridScenario \
                   --federate_name lv-grid_91301_0.loadhouse_0 \
                   --timeseries /data/input/building_timeseries.csv
"""

import argparse

from src.load_player import LoadPlayerFederate, load_timeseries


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments.

    Returns:
        Namespace with scenario, federate_name, and timeseries path.
    """
    parser = argparse.ArgumentParser(description="Load-player federate using CST")
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
        help="Federate name matching federation config",
    )
    parser.add_argument(
        "--timeseries",
        type=str,
        required=True,
        help="Path to timeseries CSV file",
    )
    args, _ = parser.parse_known_args()
    return args


def run_load_player(
    federate_name: str,
    timeseries_path: str,
    scenario_name: str,
) -> None:
    """Run a single LoadPlayerFederate lifecycle.

    Uses CST's built-in ``run()`` which calls
    ``create_federate`` → ``run_cosim_loop`` → ``destroy_federate``.

    Args:
        federate_name: Unique HELICS federate name.
        timeseries_path: Path to the timeseries CSV.
        scenario_name: CST scenario name to look up in meta_store.
    """
    ts = load_timeseries(timeseries_path)
    federate = LoadPlayerFederate(federate_name, ts)
    federate.run(scenario_name, use_meta_db="json", use_data_db="csv")


def main(
    scenario_name: str | None = None,
    federate_name: str | None = None,
    timeseries_path: str | None = None,
) -> None:
    """Load timeseries and run load-player federate.

    Args:
        scenario_name: CST scenario name. If None, parsed from CLI.
        federate_name: Federate name. If None, parsed from CLI.
        timeseries_path: Path to timeseries CSV. If None, parsed from CLI.
    """
    if scenario_name is None:
        args = parse_args()
        scenario_name = args.scenario
        federate_name = args.federate_name
        timeseries_path = args.timeseries

    run_load_player(federate_name, timeseries_path, scenario_name)


if __name__ == "__main__":
    main()
