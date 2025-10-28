# energysim/cosim/fake_controller_federate.py
# (Keep imports and class definition as before)

import helics as h
import random
import json
import time
import logging
from typing import Dict, Any, Optional
from dataclasses import asdict, dataclass

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def setup_federate_and_publication(time_delta: int, action_topic: str) -> tuple:

    logging.info("Creating HELICS federate.")
    fed_info = h.helicsCreateFederateInfo()
    h.helicsFederateInfoSetCoreTypeFromString(fed_info, "zmq")
    h.helicsFederateInfoSetIntegerProperty(fed_info, h.helics_property_int_log_level, h.helics_log_level_debug)
    h.helicsFederateInfoSetFlagOption(fed_info, h.helics_flag_uninterruptible, True)
    h.helicsFederateInfoSetTimeProperty(fed_info, h.helics_property_time_delta, time_delta)
    fed = h.helicsCreateValueFederate("fake_controller", fed_info)
    pub = h.helicsFederateRegisterGlobalPublication(fed, action_topic, h.HELICS_DATA_TYPE_STRING, "")

    return fed, pub

def publish_action(pub: h.HelicsPublication, action: dict):
    """Publish the action as a JSON string."""
    action_str = json.dumps(action)
    h.helicsPublicationPublishString(pub, action_str)

def run_federate(fed, pub, stop_time):
    logging.info("Entering HELICS execution mode.")
    h.helicsFederateEnterExecutingMode(fed)

    publish_action(pub, {"battery": {"normalized_power": random.uniform(-1.0, 1.0)}})

    # Immediately request the first time step. This is the key to avoiding the t=0 race condition.
    # The federate will now wait here until the broker grants it t=900s.
    current_time = h.helicsFederateRequestNextStep(fed)
    logging.info(f"First time step granted: {current_time}s")

    while current_time < stop_time:
        # At t=900s, get the action the agent published at t=0s. It will be waiting.
        publish_action(pub, {"battery": {"normalized_power": random.uniform(-1.0, 1.0)}})
        logging.info(f"Time: {current_time}s | Published timestep result.")
            
        # Request the next time step
        current_time = h.helicsFederateRequestNextStep(fed)

    logging.info("Simulation finished. Finalizing federate.")
    h.helicsFederateFinalize(fed)
    h.helicsFederateFree(fed)


if __name__ == "__main__":
    TIME_DELTA = 900  # seconds
    ACTION_TOPIC = "actions"
    STOP_TIME = 3600 * 3  # 3 hours

    try:
        fed, pub = setup_federate_and_publication(TIME_DELTA, ACTION_TOPIC)
        run_federate(
            fed=fed,
            pub=pub,
            stop_time=STOP_TIME
        )
    except Exception as e:
        logging.error(f"An error occurred: {e}", exc_info=True)