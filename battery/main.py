# battery/main.py

from dataclasses import asdict
import re
import helics as h
import json
import logging
import argparse
import yaml
from dacite import from_dict

from energysim.core.components.factory import build_component
from energysim.core.components.battery.config import BatteryComponentConfig
from energysim.core.components.battery.component import Battery
from energysim.core.components.outputs import ComponentOutputs, ElectricalEnergy
from energysim.core.components.spaces import DiscreteSpace
from energysim.core.state import SimulationState # For type hinting, though state is minimal
from energysim.core.timestep_data import TimestepData

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def setup_battery_and_federate(config_path: str, federate_name: str, dt_seconds: int):
    """Loads config, builds battery, and sets up HELICS federate."""

    logger.info(f"Loading battery config from {config_path}")
    with open(config_path, 'r') as f:
        config_data = yaml.safe_load(f)

    battery_model_cfg = from_dict(data_class=BatteryComponentConfig, data=config_data)
    battery = build_component(battery_model_cfg)
    current_outputs: ComponentOutputs = battery.initialize()
    logger.info(f"Battery component initialized. Initial SOC: {current_outputs.electrical_storage.soc}")

    # --- HELICS Setup ---
    fed_info = h.helicsCreateFederateInfo()
    h.helicsFederateInfoSetCoreTypeFromString(fed_info, "zmq")
    h.helicsFederateInfoSetTimeProperty(fed_info, h.helics_property_time_delta, dt_seconds)
    h.helicsFederateInfoSetIntegerProperty(fed_info, h.helics_property_int_log_level, h.helics_log_level_debug)
    fed = h.helicsCreateValueFederate(federate_name, fed_info)
    logger.info(f"HELICS federate '{federate_name}' created.")

    # Get index 'i' from name 'battery_i'
    match = re.search(r'\d+$', federate_name)
    if not match:
        raise ValueError(f"Federate name '{federate_name}' must end in an index (e.g., 'battery_0')")
    fed_index = match.group(0)

    # Register subscription for control actions
    sub_action = h.helicsFederateRegisterSubscription(fed, f"controller_{fed_index}/action/battery", "string")

    # --- MODIFIED PUBLICATIONS ---
    # 1. Publish the net load (for the house)
    pub_load = h.helicsFederateRegisterGlobalPublication(fed, f"{federate_name}/battery_load", h.HELICS_DATA_TYPE_DOUBLE, "W")
    
    # 2. Publish the full state (for the controller)
    pub_state = h.helicsFederateRegisterGlobalPublication(fed, f"{federate_name}/timestep_result", h.HELICS_DATA_TYPE_STRING, "")
    
    logger.info(f"Federate '{federate_name}' subscribing to:")
    logger.info(f"  - controller_{fed_index}/action/battery")
    logger.info(f"Federate '{federate_name}' publishing to:")
    logger.info(f"  - {federate_name}/battery_load (for house_{fed_index})")
    logger.info(f"  - {federate_name}/timestep_result (for controller_{fed_index})")

    return fed, battery, current_outputs, sub_action, (pub_load, pub_state), dt_seconds


def get_action_space_definition(battery_config: BatteryComponentConfig) -> dict:
    """Helper to define the action space based on the battery's config."""
    # This defines the "shape" of the action the controller must send.
    return {"normalized_power": asdict(DiscreteSpace(
        n=3  # Example: -1 (discharge), 0 (hold), 1 (charge)
    ))}


def run_simulation_loop(fed, battery: Battery, initial_outputs, sub_action, pubs, dt_seconds, stop_time):
    """Main HELICS simulation loop."""
    # --- MODIFIED ---
    pub_load, pub_state = pubs 
    current_outputs: ComponentOutputs = initial_outputs

    # Get the action space definition once
    action_space = asdict(battery.action_space)

    h.helicsFederateEnterExecutingMode(fed)
    logger.info("Federate entering execution mode.")

    # --- Publish initial state at t=0 ---
    initial_state_json = json.dumps({
        "observations": {
            "soc": current_outputs.electrical_storage.soc,
            "power_w": 0.0
        },
        "action_space": action_space
    })
    h.helicsPublicationPublishDouble(pub_load, 0.0) # For house
    h.helicsPublicationPublishString(pub_state, initial_state_json) # For controller

    current_time = 0
    while current_time < stop_time:
        current_time = h.helicsFederateRequestNextStep(fed)
        logger.debug(f"Granted time: {current_time}")

        # 1. Get action from controller
        action_str = h.helicsInputGetString(sub_action)
        try:
            # Action is expected to be a dict, e.g., {"battery": {"normalized_power": 0.5}}
            full_action_dict = json.loads(action_str) if action_str else {}
            # Extract just the part for this component
            battery_action = full_action_dict.get("normalized_power", 0.0)

        except json.JSONDecodeError:
            logger.warning(f"Invalid action JSON received: {action_str}. Defaulting to 0 power.")
            battery_action = 0.0

        # 2. Step the battery simulation
        dummy_data = TimestepData(timestamp=int(current_time), dt_seconds=dt_seconds, features={})
        dummy_state = SimulationState(timestep_data=dummy_data, thermal_state=None, component_outputs={})
        
        current_outputs = battery.advance(battery_action, dummy_state, dt_seconds)

        # 3. Publish results for this timestep
        net_power_w = current_outputs.electrical_energy.net / dt_seconds

        # Publish load for the house
        h.helicsPublicationPublishDouble(pub_load, net_power_w)

        current_state_json = json.dumps({
            "observations": {
                "soc": current_outputs.electrical_storage.soc,
                "power_w": net_power_w
            },
            "action_space": action_space # Publish action space every step
        })
        h.helicsPublicationPublishString(pub_state, current_state_json)

        logger.info(f"Time: {current_time}s | Action: {battery_action} | Power: {net_power_w:.2f} W | SOC: {current_outputs.electrical_storage.soc:.4f}")

    # --- Shutdown ---
    logger.info("Simulation finished. Finalizing federate.")
    h.helicsFederateFinalize(fed)
    h.helicsFederateFree(fed)
    h.helicsCloseLibrary()

def main():
    parser = argparse.ArgumentParser(description="External Battery HELICS Federate")
    parser.add_argument("--name", type=str, required=True, help="Name of the federate (e.g., battery_0)")
    parser.add_argument("--config", type=str, default="battery/config.yaml", help="Path to battery config YAML")
    parser.add_argument("--stop_time", type=float, default=86400, help="Simulation stop time in seconds")
    parser.add_argument("--dt", type=int, default=900, help="Time step in seconds")
    
    args = parser.parse_args()
    logger.info(f"Starting federate '{args.name}' with dt={args.dt}s")

    try:
        fed, battery, init_outputs, sub, pubs, dt = setup_battery_and_federate(
            config_path=args.config,
            federate_name=args.name,
            dt_seconds=args.dt
        )
        run_simulation_loop(fed, battery, init_outputs, sub, pubs, dt, args.stop_time)
    except Exception as e:
        logger.error(f"Fatal error in federate {args.name}: {e}", exc_info=True)
        h.helicsCloseLibrary() # Ensure library is closed on error

if __name__ == "__main__":
    main()