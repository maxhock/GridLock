# house/main.py

from dataclasses import asdict, is_dataclass
import logging
from time import time
from typing import Any, Dict
import yaml
from dacite import from_dict
import numpy as np
import json
import argparse
import re

from cosim_toolbox.sims import Federate
from energysim.core.shared.data_structs import (
    BatteryConfig,
    RewardConfig,
    HeatPumpConfig,
    AirConditionerConfig,
    ThermalStorageConfig,
    SystemActions,
    ThermalConfig,
    SystemState,
    ThermalState,
    BatteryState,
    ThermalStorageState,
    HeatPumpState,
    AirConditionerState,
    PVConfig,
)
from energysim.sim.simulator import JAXSimulator
from energysim.core.data.dataset import SimulationDataset
from house.common_config import create_common_configs
from house.build_my_house import create_2_room_house
from house.exogenous_data import prepare_aligned_timeseries
import tools.sample_data_generator
import helics as h
import jax.numpy as jnp
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
            obj,
            (jnp.int_, jnp.intc, jnp.intp, jnp.int8, jnp.int16, jnp.int32, jnp.int64),
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
            "current_electrical_w": np.array(
                state.heat_pump.current_electrical_w
            ).tolist(),
            "current_thermal_w": np.array(state.heat_pump.current_thermal_w).tolist(),
        },
        "air_conditioner": {
            "current_electrical_w": np.array(
                state.air_conditioner.current_electrical_w
            ).tolist(),
            "current_thermal_w": np.array(
                state.air_conditioner.current_thermal_w
            ).tolist(),
        },
    }


def serialize_exogenous_data(exo: Any) -> dict:
    if is_dataclass(exo):
        return asdict(exo)
    if hasattr(exo, "__dict__"):
        return dict(exo.__dict__)
    return {}


def setup_simulator(config_path: str, dt_seconds: int) -> tuple:
    t_config = create_2_room_house()
    n_rooms = int(len(t_config.room_air_indices))

    configs = create_common_configs(dt_seconds)
    sim = JAXSimulator(**configs)

    return sim, n_rooms, t_config


def get_action(action_value: Any) -> Dict[str, Any]:
    """Retrieve and decode the action from CST's cached subscription value."""
    if action_value is None or isinstance(action_value, list):
        return {}

    if not isinstance(action_value, str):
        logger.warning(
            f"Received non-string action payload ({type(action_value)}): {action_value}. "
            "Defaulting to empty action dict."
        )
        return {}

    if not action_value:
        return {}

    try:
        data = json.loads(action_value)
        if isinstance(data, dict):
            return data

        logger.warning(
            f"Received non-dict action from HELICS ({type(data)}): {data}. "
            "Defaulting to empty action dict."
        )
        return {}

    except json.JSONDecodeError:
        logger.warning(
            f"Invalid action JSON received: {action_value}. Defaulting to {{}}."
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
        heat_pump_power_w=jnp.array(
            action_dict.get("heat_pump_power_w", [0.0] * n_rooms)
        ),
        ac_power_w=jnp.array(action_dict.get("ac_power_w", [0.0] * n_rooms)),
        storage_discharge_w=jnp.array(
            action_dict.get("storage_discharge_w", [0.0] * n_rooms)
        ),
    )


