"""Starting point for a new federate class: the minimum a CST federate has to provide.

CST owns the HELICS lifecycle, the time loop and the data exchange. A new federate
subclasses `Federate`, must override `update_internal_model`, and may override
`create_federate` for setup. Copy this directory and register the class in
`composegen/load.py:map_params_to_class`.
"""

from cosim_toolbox import Federate
import argparse


class ExampleFederate(Federate):
    """Example federate: counts its steps and prints them."""

    def __init__(self, federate_name):
        """Hold whatever state the simulation needs across steps."""
        super().__init__(federate_name)
        self.custom_state = None

    def create_federate(self, scenario_name, use_meta_db=False, use_data_db=False):
        """Set up anything the simulation needs once the HELICS interfaces exist.

        The base call reads the federation config and registers this federate's
        publications and subscriptions; override to load data or add dynamic interfaces.
        """
        # Call parent to create HELICS federate from config
        super().create_federate(scenario_name, use_meta_db, use_data_db)

        # Add your custom initialization here
        # Example: Load data files, initialize simulation state, register dynamic interfaces
        self.custom_state = {"counter": 0}
        print("Custom initialization complete.")

        # Example: Dynamic subscription registration
        # import helics as h
        # dynamic_sub = h.helicsFederateRegisterSubscription(
        #     self.hfed, "dynamic/topic", "double"
        # )

    def update_internal_model(self):
        """Advance the simulation by one step - the one method a federate must provide.

        Read `self.data_from_federation`, update the model, write
        `self.data_to_federation`. Called every step once inputs have arrived.
        """
        # Example: Read from federation
        # input_value = self.data_from_federation.get("example/input", 0.0)

        # Example: Your simulation logic
        self.custom_state["counter"] += 1
        current_time = self.granted_time

        print(
            f"Time {current_time}: Running simulation logic (step {self.custom_state['counter']})"
        )

        # Example: Write to federation
        # self.data_to_federation["example/output"] = input_value * 2


def parse_args():
    """Read the scenario name and backend choices from the CLI."""
    parser = argparse.ArgumentParser(description="Example CST-based HELICS federate")
    parser.add_argument(
        "--scenario",
        type=str,
        default="example_scenario",
        help="Scenario name (used for metadata lookup if using CST databases)",
    )
    parser.add_argument(
        "--use_meta_db",
        action="store_true",
        help="Use CST metadata database instead of local config files",
    )
    parser.add_argument(
        "--use_data_db",
        action="store_true",
        help="Use CST timeseries database instead of CSV for logging",
    )
    args, unknown = parser.parse_known_args()
    return args


def main():
    """Run the federate through CST's lifecycle: create, loop to stop time, destroy."""
    args = parse_args()

    # Step 1: Create federate instance
    federate = ExampleFederate("example")

    try:
        # Step 2: Initialize federate (reads config, creates HELICS federate)
        federate.create_federate(
            scenario_name=args.scenario,
            use_meta_db=args.use_meta_db,
            use_data_db=args.use_data_db,
        )

        # Step 3: Run co-simulation loop
        # CST handles:
        # - Time requests and grants
        # - Calling update_internal_model() each timestep
        # - Data exchange with federation
        federate.run_cosim_loop()

    except Exception as e:
        print(f"Error during federate execution: {e}")
        raise
    finally:
        # Step 4: Clean up
        federate.destroy_federate()
        print("Federate execution complete.")


if __name__ == "__main__":
    main()
