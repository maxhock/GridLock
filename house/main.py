# house/main.py

from dataclasses import asdict
import logging
from time import time
from typing import Dict, Any
import yaml
from dacite import from_dict
import numpy as np
import json
import argparse
import re

from energysim.cosim.config import BuildingSimulationConfig
from energysim.cosim.factory import SimulatorFactory
from energysim.cosim.building_sim import BuildingSimulator, SimulationTimestepResult
from energysim.core.components.outputs import ComponentOutputs, ElectricalEnergy

import helics as h

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


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


def setup_simulator_and_federate(config_path: str, federate_name: str, dt_seconds: int) -> tuple:
    logger.info(f"Loading environment configuration from {config_path}.")
    with open(config_path, 'r') as f:
        yaml_str = f.read()
    yaml_cfg = yaml.safe_load(yaml_str)
    
    cfg = from_dict(
        data_class=BuildingSimulationConfig, data=yaml_cfg
    )
    logger.info("Environment configuration loaded.")

    logger.info("Creating environment.")
    simulator = SimulatorFactory.create_simulator(cfg)
    timestep_result = simulator.reset()
    result = asdict(timestep_result) | {"action_space": asdict(simulator.action_space)}
    logger.info("Environment created and reset successfully.")

    logger.info(f"Creating HELICS federate: {federate_name}")
    fed_info = h.helicsCreateFederateInfo()
    h.helicsFederateInfoSetCoreTypeFromString(fed_info, "zmq")
    h.helicsFederateInfoSetIntegerProperty(
        fed_info, h.helics_property_int_log_level, h.helics_log_level_debug
    )
    h.helicsFederateInfoSetTimeProperty(
        fed_info, h.helics_property_time_delta, dt_seconds
    )
    fed = h.helicsCreateValueFederate(federate_name, fed_info)
    
    # Get index 'i' from name 'house_i'
    match = re.search(r'\d+$', federate_name)
    if not match:
        raise ValueError(f"Federate name '{federate_name}' must end in an index (e.g., 'house_0')")
    fed_index = match.group(0)

    # 1. Subscribe to house-specific actions (e.g., from a controller)
    sub_action = h.helicsFederateRegisterSubscription(fed, f"{federate_name}/action", "string")
    
    # 2. Subscribe to the external battery's load
    sub_battery_load = h.helicsFederateRegisterSubscription(fed, f"battery_{fed_index}/battery_load", "W")
    
    # 3. Publish full results (for controller/logging)
    pub_result = h.helicsFederateRegisterGlobalPublication(
        fed, f"{federate_name}/timestep_result", h.HELICS_DATA_TYPE_STRING, ""
    )
    
    # 4. Publish net load (for grid federate)
    pub_load = h.helicsFederateRegisterGlobalPublication(
        fed, f"{federate_name}/house_load", h.HELICS_DATA_TYPE_DOUBLE, "W"
    )

    logger.info(f"Federate '{federate_name}' subscribing to:")
    logger.info(f"  - {federate_name}/action")
    logger.info(f"  - battery_{fed_index}/battery_load")
    logger.info(f"Federate '{federate_name}' publishing to:")
    logger.info(f"  - {federate_name}/timestep_result")
    logger.info(f"  - {federate_name}/house_load")

    return fed, simulator, result, (sub_action, sub_battery_load), (pub_result, pub_load), dt_seconds


def publish_result(pub: h.HelicsPublication, result: dict):
    """Publish the timestep result as a JSON string."""
    result_str = json.dumps(result, cls=NumpyJSONEncoder)
    h.helicsPublicationPublishString(pub, result_str)


def get_action(sub: h.HelicsInput) -> Dict[str, Any]:
    """Retrieve and decode the action from the subscription."""
    action_str = h.helicsInputGetString(sub)
    # Return an empty dict if no action, so simulator.step() receives {}
    try:
        return json.loads(action_str) if action_str else {}
    except json.JSONDecodeError:
        logger.warning(f"Invalid action JSON received: {action_str}. Defaulting to {{}}.")
        return {}


