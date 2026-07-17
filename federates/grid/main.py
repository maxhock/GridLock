"""Grid federate entry point.

Loads a pandapower net from the CST metadata store and runs a
HELICS co-simulation loop exchanging load power / bus voltage data.

Usage:
    python main.py --scenario TestGridScenario --federate_name lv-grid_91301_0
"""

import argparse
import os
import re
from typing import Any, cast

import pandapower as pp
from cosim_toolbox.sims import Federate
from cosim_toolbox.dbms import create_metadata_manager


# ---------------------------------------------------------------------------
# GridFederate
# ---------------------------------------------------------------------------

_LOAD_RE = re.compile(r"/load_(\d+)/")

_LOAD_COLUMN_DEFAULTS: dict[str, Any] = {
    "const_z_p_percent": 0.0,
    "const_i_p_percent": 0.0,
    "const_z_q_percent": 0.0,
    "const_i_q_percent": 0.0,
    "scaling": 1.0,
    "in_service": True,
}


def _parse_load_index(key: str) -> int | None:
    """Extract pandapower load index from a HELICS key like ``…/load_3/active_power``."""
    m = _LOAD_RE.search(key)
    return int(m.group(1)) if m else None


def sanitize_net_for_power_flow(net: pp.pandapowerNet) -> pp.pandapowerNet:
    """Prepare an imported pandapower net for HELICS-driven co-simulation.

    Older or externally generated pandapower JSON payloads may omit ZIP-load
    columns that newer pandapower releases expect during ``runpp``. Those
    columns describe the static load model split and can safely default to zero.

    Grid demand in this project is driven by HELICS load-player federates, so
    any static load powers imported with the raw grid are cleared here. The
    federates then write the active/reactive power values for their assigned
    load indices before every power-flow step.
    """
    if not hasattr(net, "load") or net.load is None or net.load.empty:
        return net

    added_columns: list[str] = []
    for column_name, default_value in _LOAD_COLUMN_DEFAULTS.items():
        if column_name in net.load.columns:
            continue
        net.load[column_name] = default_value
        added_columns.append(column_name)

    net.load["p_mw"] = 0.0
    net.load["q_mvar"] = 0.0

    if added_columns:
        print(
            "Sanitized net.load by adding columns: "
            + ", ".join(added_columns)
        )
    print("Reset imported net.load active/reactive powers to zero for co-simulation.")

    return net


class GridFederate(Federate):
    """Pandapower grid federate. Overrides only ``update_internal_model``."""

    def __init__(self, federate_name: str, net: pp.pandapowerNet) -> None:
        super().__init__(federate_name)
        self.net = net

    def update_internal_model(self) -> None:
        # 1. Apply received load values (filter out HELICS sentinel -1e+49)
        for key, value in self.data_from_federation.get("inputs", {}).items():
            idx = _parse_load_index(key)
            if value is None or idx is None:
                continue
            # Skip HELICS sentinel values (no data received yet)
            if abs(value) > 1e40:
                continue
            if key.endswith("/active_power"):
                self.net.load.at[idx, "p_mw"] = float(value) / 1e6
            elif key.endswith("/reactive_power"):
                self.net.load.at[idx, "q_mvar"] = float(value) / 1e6

        # 2. Run power flow
        try:
            pp.runpp(self.net, numba=True)
            print(f"Power flow converged at time {self.granted_time}.")
        except Exception as e:
            print(f"Power flow failed at time {self.granted_time}: {e}")
            return

        # 3. Publish per-load bus voltages
        for key in self.data_to_federation.get("publications", {}):
            idx = _parse_load_index(key)
            if idx is None or not key.endswith("/voltage"):
                continue
            bus = int(self.net.load.at[idx, "bus"])
            v_pu = float(self.net.res_bus.at[bus, "vm_pu"])
            self.data_to_federation["publications"][key] = v_pu

    def on_enter_executing_mode(self) -> None:
        """Run initial power flow at t=0 with load values published by load players."""
        self.get_data_from_federation()
        self.update_internal_model()
        self.send_data_to_federation()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments.

    Returns:
        Namespace with scenario and federate_name.
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


def load_net_from_metadata(
    federate_name: str,
    use_meta_db: str = "json",
) -> pp.pandapowerNet:
    """Load a pandapower net from the CST metadata store.

    The infdb data setup container writes each grid's JSON
    representation into the "custom_metadata" collection, keyed
    by the federate name.

    Args:
        federate_name: Name of the federate (matches metadata key).

    Returns:
        pandapower network object.

    Raises:
        FileNotFoundError: If no custom_metadata entry exists for this name.
    """
    md_kwargs = {"backend": use_meta_db}
    if use_meta_db == "json":
        md_kwargs["location"] = "meta_store"

    md_mgr = create_metadata_manager(**md_kwargs)
    md_mgr.connect()
    try:
        data = md_mgr.read("custom_metadata", federate_name)
    finally:
        md_mgr.disconnect()

    if not data:
        raise FileNotFoundError(
            f"No grid data found in metadata store for '{federate_name}'. "
            f"Ensure infdb data setup has run."
        )

    net_json = data.get("net_json")
    if not net_json:
        raise ValueError(f"Grid metadata for '{federate_name}' has no net_json.")
    net = cast(pp.pandapowerNet, pp.from_json_string(net_json))
    sanitize_net_for_power_flow(net)
    print(f"Loaded net for {federate_name}: {len(net.bus)} buses, {len(net.load)} loads")
    return net


def run_grid_federate(
    federate_name: str,
    net: pp.pandapowerNet,
    scenario_name: str,
    use_meta_db: str,
    use_data_db: str,
) -> None:
    """Run a single GridFederate lifecycle.

    Uses CST's built-in ``run()`` which calls
    ``create_federate`` → ``run_cosim_loop`` → ``destroy_federate``.

    Args:
        federate_name: Unique HELICS federate name.
        net: pandapower network object for this grid.
        scenario_name: CST scenario name to look up in meta_store.
        use_meta_db: Metadata backend type.
        use_data_db: Data backend type.
    """
    federate = GridFederate(federate_name, net)
    federate.run(
        scenario_name,
        use_meta_db=use_meta_db,
        use_data_db=use_data_db,
    )


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

    if scenario_name is None or federate_name is None:
        raise ValueError("scenario_name and federate_name are required")

    use_meta_db, use_data_db = get_db_backends_from_env()

    net = load_net_from_metadata(federate_name, use_meta_db)
    run_grid_federate(
        federate_name,
        net,
        scenario_name,
        use_meta_db,
        use_data_db,
    )


if __name__ == "__main__":
    main()
