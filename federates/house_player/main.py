"""Main entry point for the load-player federate.

A load player reads a timeseries CSV and publishes active/reactive power at
each co-simulation time step via CST/HELICS.

Usage:
    python main.py --scenario TestGridScenario \
                   --federate_name lv-grid_91301_0.loadhouse_0 \
                   --timeseries /data/input/building_timeseries.csv

Repeat ``--federate_name``/``--timeseries`` pairs to run several players in
this one process, one thread each; composegen does that for the load players
under one grid.
"""

import argparse
import os
import threading
import traceback
from collections.abc import Callable
from functools import partial

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
        action="append",
        required=True,
        help="Federate name matching federation config; repeat it to run several",
    )
    parser.add_argument(
        "--timeseries",
        type=str,
        action="append",
        required=True,
        help="Path to timeseries CSV file; one per --federate_name, in order",
    )
    args, _ = parser.parse_known_args()
    if len(args.federate_name) != len(args.timeseries):
        parser.error(
            f"got {len(args.federate_name)} --federate_name but "
            f"{len(args.timeseries)} --timeseries; they pair up in order"
        )
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


def _exit_on_federate_error(args: threading.ExceptHookArgs) -> None:
    """End the whole process when one federate thread fails.

    Its siblings would otherwise block forever on a time grant that waits for
    the dead federate, and the container would hang instead of failing the run.
    """
    print(f"Federate thread '{args.thread.name if args.thread else '?'}' failed:")
    traceback.print_exception(args.exc_type, args.exc_value, args.exc_traceback)
    os._exit(1)


def run_concurrently(runs: dict[str, Callable[[], None]]) -> None:
    """Run each federate on a thread of its own, named after it, and wait for all.

    Threads share one interpreter's imports instead of loading CST, HELICS and
    pandas once per player. HELICS releases the GIL while a federate waits for
    its time grant, so the others proceed.
    """
    threading.excepthook = _exit_on_federate_error
    threads = [threading.Thread(target=run, name=name) for name, run in runs.items()]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()


def main(
    scenario_name: str | None = None,
    players: dict[str, str] | None = None,
) -> None:
    """Run every load player, taking the names and CSV paths from the CLI unless given.

    ``players`` maps each federate name to the CSV it plays.
    """
    if scenario_name is None:
        args = parse_args()
        scenario_name = args.scenario
        players = dict(zip(args.federate_name, args.timeseries))

    if scenario_name is None or not players:
        raise ValueError("scenario_name and at least one load player are required")

    use_meta_db, use_data_db = get_db_backends_from_env()

    run_concurrently(
        {
            federate_name: partial(
                run_load_player,
                federate_name,
                timeseries_path,
                scenario_name,
                use_meta_db,
                use_data_db,
            )
            for federate_name, timeseries_path in players.items()
        }
    )


if __name__ == "__main__":
    main()
