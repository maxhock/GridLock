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
import os

from src.load_player import LoadPlayerFederate, load_timeseries


def parse_args() -> argparse.Namespace:
    """Read the scenario, federate name and CSV path composegen put on the CLI."""
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


def get_db_backends_from_env() -> tuple[str, str]:
    """Read DB backends from environment variables set by composegen."""
    use_meta_db = os.getenv("CST_USE_META_DB")
    use_data_db = os.getenv("CST_USE_DATA_DB")

    if not use_meta_db or not use_data_db:
        raise ValueError(
            "Missing DB backend env vars. Expected CST_USE_META_DB and "
            "CST_USE_DATA_DB from experiment general config."
        )

    return use_meta_db, use_data_db


def run_load_player(
    federate_name: str,
    timeseries_path: str,
    scenario_name: str,
    use_meta_db: str,
    use_data_db: str,
) -> None:
    """Run one load player: CST's ``run()`` does create, loop, destroy."""
    ts = load_timeseries(timeseries_path)
    federate = LoadPlayerFederate(federate_name, ts)
    federate.run(
        scenario_name,
        use_meta_db=use_meta_db,
        use_data_db=use_data_db,
    )


def main(
    scenario_name: str | None = None,
    federate_name: str | None = None,
    timeseries_path: str | None = None,
) -> None:
    """Run one load player, taking the names and CSV path from the CLI unless given."""
    if scenario_name is None:
        args = parse_args()
        scenario_name = args.scenario
        federate_name = args.federate_name
        timeseries_path = args.timeseries

    if scenario_name is None or federate_name is None or timeseries_path is None:
        raise ValueError(
            "scenario_name, federate_name and timeseries_path are required"
        )

    use_meta_db, use_data_db = get_db_backends_from_env()

    run_load_player(
        federate_name,
        timeseries_path,
        scenario_name,
        use_meta_db,
        use_data_db,
    )


if __name__ == "__main__":
    main()
