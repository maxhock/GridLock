"""
Grid federate using CoSim Toolbox (CST).
Loads pandapower network, runs power flow, exchanges data via HELICS.
"""

from cosim_toolbox.sims import Federate
import helics as h

import argparse
import os
import json
from utils import PyPSANetworkBuilder


class GridFederate(Federate):
    """Grid federate with pandapower power flow simulation."""

    def __init__(self, federate_name, grid_path):
        super().__init__(federate_name)
        self.net = None
        self.pypsa_net = None  # PyPSANetworkBuilder instance
        self.ext_grid_idx = None  # Index of ext_grid in PyPSA
        self.grid_path = grid_path

    def create_federate(self):
        """Initialize HELICS federate and load pandapower network."""

        # Load config from JSON file (bypassing CST's database requirement)
        config_path = f"/config/tmp/{self.federate_name}_config.json"
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

        # Load pypsa network
        self.pypsa_net = PyPSANetworkBuilder(self.grid_path)
        self.pypsa_net.create_network()
        self.ext_grid_idx = self.pypsa_net.ext_grid_idx.split(
            ",")[0]  # Assuming single ext_grid for now

        # Register dynamic subscriptions for loads and publications for voltages at each load bus
        self.load_indices = list(self.pypsa_net.loads.index)
        for pyp_idx in self.load_indices:
            sub_key_p = f"node_{pyp_idx}/P"
            sub_key_q = f"node_{pyp_idx}/Q"
            pub_key_v = f"node_{pyp_idx}/V"
            h.helicsFederateRegisterSubscription(self.hfed, sub_key_p, "MW")
            h.helicsFederateRegisterSubscription(self.hfed, sub_key_q, "MVAR")
            h.helicsFederateRegisterGlobalPublication(
                self.hfed, pub_key_v, h.HELICS_DATA_TYPE_DOUBLE, "pu"
            )
            # Track in CST's data structures so get_data_from_federation() works
            self.inputs[sub_key_p] = {"type": "double", "key": sub_key_p}
            self.data_from_federation["inputs"][sub_key_p] = None
            self.inputs[sub_key_q] = {"type": "double", "key": sub_key_q}
            self.data_from_federation["inputs"][sub_key_q] = None
            self.pubs[pub_key_v] = {"type": "double", "key": pub_key_v}
            self.data_to_federation["publications"][pub_key_v] = None

        # Register dynamic publications for ext_grids
        # The external grid indices are actually the bus idx that is acting as a Slack
        self.ext_grid_indices = list(self.pypsa_net.ext_grid_idx)
        for pyp_idx in self.ext_grid_indices:
            pub_key_p = f"Grid/transformer_{pyp_idx}_active_power"
            pub_key_q = f"Grid/transformer_{pyp_idx}_reactive_power"
            sub_key_v = f"Grid/transformer_{pyp_idx}_voltage"
            h.helicsFederateRegisterGlobalPublication(
                self.hfed, pub_key_p, h.HELICS_DATA_TYPE_DOUBLE, "MW"
            )
            h.helicsFederateRegisterGlobalPublication(
                self.hfed, pub_key_q, h.HELICS_DATA_TYPE_DOUBLE, "MVAR"
            )
            h.helicsFederateRegisterSubscription(self.hfed, sub_key_v, "pu")
            # Track in CST's data structures so send_data_to_federation() works
            self.pubs[pub_key_p] = {"type": "double", "key": pub_key_p}
            self.data_to_federation["publications"][pub_key_p] = None
            self.pubs[pub_key_q] = {"type": "double", "key": pub_key_q}
            self.data_to_federation["publications"][pub_key_q] = None
            self.inputs[sub_key_v] = {"type": "double", "key": sub_key_v}
            self.data_from_federation["inputs"][sub_key_v] = None

        print(
            f"Grid federate initialized: {len(self.load_indices)} loads, {len(self.ext_grid_indices)} ext_grids"
        )

    def update_internal_model(self):
        """Run power flow simulation for current timestep."""
        print(f"\n=== Time: {self.granted_time} ===")

        # Read subscriptions from CST's data structure
        for pyp_idx in self.load_indices:
            sub_key_p = f"node_{pyp_idx}/P"
            sub_key_q = f"node_{pyp_idx}/Q"
            if sub_key_p in self.data_from_federation["inputs"]:
                value_w = self.data_from_federation["inputs"][sub_key_p]
                if value_w is not None:
                    self.pypsa_net.loads.loc[pyp_idx, "p_set"] = value_w
            # The following code should be uncommented when there is a published reactive power available
            # if sub_key_q in self.data_from_federation["inputs"]:
            #     value_var = self.data_from_federation["inputs"][sub_key_q]
            #     print(f"Received load {pyp_idx} q_set: {value_var} MVAR")
                # if value_var is not None:
                #     print(f"Updating load {pyp_idx} q_set to {value_var} MVAR")
                #     self.pypsa_net.loads.loc[pyp_idx, "q_set"] = value_var
        for pyp_idx in self.ext_grid_indices:
            sub_key_v = f"Grid/transformer_{pyp_idx}_voltage"
            if sub_key_v in self.data_from_federation["inputs"]:
                value_v = self.data_from_federation["inputs"][sub_key_v]
                if value_v is not None:
                    self.pypsa_net.buses.loc[pyp_idx, "v_nom"] = value_v
        try:
            self.pypsa_net.run_pf()
            print("Power flow executed.")
        except Exception as e:
            print(f"Power flow failed: {e}")
            return
        for pyp_idx in self.ext_grid_indices:
            p_mw, q_mvar = self.pypsa_net.res_ext_grid()
            self.data_to_federation["publications"][
                f"Grid/transformer_{self.ext_grid_idx}_active_power"
            ] = float(p_mw)
            print(
                f"Published ext_grid {self.ext_grid_idx} p_mw: {p_mw}")
            self.data_to_federation["publications"][
                f"Grid/transformer_{self.ext_grid_idx}_reactive_power"
            ] = float(q_mvar)
            # print(
            #     f"Published ext_grid {self.ext_grid_idx} q_mvar: {q_mvar}")

        # Publish voltage for each load node
        for pyp_idx in self.load_indices:
            v_mag = getattr(self.pypsa_net.net.buses_t, 'v_mag_pu', None)
            if v_mag is not None and not v_mag.empty:
                # Get voltage for current timestep
                bus_idx = self.pypsa_net.loads.loc[pyp_idx, "bus"]
                v_pu = self.pypsa_net.buses_t.v_mag_pu[bus_idx].iloc[0]
                self.data_to_federation["publications"][
                    f"node_{pyp_idx}/V"
                ] = float(v_pu)
                # print(f"Published node {pyp_idx} voltage: {v_pu} pu")
            else:
                print(
                    f"Warning: buses_t is empty, cannot publish voltage for node {pyp_idx}")


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
    federate = GridFederate(
        "grid", grid_path)

    try:
        federate.create_federate()
        federate.run_cosim_loop()
    finally:
        federate.destroy_federate()


if __name__ == "__main__":
    main()
