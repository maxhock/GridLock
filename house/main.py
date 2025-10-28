from dataclasses import asdict
import logging
from typing import Dict, Any
import yaml
from dacite import from_dict
import numpy as np
import json

from energysim.cosim.config import BuildingSimulationConfig
from energysim.cosim.factory import SimulatorFactory
from energysim.cosim.building_sim import BuildingSimulator, SimulationTimestepResult

import helics as h

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)


class NumpyJSONEncoder(json.JSONEncoder):
    """
    A JSON encoder that can handle NumPy data types.
    Converts numpy arrays to lists, and numpy floats/ints to python native types.
    """

    def default(self, obj):
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        if isinstance(obj, (np.float16, np.float32, np.float64)):
            return float(obj)
        if isinstance(
            obj, (np.int_, np.intc, np.intp, np.int8, np.int16, np.int32, np.int64)
        ):
            return int(obj)
        return super().default(obj)


def setup_simulator_and_federate(yaml_str: str) -> tuple:
    logging.info("Loading environment configuration from YAML.")
    yaml_cfg = yaml.safe_load(yaml_str)
    cfg = from_dict(
        data_class=BuildingSimulationConfig, data=yaml_cfg
    )  # , config=Config(cast=[Enum]))
    logging.info("Environment configuration loaded.")

    logging.info("Creating environment.")
    simulator = SimulatorFactory.create_simulator(cfg)
    timestep_result = simulator.reset()
    logging.info("Environment created and reset successfully.")

    logging.info("Creating HELICS federate.")
    fed_info = h.helicsCreateFederateInfo()
    h.helicsFederateInfoSetCoreTypeFromString(fed_info, "zmq")
    h.helicsFederateInfoSetIntegerProperty(
        fed_info, h.helics_property_int_log_level, h.helics_log_level_debug
    )
    h.helicsFederateInfoSetFlagOption(fed_info, h.helics_flag_uninterruptible, True)
    time_delta = cfg.dataset.params.dt_seconds
    h.helicsFederateInfoSetTimeProperty(
        fed_info, h.helics_property_time_delta, time_delta
    )
    fed = h.helicsCreateValueFederate("building_simulation", fed_info)

    sub = h.helicsFederateRegisterSubscription(fed, "actions", "")
    pub = h.helicsFederateRegisterGlobalPublication(
        fed, "timestep_result", h.HELICS_DATA_TYPE_STRING, ""
    )

    return fed, simulator, timestep_result, sub, pub


def publish_result(pub: h.HelicsPublication, result: Any):
    """Publish the timestep result as a JSON string."""
    result_str = json.dumps(asdict(result), cls=NumpyJSONEncoder)
    h.helicsPublicationPublishString(pub, result_str)


def get_action(sub: h.HelicsInput) -> Dict[str, Any]:
    """Retrieve and decode the action from the subscription."""
    action_str = h.helicsInputGetString(sub)
    return json.loads(action_str) if action_str else {}


def run_federate(
    fed, simulator: BuildingSimulator, timestep_result, sub, pub, stop_time
):
    logging.info("Entering HELICS execution mode.")
    h.helicsFederateEnterExecutingMode(fed)

    # Publish the initial state of the environment for t=0
    publish_result(pub, timestep_result)

    # Immediately request the first time step. This is the key to avoiding the t=0 race condition.
    # The federate will now wait here until the broker grants it t=900s.
    current_time = h.helicsFederateRequestNextStep(fed)
    logging.info(f"First time step granted: {current_time}s")

    while current_time < stop_time:
        # At t=900s, get the action the agent published at t=0s. It will be waiting.
        action = get_action(sub)
        if not action or any(not d for d in action.values()):
            logging.error(
                f"Invalid or incomplete action received: {action}. Terminating."
            )
            break
        logging.info(f"Time: {current_time}s | Received action: {action}")

        # Step the environment
        timestep_result = simulator.step(action)
        assert isinstance(
            timestep_result, SimulationTimestepResult
        ), f"timestep_result must be of type SimulationTimestepResult, got {type(timestep_result)}"
        # Publish results for the *next* time step
        publish_result(pub=pub, result=timestep_result)
        logging.info(f"Time: {current_time}s | Published timestep result.")

        # Request the next time step
        current_time = h.helicsFederateRequestNextStep(fed)

    logging.info("Simulation finished. Finalizing federate.")
    h.helicsFederateFinalize(fed)
    h.helicsFederateFree(fed)


if __name__ == "__main__":
    STOP_TIME = 48 * 3600
    fed = None
    try:
        with open("house/config.yaml", "r") as f:
            yaml_str = f.read()
        fed, simulator, timestep_result, sub, pub = setup_simulator_and_federate(
            yaml_str
        )
        run_federate(
            fed=fed,
            simulator=simulator,
            timestep_result=timestep_result,
            sub=sub,
            pub=pub,
            stop_time=STOP_TIME,
        )
    except Exception as e:
        logging.error(f"An error occurred: {e}", exc_info=True)
        if fed:
            h.helicsFederateFinalize(fed)
    finally:
        h.helicsCloseLibrary()
