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

    This function uses pandapower's from_excel() to load the network and then
    PyPSA's built-in import_from_pandapower_net() to convert it.

    Args:
        excel_path: Path to Excel file with pandapower format

    Returns:
        PyPSA Network object
    """
    # Load pandapower network from Excel
    pp_net = pp.from_excel(excel_path)

    # Assign unique names to components that have None as name
    # This prevents duplicate name errors in PyPSA
    for component_type in ["load", "bus", "line", "trafo", "ext_grid"]:
        if hasattr(pp_net, component_type):
            component = getattr(pp_net, component_type)
            if "name" in component.columns:
                # Replace None/empty with index-based names
                component["name"] = component["name"].fillna("").astype(str)
                component.loc[component["name"] == "", "name"] = component.index.astype(
                    str
                )

    # Create PyPSA network and import from pandapower
    # Use extra_line_data=True to import actual parameter values for lines
    network = pypsa.Network()
    network.import_from_pandapower_net(
        pp_net, extra_line_data=True, use_pandapower_index=True
    )

    # Clear type columns in PyPSA network to prevent type lookup errors during power flow
    # The parameter values are already imported, so we just need to clear the type references
    if "type" in network.lines.columns:
        network.lines["type"] = ""
    if "type" in network.transformers.columns:
        network.transformers["type"] = ""

    # Fix transformer parameters - PyPSA's import doesn't handle all transformer parameters correctly
    # We need to manually copy the transformer impedances from pandapower
    if len(network.transformers) > 0 and len(pp_net.trafo) > 0:
        for pp_idx, row in pp_net.trafo.iterrows():
            # PyPSA may name transformers differently - try to find the corresponding one
            # Usually it's "trafo {idx}" but the index may be off by 1
            pypsa_name = None
            for name in network.transformers.index:
                if str(pp_idx) in name or f"{pp_idx+1}" in name:
                    pypsa_name = name
                    break

            if pypsa_name is None and len(network.transformers) == 1:
                # If there's only one transformer, use it
                pypsa_name = network.transformers.index[0]

            if pypsa_name:
                # Get s_nom from pandapower
                s_nom_mva = row.get("sn_mva", 0.16)
                network.transformers.at[pypsa_name, "s_nom"] = s_nom_mva

                # Calculate impedances from pandapower's vk and vkr percentages
                vk_percent = row.get("vk_percent", 4.0)
                vkr_percent = row.get("vkr_percent", 1.2)

                # Convert to per-unit impedances
                # In PyPSA, transformer impedances are in per-unit on the transformer base
                network.transformers.at[pypsa_name, "x"] = vk_percent / 100.0
                network.transformers.at[pypsa_name, "r"] = vkr_percent / 100.0

    # Fix slack generator capacity - ext_grids in pandapower have infinite capacity
    # but PyPSA needs a finite value. Set to a large value (1000 MW)
    for idx in network.generators.index:
        if network.generators.at[idx, "control"] == "Slack":
            network.generators.at[idx, "p_nom"] = 1000.0
            # Also ensure the slack bus has correct control
            slack_bus = network.generators.at[idx, "bus"]
            network.buses.at[slack_bus, "control"] = "Slack"

    # Set a snapshot for the power flow
    network.set_snapshots([pd.Timestamp("2025-01-01")])

    return network

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