def run_federate(
    fed, simulator: BuildingSimulator, result, subs, pubs, dt_seconds, stop_time
):
    sub_action, sub_battery_load = subs
    pub_result, pub_load = pubs
    
    logger.info("Entering HELICS execution mode.")
    h.helicsFederateEnterExecutingMode(fed)

    # Publish the initial state of the environment for t=0
    publish_result(pub_result, result)
    h.helicsPublicationPublishDouble(pub_load, 0.0) # No load at t=0

    current_time = 0
    while current_time < stop_time:
        # Request the next time step (e.g., 900s)
        current_time = h.helicsFederateRequestNextStep(fed)
        logger.debug(f"Granted time: {current_time}s")

        # 1. Get house action (for internal components, if any)
        action = get_action(sub_action)
        
        # 2. Get external battery load
        battery_power_w = h.helicsInputGetDouble(sub_battery_load)
        battery_energy_j = battery_power_w * dt_seconds

        # 3. Package external battery data for the simulator
        # A positive value is demand (charging), negative is generation (discharging)
        external_battery_output = ComponentOutputs(
            electrical_energy=ElectricalEnergy(
                demand_j=max(0, battery_energy_j),
                generation_j=max(0, -battery_energy_j)
            )
        )
        external_outputs = {"external_battery": external_battery_output}

        logger.info(f"Time: {current_time}s | Received action: {action} | Received ext. battery: {battery_power_w:.2f} W")

        # 4. Step the environment
        timestep_result = simulator.step(action, external_component_outputs=external_outputs)
        
        if timestep_result is None:
            logger.warning(f"Time: {current_time}s | Simulator step returned None. Ending simulation.")
            break
        
        assert isinstance(
            timestep_result, SimulationTimestepResult
        ), f"timestep_result must be of type SimulationTimestepResult, got {type(timestep_result)}"
        result = asdict(timestep_result) | {"action_space": asdict(simulator.action_space)}

        # 5. Publish full JSON results (for controller)
        publish_result(pub=pub_result, result=result)
        
        # 6. Publish net load (for grid)
        # system_balance.electrical_energy.net is the *total* net load of the house
        # (internal loads + external battery). Convert J back to W.
        total_net_power_w = timestep_result.system_balance.electrical_energy.net / dt_seconds
        h.helicsPublicationPublishDouble(pub_load, total_net_power_w)
        
        logger.info(f"Time: {current_time}s | Published total net load: {total_net_power_w:.2f} W")

    logger.info("Simulation finished. Finalizing federate.")
    h.helicsFederateFinalize(fed)
    h.helicsFederateFree(fed)


def main():
    parser = argparse.ArgumentParser(description="House Simulation HELICS Federate")
    parser.add_argument("--name", type=str, required=True, help="Name of the federate (e.g., house_0)")
    parser.add_argument("--config", type=str, default="house/config.yaml", help="Path to house config YAML")
    parser.add_argument("--stop_time", type=float, default=86400, help="Simulation stop time in seconds")
    parser.add_argument("--dt", type=int, default=900, help="Time step in seconds")
    
    args = parser.parse_args()
    logger.info(f"Starting federate '{args.name}' with dt={args.dt}s")
    
    fed = None
    try:
        fed, simulator, result, subs, pubs, dt = setup_simulator_and_federate(
            config_path=args.config,
            federate_name=args.name,
            dt_seconds=args.dt
        )
        run_federate(
            fed=fed,
            simulator=simulator,
            result=result,
            subs=subs,
            pubs=pubs,
            dt_seconds=dt,
            stop_time=args.stop_time,
        )
    except Exception as e:
        logger.error(f"An error occurred in {args.name}: {e}", exc_info=True)
        if fed:
            h.helicsFederateFinalize(fed)
    finally:
        h.helicsCloseLibrary()

if __name__ == "__main__":
    main()