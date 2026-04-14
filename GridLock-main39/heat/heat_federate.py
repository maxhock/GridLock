#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Mar 25 12:27:18 2026

@author: sebastianbloesch
"""

#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Heat federate using CoSim Toolbox (CST) with pandapipes.
Based on simple 3-junction example network.
"""

from cosim_toolbox.sims import Federate
import helics as h
import pandapipes as pp
import json


class HeatFederate(Federate):
    """Heat network federate using pandapipes."""

    def __init__(self, federate_name):
        super().__init__(federate_name)
        self.net = None
        self.sink_idx = None
        self.ext_grid_idx = None

    def create_federate(self):
        """Initialize HELICS federate and pandapipes network."""

        # Load HELICS config
        config_path = f"/config/tmp/{self.federate_name}_config.json"
        with open(config_path, "r") as f:
            self.config = json.load(f)

        # Required CST attributes
        self.scenario_name = "heat"
        self.federate_type = "value"
        self.period = self.config.get("period", 3600.0)
        self.stop_time = self.config.get("max_cosim_duration", 82800.0)
        self.granted_time = 0.0

        self.scenario = {}
        self.scenario["start_time"] = "2025-01-01T00:00:00"
        self.scenario["stop_time"] = "2025-01-02T00:00:00"
        self.set_metadata()

        # Data exchange structures
        self.pubs = {}
        self.inputs = {}
        self.data_from_federation = {"inputs": {}, "endpoints": {}}
        self.data_to_federation = {"publications": {}, "endpoints": {}}

        # Create HELICS federate
        self.create_helics_fed()

        # -------------------------
        # Build pandapipes network
        # -------------------------
        self.net = pp.create_empty_network(fluid="water")

        # Junctions
        j1 = pp.create_junction(self.net, pn_bar=1.05, tfluid_k=293.15, name="J1")
        j2 = pp.create_junction(self.net, pn_bar=1.05, tfluid_k=293.15, name="J2")
        j3 = pp.create_junction(self.net, pn_bar=1.05, tfluid_k=293.15, name="J3")

        # External grid
        self.ext_grid_idx = pp.create_ext_grid(
            self.net, junction=j1, p_bar=1.1, t_k=293.15, name="Grid"
        )

        # Sink (will be controlled dynamically)
        self.sink_idx = pp.create_sink(
            self.net, junction=j3, mdot_kg_per_s=0.045, name="Sink"
        )

        # Pipe
        pp.create_pipe_from_parameters(
            self.net,
            from_junction=j1,
            to_junction=j2,
            length_km=0.1,
            diameter_m=0.05,
            name="Pipe",
        )

        # Valve
        #pp.create_valve(
        #    self.net,
        #    junction=j2,
        #    element=j3,
        #    et="j",
        #    diameter_m=0.05,
        #    opened=True,
        #    name="Valve",
        #)

        # -------------------------
        # HELICS subscriptions
        # -------------------------
        sub_key = "house/heat_demand"
        h.helicsFederateRegisterSubscription(self.hfed, sub_key, "kW")

        self.inputs[sub_key] = {"type": "double", "key": sub_key}
        self.data_from_federation["inputs"][sub_key] = None

        # -------------------------
        # HELICS publications
        # -------------------------
        pub_key = "heat/massflow"
        h.helicsFederateRegisterGlobalPublication(
            self.hfed, pub_key, h.HELICS_DATA_TYPE_DOUBLE, "kg/s"
        )

        self.pubs[pub_key] = {"type": "double", "key": pub_key}
        self.data_to_federation["publications"][pub_key] = None

        print("Heat federate initialized.")

    def update_internal_model(self):
        """Run pipeflow simulation."""

        print(f"\n=== Time: {self.granted_time} ===")

        # -------------------------
        # Read input (heat demand)
        # -------------------------
        heat_kw = self.data_from_federation["inputs"].get("house/heat_demand")

        if heat_kw is not None:
            # Convert kW → kg/s
            cp = 4180  # J/(kg*K)
            delta_T = 20  # K
            mdot = (heat_kw * 1000) / (cp * delta_T)

            self.net.sink.at[self.sink_idx, "mdot_kg_per_s"] = mdot

            print(f"Heat demand: {heat_kw} kW → {mdot:.4f} kg/s")

        # -------------------------
        # Run simulation
        # -------------------------
        try:
            pp.pipeflow(self.net)
            print("Pipeflow executed.")
        except Exception as e:
            print(f"Pipeflow failed: {e}")
            return

        # -------------------------
        # Publish result
        # -------------------------
        mdot_res = self.net.res_ext_grid.at[self.ext_grid_idx, "mdot_kg_per_s"]

        self.data_to_federation["publications"]["heat/massflow"] = float(mdot_res)

        print(f"Published massflow: {mdot_res:.4f} kg/s")


def main():
    federate = HeatFederate("heat")

    try:
        federate.create_federate()
        federate.run_cosim_loop()
    finally:
        federate.destroy_federate()


if __name__ == "__main__":
    main()