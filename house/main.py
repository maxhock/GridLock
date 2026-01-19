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

from energysim.core.shared.data_structs import (
    BatteryConfig, RewardConfig, HeatPumpConfig, AirConditionerConfig,
    ThermalStorageConfig, SolarConfig, SystemActions, ThermalConfig,
    SystemState, ThermalState, BatteryState, ThermalStorageState,
    HeatPumpState, AirConditionerState,
)
from energysim.sim.simulator import JAXSimulator
from energysim.core.data.dataset import SimulationDataset
from house.common_config import create_common_configs
from house.build_my_house import create_2_room_house
import house.sample_data_generator
import helics as h
import jax.numpy as jnp
import equinox as eqx
import os


logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class NumpyJSONEncoder(json.JSONEncoder):
    """
    A JSON encoder that can handle NumPy/JAX data types.
    Converts arrays to lists, and numpy/jax floats/ints to python native types.
    """
    def default(self, obj):
        if isinstance(obj, np.ndarray) or isinstance(obj, jnp.ndarray):
            return obj.tolist()
        if isinstance(obj, (np.float16, np.float32, np.float64)) or isinstance(
            obj, (jnp.float16, jnp.float32, jnp.float64)
        ):
            return float(obj)
        if isinstance(
            obj, (np.int_, np.intc, np.intp, np.int8, np.int16, np.int32, np.int64)
        ) or isinstance(
            obj, (jnp.int_, jnp.intc, jnp.intp, jnp.int8, jnp.int16, jnp.int32, jnp.int64)
        ):
            return int(obj)
        return super().default(obj)


def serialize_system_state(state: SystemState) -> dict:
    """
    SystemState -> pure python dict for controller consumption.
    """
    return {
        "thermal": {
            "T_vector": np.array(state.thermal.T_vector).tolist(),
        },
        "battery": {
            "soc": float(state.battery.soc),
            "soh": float(state.battery.soh),
        },
        "storage": {
            "temperatures_c": np.array(state.storage.temperatures_c).tolist(),
        },
        "heat_pump": {
            "current_electrical_w": np.array(state.heat_pump.current_electrical_w).tolist(),
            "current_thermal_w": np.array(state.heat_pump.current_thermal_w).tolist(),
        },
        "air_conditioner": {
            "current_electrical_w": np.array(state.air_conditioner.current_electrical_w).tolist(),
            "current_thermal_w": np.array(state.air_conditioner.current_thermal_w).tolist(),
        },
    }


def setup_simulator(config_path: str) -> tuple:
    t_config = create_2_room_house()
    n_rooms = int(len(t_config.room_air_indices))

    configs = create_common_configs(house.sample_data_generator.DT_SECONDS)
    sim = JAXSimulator(**configs)

    return sim, n_rooms, t_config


def setup_federate(federate_name: str, dt_seconds: int):
    logger.info(f"Creating HELICS federate: {federate_name}")
    fed_info = h.helicsCreateFederateInfo()

    h.helicsFederateInfoSetCoreInitString(
        fed_info, f"--broker={os.getenv('HELICS_BROKER', 'broker')}"
    )

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
        raise ValueError(
            f"Federate name '{federate_name}' must end in an index (e.g., 'house_0')"
        )
    fed_index = match.group(0)

    # 1. Subscribe to house-specific actions (e.g., from controller)
    sub_action = h.helicsFederateRegisterSubscription(
        fed, f"{federate_name}/action", "string"
    )

    # 2. Subscribe to external battery load (currently unused in your net-load calc)
    sub_battery_load = h.helicsFederateRegisterSubscription(
        fed, f"battery_{fed_index}/battery_load", "W"
    )

    # 3. Publish full results (for controller/logging)
    pub_result = h.helicsFederateRegisterGlobalPublication(
        fed, f"{federate_name}/timestep_result", h.HELICS_DATA_TYPE_STRING, ""
    )

    # 4. Publish net load (for grid federate)
    pub_load = h.helicsFederateRegisterGlobalPublication(
        fed, f"node_{fed_index}/P", h.HELICS_DATA_TYPE_DOUBLE, "MW"
    )

    logger.info(f"Federate '{federate_name}' subscribing to:")
    logger.info(f"  - {federate_name}/action")
    logger.info(f"  - battery_{fed_index}/battery_load")
    logger.info(f"Federate '{federate_name}' publishing to:")
    logger.info(f"  - {federate_name}/timestep_result")
    logger.info(f"  - node_{fed_index}/P")

    return fed, (sub_action, sub_battery_load), (pub_result, pub_load), dt_seconds


def publish_result(pub: h.HelicsPublication, result: dict):
    """Publish the timestep result as a JSON string."""
    result_str = json.dumps(result, cls=NumpyJSONEncoder)
    h.helicsPublicationPublishString(pub, result_str)


def get_action(sub: h.HelicsInput) -> Dict[str, Any]:
    """Retrieve and decode the action from the subscription."""
    action_str = h.helicsInputGetString(sub)

    if not action_str:
        return {}

    try:
        data = json.loads(action_str)
        if isinstance(data, dict):
            return data

        logger.warning(
            f"Received non-dict action from HELICS ({type(data)}): {data}. "
            "Defaulting to empty action dict."
        )
        return {}

    except json.JSONDecodeError:
        logger.warning(
            f"Invalid action JSON received: {action_str}. Defaulting to {{}}."
        )
        return {}


