"""
Grid federate using CoSim Toolbox (CST).
Loads pandapower network, runs power flow, exchanges data via HELICS.
"""

from cosim_toolbox.sims import Federate
import argparse
import helics as h
import json
import math
import os

import pandapower as pp


class GridFederate(Federate):
    """Grid federate with pandapower power flow simulation."""

    def __init__(self, federate_name, grid_path):
        super().__init__(federate_name)
        self.net = None
        self.load_indices = []  # List of pandapower load indices
        self.ext_grid_indices = []  # List of pandapower ext_grid indices
        self.grid_path = grid_path

    def create_federate(self):
        """Initialize HELICS federate and load pandapower network."""

        # Load config from JSON file (bypassing CST's database requirement)
        config_path = f"/config/tmp/{self.federate_name}_config.json"  # For Docker

        with open(config_path, "r") as f:
            self.config = json.load(f)

        # Initialize CST's required attributes
        self.scenario_name = "grid"
        self.federate_type = "value"
        self.period = self.config.get("period", 3600.0)
        self.stop_time = self.config.get("max_cosim_duration", 82800.0)
        self.granted_time = 0.0

        self.scenario = {}
        self.scenario["start_time"] = "2025-01-01T00:00:00"
        self.scenario["stop_time"] = "2025-01-02T00:00:00"
        self.set_metadata()

        # Initialize CST's data exchange dictionaries
        self.pubs = {}
        self.inputs = {}
        self.data_from_federation = {"inputs": {}, "endpoints": {}}
        self.data_to_federation = {"publications": {}, "endpoints": {}}

        # Use CST's create_helics_fed() method - it reads from self.config
        self.create_helics_fed()

        # Load pandapower network
        self.net = pp.from_excel(self.grid_path)

        # Register dynamic subscriptions for loads
        self.load_indices = list(self.net.load.index)
        for pp_idx in self.load_indices:
            sub_key = f"node_{pp_idx}/P"
            h.helicsFederateRegisterSubscription(self.hfed, sub_key, "MW")
            self.inputs[sub_key] = {"type": "double", "key": sub_key}
            self.data_from_federation["inputs"][sub_key] = None

        # Register dynamic publications for ext_grids
        self.ext_grid_indices = list(self.net.ext_grid.index)
        for pp_idx in self.ext_grid_indices:
            pub_key = f"Grid/transformer_{pp_idx}_power"
            h.helicsFederateRegisterGlobalPublication(
                self.hfed, pub_key, h.HELICS_DATA_TYPE_DOUBLE, "MW"
            )
            self.pubs[pub_key] = {"type": "double", "key": pub_key}
            self.data_to_federation["publications"][pub_key] = None

        print(
            f"Grid federate initialized: {len(self.load_indices)} loads, {len(self.ext_grid_indices)} ext_grids"
        )

    def update_internal_model(self):
        """Run power flow simulation for current timestep."""
        print(f"\n=== Time: {self.granted_time} ===")

        for pp_idx in self.load_indices:
            sub_key = f"node_{pp_idx}/P"
            value_mw = self.data_from_federation["inputs"].get(sub_key)
            if value_mw is None or isinstance(value_mw, list):
                continue

            # HELICS can return an invalid default (e.g. -1e49) for not-connected inputs.
            if (not math.isfinite(value_mw)) or abs(value_mw) > 1e6:
                continue

            print(f"Grid received: {sub_key} = {value_mw} MW")
            self.net.load.at[pp_idx, "p_mw"] = value_mw
        try:
            pp.runpp(self.net, numba=False)
            print("Power flow executed.")
        except Exception as e:
            print(f"Power flow failed: {e}")
            return

        # Write publications to CST's data structure
        for pp_idx in self.ext_grid_indices:
            p_mw = self.net.res_ext_grid.at[pp_idx, "p_mw"]
            self.data_to_federation["publications"][
                f"Grid/transformer_{pp_idx}_power"
            ] = float(p_mw)
            print(f"Published ext_grid {pp_idx} p_mw: {p_mw}")


def parse_args():
    parser = argparse.ArgumentParser(description="Grid federate using CST")
    parser.add_argument(
        "--grid_file",
        type=str,
        required=True,
        help="Path to Excel file (relative to /data/input)",
    )
    args, _ = parser.parse_known_args()
    return args


def main():
    args = parse_args()
    # For Docker:
    grid_path = os.path.join("/data", "input", args.grid_file)
    # For testing without Docker:
    #repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    #grid_path = os.path.join(repo_root, "data", "input", args.grid_file)
    federate = GridFederate("grid", grid_path)

    try:
        federate.create_federate()
        federate.run_cosim_loop()
    finally:
        federate.destroy_federate()


if __name__ == "__main__":
    main()
