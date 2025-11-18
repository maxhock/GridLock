"""
Grid federate using CoSim Toolbox (CST) with PyPSA.
Loads pandapower network from Excel, converts to PyPSA, runs power flow, exchanges data via HELICS.
"""

from cosim_toolbox.sims import Federate
import helics as h
import pypsa
import pandapower as pp
import pandas as pd
import argparse
import os
import json
import math


class GridPyPSAFederate(Federate):
    """Grid federate with PyPSA power flow simulation."""

    def __init__(self, federate_name, grid_path):
        super().__init__(federate_name)
        self.network = None
        self.load_indices = []  # List of load indices
        self.ext_grid_indices = []  # List of external grid indices
        self.grid_path = grid_path

    def create_federate(self):
        """Initialize HELICS federate and load PyPSA network."""

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

        # Load PyPSA network from pandapower Excel file
        self.network = load_pypsa_from_pandapower_excel(self.grid_path)

        # Register dynamic subscriptions for loads
        self.load_indices = list(self.network.loads.index)
        for idx in self.load_indices:
            sub_key = f"node_{idx}/P"
            h.helicsFederateRegisterSubscription(self.hfed, sub_key, "MW")
            # Track in CST's data structures so get_data_from_federation() works
            self.inputs[sub_key] = {"type": "double", "key": sub_key}
            self.data_from_federation["inputs"][sub_key] = None

        # Register dynamic publications for generators (equivalent to ext_grids)
        self.ext_grid_indices = list(self.network.generators.index)
        for idx in self.ext_grid_indices:
            pub_key = f"Grid/transformer_{idx}_power"
            h.helicsFederateRegisterGlobalPublication(
                self.hfed, pub_key, h.HELICS_DATA_TYPE_DOUBLE, "MW"
            )
            # Track in CST's data structures so send_data_to_federation() works
            self.pubs[pub_key] = {"type": "double", "key": pub_key}
            self.data_to_federation["publications"][pub_key] = None

        print(
            f"Grid PyPSA federate initialized: {len(self.load_indices)} loads, {len(self.ext_grid_indices)} generators"
        )

    def update_internal_model(self):
        """Run power flow simulation for current timestep."""
        print(f"\n=== Time: {self.granted_time} ===")

        # Read subscriptions from CST's data structure
        for idx in self.load_indices:
            sub_key = f"node_{idx}/P"
            if sub_key in self.data_from_federation["inputs"]:
                value_w = self.data_from_federation["inputs"][sub_key]
                if value_w is not None:
                    self.network.loads.at[idx, "p_set"] = value_w

        try:
            self.network.pf()
            print("Power flow executed.")
        except Exception as e:
            print(f"Power flow failed: {e}")
            return

        # Write publications to CST's data structure
        # For slack generators, get power from bus injection
        for idx in self.ext_grid_indices:
            # Get the bus where this generator is connected
            gen_bus = self.network.generators.at[idx, "bus"]
            # Get power injection at that bus (positive = generation)
            p_mw = self.network.buses_t.p[gen_bus].iloc[-1]
            self.data_to_federation["publications"][f"Grid/transformer_{idx}_power"] = (
                float(p_mw)
            )
            print(f"Published generator {idx} p_mw: {p_mw}")


def load_pypsa_from_pandapower_excel(excel_path: str) -> pypsa.Network:
    """
    Load a pandapower network from Excel and convert to PyPSA network.

    This function loads a pandapower network from an Excel file and then
    converts it to PyPSA format. It uses pandapower's from_excel() to load
    the network, then manually transfers components to PyPSA.

    Note: PyPSA's built-in import_from_pandapower_net() has issues with
    standard line/transformer types in some networks, so we use a custom
    conversion approach.

    Args:
        excel_path: Path to Excel file with pandapower format

    Returns:
        PyPSA Network object
    """
    # Load pandapower network from Excel
    pp_net = pp.from_excel(excel_path)

    # Create PyPSA network
    network = pypsa.Network()

    # Add buses to PyPSA network
    for idx, row in pp_net.bus.iterrows():
        network.add(
            "Bus",
            name=str(idx),
            v_nom=row.get("vn_kv", 1.0),
        )

    # Add loads to PyPSA network
    for idx, row in pp_net.load.iterrows():
        network.add(
            "Load",
            name=str(idx),
            bus=str(row["bus"]),
            p_set=row.get("p_mw", 0.0),
        )

    # Add external grids as generators in PyPSA
    for idx, row in pp_net.ext_grid.iterrows():
        network.add(
            "Generator",
            name=str(idx),
            bus=str(row["bus"]),
            p_nom=1000.0,  # Large capacity
            control="Slack",
        )

    # Add lines to PyPSA network
    SQRT_3 = math.sqrt(3)  # For three-phase apparent power calculation
    for idx, row in pp_net.line.iterrows():
        from_bus = row["from_bus"]
        # Get bus voltage with error handling
        if from_bus in pp_net.bus.index:
            v_nom = pp_net.bus.at[from_bus, "vn_kv"]
        else:
            v_nom = 1.0  # Default if bus not found

        network.add(
            "Line",
            name=str(idx),
            bus0=str(from_bus),
            bus1=str(row["to_bus"]),
            r=row.get("r_ohm_per_km", 0.0) * row.get("length_km", 1.0),
            x=row.get("x_ohm_per_km", 0.0) * row.get("length_km", 1.0),
            s_nom=row.get("max_i_ka", 1.0) * row.get("df", 1.0) * v_nom * SQRT_3,
        )

    # Add transformers if they exist
    if len(pp_net.trafo) > 0:
        for idx, row in pp_net.trafo.iterrows():
            network.add(
                "Transformer",
                name=str(idx),
                bus0=str(row["hv_bus"]),
                bus1=str(row["lv_bus"]),
                s_nom=row.get("sn_mva", 1.0),
                x=row.get("vk_percent", 5.0) / 100.0,
                r=row.get("vkr_percent", 0.5) / 100.0,
            )

    # Set a snapshot for the power flow
    network.set_snapshots([pd.Timestamp("2025-01-01")])

    return network


def parse_args():
    parser = argparse.ArgumentParser(description="Grid federate using CST with PyPSA")
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
    federate = GridPyPSAFederate("grid", grid_path)

    try:
        federate.create_federate()
        federate.run_cosim_loop()
    finally:
        federate.destroy_federate()


if __name__ == "__main__":
    main()
