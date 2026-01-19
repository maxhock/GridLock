# controller/main.py

import helics as h
import json
import logging
import argparse
import re
import random

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

def setup_federate(federate_name: str, dt_seconds: int) -> tuple:
    """Sets up HELICS federate, subscriptions, and publications."""
    
    logger.info(f"Creating HELICS federate: {federate_name}")
    fed_info = h.helicsCreateFederateInfo()
    h.helicsFederateInfoSetCoreTypeFromString(fed_info, "zmq")
    h.helicsFederateInfoSetIntegerProperty(fed_info, h.helics_property_int_log_level, h.helics_log_level_debug)
    h.helicsFederateInfoSetTimeProperty(fed_info, h.helics_property_time_delta, dt_seconds)
    fed = h.helicsCreateValueFederate(federate_name, fed_info)

    # Get index 'i' from name 'controller_i'
    match = re.search(r'\d+$', federate_name)
    if not match:
        raise ValueError(f"Federate name '{federate_name}' must end in an index (e.g., 'controller_0')")
    fed_index = match.group(0)

    # 1. Subscribe to the house's full state result
    sub_house_result = h.helicsFederateRegisterSubscription(fed, f"house_{fed_index}/timestep_result", "string")
    
    # 2. Publish actions to the house (e.g., for HVAC, which is not yet implemented)
    pub_house_action = h.helicsFederateRegisterGlobalPublication(
        fed, f"house_{fed_index}/action", h.HELICS_DATA_TYPE_STRING, ""
    )
    
    # 3. Publish actions to the battery
    pub_battery_action = h.helicsFederateRegisterGlobalPublication(
        fed, f"battery_{fed_index}/action", h.HELICS_DATA_TYPE_STRING, ""
    )

    logger.info(f"Federate '{federate_name}' subscribing to:")
    logger.info(f"  - house_{fed_index}/timestep_result")
    logger.info(f"Federate '{federate_name}' publishing to:")
    logger.info(f"  - house_{fed_index}/action")
    logger.info(f"  - battery_{fed_index}/action")

    return fed, (sub_house_result), (pub_house_action, pub_battery_action)


def run_simulation_loop(fed, subs, pubs, stop_time):
    """Main HELICS simulation loop for the controller."""
    (sub_house_result) = subs
    (pub_house_action, pub_battery_action) = pubs

    h.helicsFederateEnterExecutingMode(fed)
    logger.info("Federate entering execution mode.")

    # --- Publish initial actions at t=0 ---
    # House action (empty for now)
    h.helicsPublicationPublishString(pub_house_action, json.dumps({})) 
    # Battery action (hold)
    h.helicsPublicationPublishString(pub_battery_action, json.dumps({"normalized_power": 0.0}))

    current_time = 0
    while current_time < stop_time:
        # Request the next time step (e.g., 900s)
        current_time = h.helicsFederateRequestNextStep(fed)
        logger.debug(f"Granted time: {current_time}s")

        # 1. Get the latest state from the house
        state_str = h.helicsInputGetString(sub_house_result)
        if state_str:
            try:
                state = json.loads(state_str)
                # You could use this state for smart decisions, e.g.:
                # temp = state['observations']['thermal']['temperature']
                # soc = state['observations']['components']['external_battery']['electrical_soc']
                logger.info(f"Time: {current_time}s | Received state from house.")
            except (json.JSONDecodeError, KeyError):
                logger.warning(f"Time: {current_time}s | Could not decode state: {state_str[:50]}...")
        
        # 2. Decide on new actions (FAKE CONTROLLER: just be random)
        
        # Action for the house (e.g., for HVAC component if it existed)
        # We send an empty action, which the house simulator will ignore.
        house_action = {} 
        
        # Action for the battery (charge/discharge randomly)
        battery_action = {"normalized_power": random.uniform(-1.0, 1.0)}

        # 3. Publish actions for the *next* timestep
        h.helicsPublicationPublishString(pub_house_action, json.dumps(house_action))
        h.helicsPublicationPublishString(pub_battery_action, json.dumps(battery_action))
        
        logger.info(f"Time: {current_time}s | Published actions: House {house_action}, Battery {battery_action}")

    logger.info("Simulation finished. Finalizing federate.")
    h.helicsFederateFinalize(fed)
    h.helicsFederateFree(fed)


def main():
    parser = argparse.ArgumentParser(description="Controller HELICS Federate")
    parser.add_argument("--name", type=str, required=True, help="Name of the federate (e.g., controller_0)")
    parser.add_argument("--stop_time", type=float, default=86400, help="Simulation stop time in seconds")
    parser.add_argument("--dt", type=int, default=900, help="Time step in seconds")
    
    args = parser.parse_args()
    logger.info(f"Starting federate '{args.name}' with dt={args.dt}s")
    
    fed = None
    try:
        fed, subs, pubs = setup_federate(
            federate_name=args.name,
            dt_seconds=args.dt
        )
        run_simulation_loop(fed, subs, pubs, args.stop_time)
    except Exception as e:
        logger.error(f"An error occurred in {args.name}: {e}", exc_info=True)
        if fed:
            h.helicsFederateFinalize(fed)
    finally:
        h.helicsCloseLibrary()

if __name__ == "__main__":
    main()