def dict_to_system_actions(action_dict: Dict[str, Any], n_rooms: int) -> SystemActions:
    if not isinstance(action_dict, dict):
        logger.warning(
            f"dict_to_system_actions received non-dict ({type(action_dict)}): "
            f"{action_dict}. Using zero actions."
        )
        action_dict = {}

    return SystemActions(
        battery_power_w=jnp.array(action_dict.get("battery_power_w", 0.0)),
        heat_pump_power_w=jnp.array(action_dict.get("heat_pump_power_w", [0.0] * n_rooms)),
        ac_power_w=jnp.array(action_dict.get("ac_power_w", [0.0] * n_rooms)),
        storage_discharge_w=jnp.array(action_dict.get("storage_discharge_w", [0.0] * n_rooms)),
    )


def run_federate(
    fed,
    simulator,
    subs,
    pubs,
    dt_seconds,
    stop_time,
    n_rooms,
    t_config: ThermalConfig
):
    """
    FIX 2 Implementation:
    - Drive simulation with explicit helicsFederateRequestTime(fed, t_req)
    - Run only until stop_time (no longer always iterating full dataset)
    """
    sub_action, sub_battery_load = subs
    pub_result, pub_load = pubs

    simulator.reset()
    state: SystemState = simulator.state

    previous_action = SystemActions(
        battery_power_w=jnp.array(0.0),
        heat_pump_power_w=jnp.zeros(n_rooms),
        ac_power_w=jnp.zeros(n_rooms),
        storage_discharge_w=jnp.zeros(n_rooms)
    )

    # enter HELICS execution mode
    h.helicsFederateEnterExecutingMode(fed)

    # -------------------------
    # initial publish at t=0
    # -------------------------
    result = {
        "time": 0.0,
        "cost": 0.0,
        "state": serialize_system_state(state),
    }
    publish_result(pub_result, result)
    h.helicsPublicationPublishDouble(pub_load, 0.0)

    # dataset + split factors (keep ✅)
    dataset = SimulationDataset(
        house.sample_data_generator.FILE_NAME,
        house.sample_data_generator.DT_SECONDS
    )
    split_factors = jnp.array([0.6, 0.4])

    # ----------------------------------------------------------
    # FIX 2: Explicit HELICS RequestTime + stop_time control
    # ----------------------------------------------------------
    if dt_seconds <= 0:
        raise ValueError(f"dt_seconds must be > 0, got {dt_seconds}")

    # how many steps do we need?  stop_time=7200, dt=3600 => 2 steps (3600, 7200)
    n_steps = int(stop_time // dt_seconds)

    # dataset safety
    max_steps = min(n_steps, len(dataset))

    logger.info(
        f"[{h.helicsFederateGetName(fed)}] Running {max_steps} steps with dt={dt_seconds}s "
        f"(stop_time={stop_time}s, dataset_len={len(dataset)})."
    )

    for k in range(1, max_steps + 1):
        # request specific simulation time from HELICS
        t_req = k * dt_seconds
        current_time = h.helicsFederateRequestTime(fed, t_req)

        # dataset index
        exo_base = dataset[k - 1]

        exo = eqx.tree_at(
            lambda e: (e.solar_gains_w, e.occupancy_gains_w, e.device_gains_w),
            exo_base,
            (
                exo_base.solar_gains_w * split_factors,
                exo_base.occupancy_gains_w * split_factors,
                jnp.zeros(n_rooms),
            ),
        )

        raw_action = get_action(sub_action)
        action = dict_to_system_actions(raw_action, n_rooms)

        simulator, cost = simulator.step(action, previous_action, exo)
        state = simulator.state
        previous_action = action

        result = {
            "time": float(current_time),
            "cost": float(cost),
            "state": serialize_system_state(state),
        }
        publish_result(pub_result, result)

        # Calculate net load for grid federate
        hp_p_el = jnp.sum(state.heat_pump.current_electrical_w)
        ac_p_el = jnp.sum(state.air_conditioner.current_electrical_w)
        print(f"  Heat Pump Power: {hp_p_el} W, AC Power: {ac_p_el} W")

        # Battery power (from action)
        bat_p_el = jnp.asarray(action.battery_power_w * 1000.0)  # Convert kW to W
        print(f"  Battery Power Action: {bat_p_el} W")

        total_net_power_w = float(hp_p_el + ac_p_el + bat_p_el)
        total_net_power_mw = total_net_power_w / 1e6

        print(f"Time {current_time}s: Net Load = {total_net_power_mw:.6f} MW")
        h.helicsPublicationPublishDouble(pub_load, float(total_net_power_mw))

    logger.info(
        f"[{h.helicsFederateGetName(fed)}] Finished after {max_steps} steps "
        f"(last_time={max_steps * dt_seconds}s). Finalizing."
    )

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
        simulator, n_rooms, t_config = setup_simulator(args.config)
        fed, subs, pubs, dt = setup_federate(
            federate_name=args.name,
            dt_seconds=args.dt,
        )
        run_federate(
            fed=fed,
            simulator=simulator,
            subs=subs,
            pubs=pubs,
            dt_seconds=dt,
            stop_time=args.stop_time,
            n_rooms=n_rooms,
            t_config=t_config
        )
    except Exception as e:
        logger.error(f"An error occurred in {args.name}: {e}", exc_info=True)
        if fed:
            h.helicsFederateFinalize(fed)
    finally:
        h.helicsCloseLibrary()


if __name__ == "__main__":
    main()