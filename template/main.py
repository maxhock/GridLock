"""
Template for creating a HELICS federate using CoSim Toolbox (CST).

This template demonstrates the minimal structure for a CST-based federate:
1. Subclass cosim_toolbox.Federate
2. Override create_federate() for custom initialization (optional)
3. Override update_internal_model() for your simulation logic (required)
4. Call create_federate(), run_cosim_loop(), destroy_federate() in main()

CST handles:
- HELICS federate lifecycle (create/destroy)
- Time loop management and time requests
- Data exchange via data_from_federation/data_to_federation dicts

You customize:
- Domain-specific simulation logic in update_internal_model()
- Any dynamic HELICS interface registration in create_federate()
"""

from cosim_toolbox import Federate
import argparse


class ExampleFederate(Federate):
    """
    Example federate showing CST Federate class usage.
    
    Attributes:
        custom_state: Example of custom state for your simulation
    """
    
    def __init__(self, federate_name):
        """
        Initialize the federate.
        
        Args:
            federate_name: Name of this federate in the federation
        """
        super().__init__(federate_name)
        self.custom_state = None
        
    def create_federate(self, scenario_name, use_meta_db=False, use_data_db=False):
        """
        Override this method for custom initialization.
        
        CST's base create_federate():
        - Reads HELICS config from grid_config.json (or metadata DB)
        - Creates HELICS federate object (self.hfed)
        - Registers static publications/subscriptions from config
        
        Override to add:
        - Custom initialization logic
        - Dynamic interface registration (subscriptions/publications)
        - Load simulation-specific data files
        
        Args:
            scenario_name: Scenario name for metadata lookup
            use_meta_db: Use CST metadata database (default: False, uses local config)
            use_data_db: Use CST timeseries database for logging (default: False, uses CSV)
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
        """
        REQUIRED: Override this method with your simulation logic.
        
        This is called every simulation timestep after data is received.
        
        Workflow:
        1. Read inputs from self.data_from_federation dict
           - Keys are subscription/input names from HELICS config
           - Values are the received data
        
        2. Run your simulation logic
           - Update internal model state
           - Perform calculations, solve equations, etc.
        
        3. Write outputs to self.data_to_federation dict
           - Keys are publication/endpoint names from HELICS config
           - Values are the data to send
        
        Example:
            input_value = self.data_from_federation.get("input_topic", 0.0)
            result = input_value * 2  # Your logic here
            self.data_to_federation["output_topic"] = result
        """
        # Example: Read from federation
        # input_value = self.data_from_federation.get("example/input", 0.0)
        
        # Example: Your simulation logic
        self.custom_state["counter"] += 1
        current_time = self.granted_time
        
        print(f"Time {current_time}: Running simulation logic (step {self.custom_state['counter']})")
        
        # Example: Write to federation
        # self.data_to_federation["example/output"] = input_value * 2


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Example CST-based HELICS federate"
    )
    parser.add_argument(
        "--scenario", 
        type=str, 
        default="example_scenario",
        help="Scenario name (used for metadata lookup if using CST databases)"
    )
    parser.add_argument(
        "--use_meta_db",
        action="store_true",
        help="Use CST metadata database instead of local config files"
    )
    parser.add_argument(
        "--use_data_db",
        action="store_true", 
        help="Use CST timeseries database instead of CSV for logging"
    )
    args, unknown = parser.parse_known_args()
    return args


def main():
    """
    Main execution function.
    
    CST federate lifecycle:
    1. Create federate instance
    2. create_federate() - Initialize HELICS and your simulation
    3. run_cosim_loop() - Run main simulation loop until stop time
    4. destroy_federate() - Clean up and disconnect
    """
    args = parse_args()
    
    # Step 1: Create federate instance
    federate = ExampleFederate("example")
    
    try:
        # Step 2: Initialize federate (reads config, creates HELICS federate)
        federate.create_federate(
            scenario_name=args.scenario,
            use_meta_db=args.use_meta_db,
            use_data_db=args.use_data_db
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