class HouseFederate(Federate):
    """House federate using CST for lifecycle/time management."""

    def __init__(
        self,
        federate_name: str,
        config_path: str,
        stop_time: float,
        dt_seconds: int,
    ):
        super().__init__(federate_name)
        self.config_path = config_path
        self.requested_stop_time = float(stop_time)
        self.dt_seconds = int(dt_seconds)
        self.simulator: JAXSimulator | None = None
        self.dataset: SimulationDataset | None = None
        self.n_rooms = 2
        self.t_config: ThermalConfig | None = None
        self.max_steps = 0
        self.action_key = ""
        self.result_key = ""
        self.load_key = ""

    def create_federate(self):
        """Create the CST federate and register HELICS interfaces."""
        if self.dt_seconds <= 0:
            raise ValueError(f"dt_seconds must be > 0, got {self.dt_seconds}")

        self.simulator, self.n_rooms, self.t_config = setup_simulator(
            self.config_path, self.dt_seconds
        )

        dataset_info = prepare_aligned_timeseries(
            tools.sample_data_generator.FILE_NAME,
            self.dt_seconds,
        )
        logger.info(
            f"[{self.federate_name}] Using exogenous dataset {dataset_info.path} "
            f"(source_dt={dataset_info.source_dt_seconds}s, "
            f"target_dt={dataset_info.target_dt_seconds}s, "
            f"rows={dataset_info.source_rows}->{dataset_info.aligned_rows})."
        )
        self.dataset = SimulationDataset(dataset_info.path, self.dt_seconds)
        self.max_steps = min(
            int(self.requested_stop_time // self.dt_seconds), len(self.dataset)
        )

        self.federate_type = "value"
        self.period = float(self.dt_seconds)
        self.stop_time = float(self.max_steps * self.dt_seconds)
        self.granted_time = 0.0
        self.config = {
            "name": self.federate_name,
            "coreType": "zmq",
            "broker": os.getenv("HELICS_BROKER", "broker"),
            "period": float(self.dt_seconds),
            "terminate_on_error": True,
        }

        self.pubs = {}
        self.inputs = {}
        self.data_from_federation = {"inputs": {}, "endpoints": {}}
        self.data_to_federation = {"publications": {}, "endpoints": {}}

        self.create_helics_fed()

        match = re.search(r"\d+$", self.federate_name)
        if not match:
            raise ValueError(
                f"Federate name '{self.federate_name}' must end in an index "
                "(e.g., 'house_0')"
            )
        fed_index = match.group(0)

        self.action_key = f"{self.federate_name}/action"
        self.result_key = f"{self.federate_name}/timestep_result"
        self.load_key = f"node_{fed_index}/P"

        h.helicsFederateRegisterSubscription(self.hfed, self.action_key, "string")
        self.inputs[self.action_key] = {"type": "string", "key": self.action_key}
        self.data_from_federation["inputs"][self.action_key] = None

        h.helicsFederateRegisterGlobalPublication(
            self.hfed, self.result_key, h.HELICS_DATA_TYPE_STRING, ""
        )
        self.pubs[self.result_key] = {"type": "string", "key": self.result_key}
        self.data_to_federation["publications"][self.result_key] = None

        h.helicsFederateRegisterGlobalPublication(
            self.hfed, self.load_key, h.HELICS_DATA_TYPE_DOUBLE, "MW"
        )
        self.pubs[self.load_key] = {"type": "double", "key": self.load_key}
        self.data_to_federation["publications"][self.load_key] = None

        logger.info(f"Federate '{self.federate_name}' subscribing to:")
        logger.info(f"  - {self.action_key}")
        logger.info(f"Federate '{self.federate_name}' publishing to:")
        logger.info(f"  - {self.result_key}")
        logger.info(f"  - {self.load_key}")
        logger.info(
            f"[{self.federate_name}] Running {self.max_steps} steps with dt={self.dt_seconds}s "
            f"(stop_time={self.stop_time}s, dataset_len={len(self.dataset)})."
        )

    def on_enter_executing_mode(self) -> None:
        """Reset and publish the initial t=0 state."""
        self.simulator.reset()
        state = self.simulator.state
        result = {
            "schema_version": 1,
            "federate": self.federate_name,
            "node": self.load_key.rsplit("/", maxsplit=1)[0],
            "time": 0.0,
            "dt_s": self.dt_seconds,
            "cost": 0.0,
            "grid_exchange": {
                "publication": self.load_key,
                "total_load_mw": 0.0,
                "sign_convention": "positive_consumption",
            },
            "loads_w": {
                "base": 0.0,
                "heat_pump": 0.0,
                "air_conditioner": 0.0,
                "battery": 0.0,
                "controllable_total": 0.0,
                "grid_total": 0.0,
            },
            "action": {},
            "state": serialize_system_state(state),
            "exogenous": {},
        }
        self.data_to_federation["publications"][self.result_key] = json.dumps(
            result, cls=NumpyJSONEncoder
        )
        self.data_to_federation["publications"][self.load_key] = 0.0
        self.send_data_to_federation(reset=True)

    def update_internal_model(self):
        """Advance the house simulation by one CST-controlled time step."""
        step_idx = int(self.granted_time // self.dt_seconds) - 1
        if step_idx < 0 or step_idx >= self.max_steps:
            return

        exo = self.dataset[step_idx]

        raw_action = get_action(
            self.data_from_federation["inputs"].get(self.action_key)
        )
        action = dict_to_system_actions(raw_action, self.n_rooms)

        self.simulator, _outputs = self.simulator.step(action, exo)
        state = self.simulator.state

        hp_p_el = jnp.sum(state.heat_pump.current_electrical_w)
        ac_p_el = jnp.sum(state.air_conditioner.current_electrical_w)
        print(f"  Heat Pump Power: {hp_p_el} W, AC Power: {ac_p_el} W")

        bat_p_el = jnp.asarray(action.battery_power_w)
        base_load_w = float(getattr(exo, "base_load_w", getattr(exo, "load", 0.0)))
        print(f"  Battery Power Action: {bat_p_el} W, Base Load: {base_load_w} W")

        heat_pump_w = float(hp_p_el)
        air_conditioner_w = float(ac_p_el)
        battery_w = float(bat_p_el)
        controllable_load_w = heat_pump_w + air_conditioner_w + battery_w
        controllable_load_mw = controllable_load_w / 1e6
        total_load_w = base_load_w + controllable_load_w
        total_load_mw = total_load_w / 1e6

        print(
            f"Time {self.granted_time}s: Controllable Load = "
            f"{controllable_load_mw:.6f} MW, Total Load = {total_load_mw:.6f} MW"
        )

        result = {
            "schema_version": 1,
            "federate": self.federate_name,
            "node": self.load_key.rsplit("/", maxsplit=1)[0],
            "time": float(self.granted_time),
            "dt_s": self.dt_seconds,
            "cost": 0.0,
            "grid_exchange": {
                "publication": self.load_key,
                "total_load_mw": total_load_mw,
                "sign_convention": "positive_consumption",
            },
            "loads_w": {
                "base": base_load_w,
                "heat_pump": heat_pump_w,
                "air_conditioner": air_conditioner_w,
                "battery": battery_w,
                "controllable_total": controllable_load_w,
                "grid_total": total_load_w,
            },
            "action": {
                "battery_power_w": battery_w,
                "heat_pump_power_w": np.array(action.heat_pump_power_w).tolist(),
                "ac_power_w": np.array(action.ac_power_w).tolist(),
                "storage_discharge_w": np.array(
                    action.storage_discharge_w
                ).tolist(),
            },
            "state": serialize_system_state(state),
            "exogenous": serialize_exogenous_data(exo),
        }
        self.data_to_federation["publications"][self.result_key] = json.dumps(
            result, cls=NumpyJSONEncoder
        )
        self.data_to_federation["publications"][self.load_key] = float(total_load_mw)


def main():
    parser = argparse.ArgumentParser(description="House Simulation HELICS Federate")
    parser.add_argument(
        "--name",
        type=str,
        required=True,
        help="Name of the federate (e.g., house_0)",
    )
    parser.add_argument(
        "--config",
        type=str,
        default="house/config.yaml",
        help="Path to house config YAML",
    )
    parser.add_argument(
        "--stop_time",
        type=float,
        default=86400,
        help="Simulation stop time in seconds",
    )
    parser.add_argument("--dt", type=int, default=900, help="Time step in seconds")

    args = parser.parse_args()
    logger.info(f"Starting federate '{args.name}' with dt={args.dt}s")

    federate = HouseFederate(args.name, args.config, args.stop_time, args.dt)
    try:
        federate.create_federate()
        federate.run_cosim_loop()
    except Exception as e:
        logger.error(f"An error occurred in {args.name}: {e}", exc_info=True)
    finally:
        if federate.hfed is not None:
            federate.destroy_federate()
        h.helicsCloseLibrary()


if __name__ == "__main__":
    main()
