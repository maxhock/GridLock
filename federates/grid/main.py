"""
Grid federate using CoSim Toolbox (CST).
Loads pandapower network, runs power flow, exchanges data via HELICS.
"""

from cosim_toolbox.sims import Federate
from cosim_toolbox.sims import FederationConfig, FederateConfig
from cosim_toolbox.dbms import create_metadata_manager
from cosim_toolbox.dbms import create_timeseries_manager
import helics as h
import pandapower as pp
import argparse
import os
import json
import shutil
from pathlib import Path

class GridFederate(Federate):
    """Grid federate with pandapower power flow simulation."""

    def __init__(self, federate_name, grid_path):
        super().__init__(federate_name)
        self.net = None
        self.load_indices = []  # List of pandapower load indices
        self.ext_grid_indices = []  # List of pandapower ext_grid indices
        self.grid_path = grid_path

    def load_pp_net(self):

        self.net = pp.from_excel(self.grid_path)

        # Register dynamic subscriptions for loads
        self.load_indices = list(self.net.load.index)
        for pp_idx in self.load_indices:
            sub_key = f"node_{pp_idx}/P"
            h.helicsFederateRegisterSubscription(self.hfed, sub_key, "MW")
            # Track in CST's data structures so get_data_from_federation() works
            self.inputs[sub_key] = {"type": "double", "key": sub_key}
            self.data_from_federation["inputs"][sub_key] = None

        # Register dynamic publications for ext_grids
        self.ext_grid_indices = list(self.net.ext_grid.index)
        for pp_idx in self.ext_grid_indices:
            pub_key = f"Grid/transformer_{pp_idx}_power"
            h.helicsFederateRegisterGlobalPublication(
                self.hfed, pub_key, h.HELICS_DATA_TYPE_DOUBLE, "MW"
            )
            # Track in CST's data structures so send_data_to_federation() works
            self.pubs[pub_key] = {"type": "double", "key": pub_key}
            self.data_to_federation["publications"][pub_key] = None

        print(
            f"Grid federate initialized: {len(self.load_indices)} loads, {len(self.ext_grid_indices)} ext_grids"
        )

    def update_internal_model(self):
        """Run power flow simulation for current timestep."""
        print(f"\n=== Time: {self.granted_time} ===")

        # Read subscriptions from CST's data structure
        for pp_idx in self.load_indices:
            sub_key = f"node_{pp_idx}/P"
            if sub_key in self.data_from_federation["inputs"]:
                value_w = self.data_from_federation["inputs"][sub_key]
                if value_w is not None:
                    self.net.load.at[pp_idx, "p_mw"] = value_w
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
    grid_path = os.path.join("/data", "input", args.grid_file)
    # Copy meta_store configuration into working directory
    # shutil.copytree("../config/composegen/meta_store", Path.cwd() / "meta_store", dirs_exist_ok=True)
    # Initialize federate with entrypoint federate
    federate = GridFederate("lv-grid_0", grid_path)

    try:
        # Create CST and HELICS federates
        federate.create_federate(scenario_name="TestGridScenario", use_meta_db="json", use_data_db="csv")
        federate.load_pp_net()
        federate.run_cosim_loop()
    finally:
        federate.destroy_federate()


if __name__ == "__main__":
    main()
