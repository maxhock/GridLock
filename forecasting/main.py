"""
Forecasting federate using CoSim Toolbox (CST).
Collects house load data, calls external FastAPI for forecasting, publishes results via HELICS.
"""

from cosim_toolbox.sims import Federate
import helics as h
import argparse
import os
import pandas as pd
import json
from typing import List
from forecasting import ForecastInput, predict_agg_load


class ForecastingFederate(Federate):
    """Forecasting federate that calls external FastAPI for load predictions."""

    def __init__(self, federate_name, grid_path):
        super().__init__(federate_name)
        self.net = None
        self.load_indices = []  # List of house indices to subscribe to
        self.current_loads = []  # Current load values
        self.grid_path = grid_path

    def create_federate(self):
        """Initialize HELICS federate and register subscriptions/publications."""

        # Load config from JSON file (same pattern as grid federate)
        config_path = f"/config/tmp/{self.federate_name}_config.json"
        with open(config_path, "r") as f:
            self.config = json.load(f)

        print(f"📊 Loaded config from {config_path}")

        # Initialize CST's required attributes from config
        self.scenario_name = self.config.get("name", "forecasting")
        self.federate_type = "value"
        self.period = self.config.get("period", 3600.0)
        self.stop_time = self.config.get("max_cosim_duration", 82800.0)
        self.granted_time = 0.0

        # Set scenario timeframe
        self.scenario = {}
        self.scenario["start_time"] = "2025-01-01T00:00:00"
        self.scenario["stop_time"] = "2025-01-02T00:00:00"
        self.set_metadata()

        print(f"📊 Using period: {self.period}s, stop time: {self.stop_time}s")

        # Initialize CST's data exchange dictionaries
        self.pubs = {}
        self.inputs = {}
        self.data_from_federation = {"inputs": {}, "endpoints": {}}
        self.data_to_federation = {"publications": {}, "endpoints": {}}

        # Use CST's create_helics_fed() method
        self.create_helics_fed()

        load_df = pd.read_excel(self.grid_path, sheet_name="load")

        # Get load indices from the dataframe index
        self.load_indices = list(load_df.index)
        num_houses = len(self.load_indices)

        print(f"📊 Read {num_houses} houses from grid file: {self.grid_path}")

        # Register dynamic subscriptions for house loads based on grid file
        for house_idx in self.load_indices:
            sub_key = f"house_{house_idx}/house_load"
            h.helicsFederateRegisterSubscription(self.hfed, sub_key, "double")
            # Track in CST's data structures
            self.inputs[sub_key] = {"type": "double", "key": sub_key}
            self.data_from_federation["inputs"][sub_key] = None

        # Register publication for total load forecast
        pub_key = "Forecaster/total_load_forecast"
        h.helicsFederateRegisterPublication(
            self.hfed, pub_key, h.HELICS_DATA_TYPE_DOUBLE, "MW"
        )
        # Track in CST's data structures
        self.pubs[pub_key] = {"type": "double", "key": pub_key}
        self.data_to_federation["publications"][pub_key] = None

        print(
            f"✅ Forecasting federate '{self.scenario_name}' initialized: subscribing to {len(self.load_indices)} houses"
        )
        print("📡 Registered forecasting publication: Forecaster/total_load_forecast")

    def update_internal_model(self):
        """Collect house loads, call FastAPI for forecasting, prepare publication."""
        print(f"\n=== FORECASTING Time: {self.granted_time} ===")

        # Collect current loads from CST's data structure
        self.current_loads = []
        for house_idx in self.load_indices:
            sub_key = f"house_{house_idx}/house_load"
            if sub_key in self.data_from_federation["inputs"]:
                value = self.data_from_federation["inputs"][sub_key]
                if value is not None:
                    # Convert from W to MW (assuming houses publish in W)
                    load_mw = value / 1000
                    self.current_loads.append(load_mw)
                    print(
                        f"Set load {house_idx} p_mw to {load_mw} from HELICS subscription."
                    )
                else:
                    # Use default value if no data received
                    self.current_loads.append(0.0003)  # Default 300W

        # Only proceed if we have load data
        if self.current_loads:
            # Create forecast input
            forecast_input = ForecastInput(
                current_loads=self.current_loads,
                current_time=self.granted_time,
                time_step=self.period,
            )

            # Call external FastAPI for forecasting
            try:
                forecast_output = predict_agg_load(forecast_input)

                # Store result in CST's data structure for publication
                self.data_to_federation["publications"][
                    "Forecaster/total_load_forecast"
                ] = forecast_output.total_load_forecast

                print(
                    f"📤 Publishing total load forecast: {forecast_output.total_load_forecast:.6f} MW"
                )

            except Exception as e:
                print(f"❌ Forecasting failed: {e}")
                # Publish fallback value (sum of current loads)
                fallback_forecast = sum(self.current_loads)
                self.data_to_federation["publications"][
                    "Forecaster/total_load_forecast"
                ] = fallback_forecast
                print(f"📤 Publishing fallback forecast: {fallback_forecast:.6f} MW")
        else:
            # No load data received, publish zero
            self.data_to_federation["publications"][
                "Forecaster/total_load_forecast"
            ] = 0.0
            print("📤 No load data received, publishing 0.0 MW forecast")


def parse_args():
    parser = argparse.ArgumentParser(description="Forecasting federate using CST")
    parser.add_argument(
        "--grid_file",
        type=str,
        required=True,
        help="Path to Excel file (relative to /data/input)",
    )
    parser.add_argument(
        "--api_host",
        type=str,
        default="fastapi-server",
        help="FastAPI server host (default: fastapi-server)",
    )
    parser.add_argument(
        "--api_port", type=int, default=8000, help="FastAPI server port (default: 8000)"
    )
    args, _ = parser.parse_known_args()
    return args


def main():
    args = parse_args()

    # Set environment variables for API configuration
    os.environ["FASTAPI_HOST"] = args.api_host
    os.environ["FASTAPI_PORT"] = str(args.api_port)

    grid_path = os.path.join("/data", "input", args.grid_file)

    federate = ForecastingFederate("forecasting", grid_path)

    try:
        federate.create_federate()
        federate.run_cosim_loop()
    finally:
        federate.destroy_federate()


if __name__ == "__main__":
    main()
