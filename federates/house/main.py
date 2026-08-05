"""House federate entry point: simulates one building and publishes its grid exchange.

Usage:
    python house/main.py --scenario TestGrid_20260804_120000 --federate_name house_4
"""

from dataclasses import asdict, is_dataclass
import logging
from typing import Any, Dict
import numpy as np
import json
import argparse
import os

from cosim_toolbox.sims import Federate
from energysim.core.shared.data_structs import (
    SystemActions,
    ThermalConfig,
    SystemState,
)
from energysim.sim.simulator import JAXSimulator
from energysim.core.data.dataset import SimulationDataset
from house.common_config import create_common_configs
from house.build_my_house import create_2_room_house
from house.exogenous_data import (
    prepare_aligned_timeseries,
    write_exogenous_csv_from_metadata,
)
import jax.numpy as jnp


logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class NumpyJSONEncoder(json.JSONEncoder):
    """JSON encoder for the NumPy/JAX types EnergySim returns."""

    def default(self, obj):
        """Convert an array to a list and a NumPy/JAX scalar to a float or int."""
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
    """Reduce the simulator state to the fields the HEMS reads off the `state` topic."""
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
    """Flatten one row of the exogenous dataset into a dict for the published record."""
    if is_dataclass(exo) and not isinstance(exo, type):
        return asdict(exo)
    if hasattr(exo, "__dict__"):
        return dict(exo.__dict__)
    return {}


def setup_simulator(config_path: str, dt_seconds: int) -> tuple:
    """Build the EnergySim simulator for this house and report its room count."""
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
    """Turn a HEMS control message into simulator actions, idling whatever it omits."""
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
    ):
        """Record the config path; the simulator is built in `create_federate`."""
        super().__init__(federate_name)
        self.config_path = config_path
        self.simulator: JAXSimulator | None = None
        self.dataset: SimulationDataset | None = None
        self.n_rooms = 2
        self.t_config: ThermalConfig | None = None
        self.action_key = ""
        self.load_key = ""
        self.reactive_power_key = ""
        self.state_key = ""

    def create_federate(
        self,
        scenario_name: str = "",
        use_meta_db: str = "mongo",
        use_data_db: str = "postgres",
    ) -> None:
        """Create the CST federate and register HELICS interfaces."""
        super().create_federate(scenario_name, use_meta_db, use_data_db)

        dt_seconds = int(self.period)
        if dt_seconds <= 0:
            raise ValueError(f"dt_seconds (period) must be > 0, got {dt_seconds}")

        self.simulator, self.n_rooms, self.t_config = setup_simulator(
            self.config_path, dt_seconds
        )

        exogenous_metadata = self.metadata_manager.read(
            "custom_metadata", self.federate_name
        )
        csv_text = (exogenous_metadata or {}).get("exogenous_data_csv")
        if not csv_text:
            raise ValueError(
                f"No exogenous dataset found in metadata store for "
                f"'{self.federate_name}'. Ensure composegen ran with a valid "
                f"'exogenous_data' field for this house."
            )

        exogenous_csv_path = write_exogenous_csv_from_metadata(
            self.federate_name, csv_text
        )
        dataset_info = prepare_aligned_timeseries(
            exogenous_csv_path,
            dt_seconds,
        )
        logger.info(
            f"[{self.federate_name}] Using exogenous dataset {dataset_info.path} "
            f"(source_dt={dataset_info.source_dt_seconds}s, "
            f"target_dt={dataset_info.target_dt_seconds}s, "
            f"rows={dataset_info.source_rows}->{dataset_info.aligned_rows})."
        )
        self.dataset = SimulationDataset(dataset_info.path, dt_seconds)
        self.max_steps = len(self.dataset)

        self.load_key = self._find_pub_key("active_power")
        self.reactive_power_key = self._find_pub_key("reactive_power")
        self.state_key = self._find_pub_key("state")
        self.action_key = self._find_sub_key("control")

        logger.info(f"Federate '{self.federate_name}' subscribing to:")
        logger.info(f"  - {self.action_key}")
        logger.info(f"Federate '{self.federate_name}' publishing to:")
        logger.info(f"  - {self.load_key}")
        logger.info(f"  - {self.reactive_power_key}")
        logger.info(f"  - {self.state_key}")
        logger.info(
            f"[{self.federate_name}] Running with dt={dt_seconds}s "
            f"(stop_time={self.stop_time}s, dataset_len={len(self.dataset)})."
        )

    def _find_pub_key(self, suffix: str) -> str:
        """Find the registered publication key ending in ``suffix``.

        Keys are discovered from ``self.data_to_federation`` (populated by
        the base class from the CST federation config) rather than guessed,
        since composegen derives the actual HELICS key names from the
        experiment tree rather than the federate's own name/index.
        """
        matches = [
            key
            for key in self.data_to_federation["publications"]
            if key == suffix or key.endswith(f"/{suffix}")
        ]
        if not matches:
            raise ValueError(
                f"No publication ending in '{suffix}' found for federate "
                f"'{self.federate_name}'. Registered publications: "
                f"{list(self.data_to_federation['publications'])}"
            )
        if len(matches) > 1:
            raise ValueError(
                f"Multiple publications ending in '{suffix}' found for "
                f"federate '{self.federate_name}': {matches}"
            )
        return matches[0]

    def _find_sub_key(self, suffix: str) -> str:
        """Find the registered subscription key ending in ``suffix``."""
        matches = [
            key
            for key in self.data_from_federation["inputs"]
            if key == suffix or key.endswith(f"/{suffix}")
        ]
        if not matches:
            raise ValueError(
                f"No subscription ending in '{suffix}' found for federate "
                f"'{self.federate_name}'. Registered subscriptions: "
                f"{list(self.data_from_federation['inputs'])}"
            )
        if len(matches) > 1:
            raise ValueError(
                f"Multiple subscriptions ending in '{suffix}' found for "
                f"federate '{self.federate_name}': {matches}"
            )
        return matches[0]

    def on_enter_executing_mode(self) -> None:
        """Reset and publish the initial t=0 state."""
        assert self.simulator is not None, "create_federate must run first"
        self.simulator.reset()
        state = self.simulator.state
        result = {
            "schema_version": 1,
            "federate": self.federate_name,
            "time": 0.0,
            "dt_s": int(self.period),
            "grid_exchange": {
                "active_power_publication": self.load_key,
                "reactive_power_publication": self.reactive_power_key,
                "total_load_w": 0.0,
                "sign_convention": "positive_consumption",
            },
            "loads_w": {
                "base": 0.0,
                "heat_pump": 0.0,
                "air_conditioner": 0.0,
                "battery": 0.0,
                "pv_generation": 0.0,
                "controllable_total": 0.0,
                "grid_total": 0.0,
            },
            "action": {},
            "state": serialize_system_state(state),
            "exogenous": {},
        }
        self.data_to_federation["publications"][self.state_key] = json.dumps(
            result, cls=NumpyJSONEncoder
        )
        self.data_to_federation["publications"][self.load_key] = 0.0
        self.data_to_federation["publications"][self.reactive_power_key] = 0.0
        self.send_data_to_federation(reset=True)

    def update_internal_model(self) -> None:
        """Advance the house simulation by one CST-controlled time step."""
        assert self.simulator is not None, "create_federate must run first"
        assert self.dataset is not None, "create_federate must run first"

        dt_seconds = int(self.period)
        step_idx = int(self.granted_time // dt_seconds)

        if step_idx < 0 or step_idx >= self.max_steps:
            return

        exo = self.dataset[step_idx]

        raw_action = get_action(
            self.data_from_federation["inputs"].get(self.action_key)
        )
        action = dict_to_system_actions(raw_action, self.n_rooms)

        self.simulator, outputs = self.simulator.step(action, exo)
        state = self.simulator.state

        hp_p_el = jnp.sum(state.heat_pump.current_electrical_w)
        ac_p_el = jnp.sum(state.air_conditioner.current_electrical_w)
        print(f"  Heat Pump Power: {hp_p_el} W, AC Power: {ac_p_el} W")

        bat_p_el = jnp.asarray(action.battery_power_w)
        pv_p_gen = jnp.asarray(outputs.pv.pv_generation_w)
        base_load_w = float(getattr(exo, "base_load_w", getattr(exo, "load", 0.0)))
        print(
            f"  Battery Power Action: {bat_p_el} W, PV Generation: {pv_p_gen} W, "
            f"Base Load: {base_load_w} W"
        )

        heat_pump_w = float(hp_p_el)
        air_conditioner_w = float(ac_p_el)
        battery_w = float(bat_p_el)
        pv_generation_w = float(pv_p_gen)
        controllable_load_w = heat_pump_w + air_conditioner_w + battery_w
        total_load_w = base_load_w + controllable_load_w - pv_generation_w

        print(
            f"Time {self.granted_time}s: Controllable Load = "
            f"{controllable_load_w:.2f} W, Total Load = {total_load_w:.2f} W"
        )

        result = {
            "schema_version": 1,
            "federate": self.federate_name,
            "time": float(self.granted_time),
            "dt_s": dt_seconds,
            "grid_exchange": {
                "active_power_publication": self.load_key,
                "reactive_power_publication": self.reactive_power_key,
                "total_load_w": total_load_w,
                "sign_convention": "positive_consumption",
            },
            "loads_w": {
                "base": base_load_w,
                "heat_pump": heat_pump_w,
                "air_conditioner": air_conditioner_w,
                "battery": battery_w,
                "pv_generation": pv_generation_w,
                "controllable_total": controllable_load_w,
                "grid_total": total_load_w,
            },
            "action": {
                "battery_power_w": battery_w,
                "heat_pump_power_w": np.array(action.heat_pump_power_w).tolist(),
                "ac_power_w": np.array(action.ac_power_w).tolist(),
                "storage_discharge_w": np.array(action.storage_discharge_w).tolist(),
            },
            "state": serialize_system_state(state),
            "exogenous": serialize_exogenous_data(exo),
        }
        self.data_to_federation["publications"][self.state_key] = json.dumps(
            result, cls=NumpyJSONEncoder
        )
        self.data_to_federation["publications"][self.load_key] = total_load_w
        self.data_to_federation["publications"][self.reactive_power_key] = 0.0


def parse_args() -> argparse.Namespace:
    """Read the scenario, federate name and config path composegen put on the CLI."""
    parser = argparse.ArgumentParser(description="House federate using CST")
    parser.add_argument(
        "--scenario",
        type=str,
        required=True,
        help="CST scenario name (e.g. TestGridScenario)",
    )
    parser.add_argument(
        "--federate_name",
        type=str,
        required=True,
        help="Federate name matching meta_store entry (e.g. house_0)",
    )
    parser.add_argument(
        "--config",
        type=str,
        default="house/config.yaml",
        help="Path to house config YAML",
    )
    args, _ = parser.parse_known_args()
    return args


def get_db_backends_from_env() -> tuple[str, str]:
    """Read DB backends from environment variables set by composegen."""
    use_meta_db = os.getenv("CST_USE_META_DB")
    use_data_db = os.getenv("CST_USE_DATA_DB")

    if not use_meta_db or not use_data_db:
        raise ValueError(
            "Missing DB backend env vars. Expected CST_USE_META_DB and "
            "CST_USE_DATA_DB from experiment general config."
        )

    return use_meta_db, use_data_db


def main(
    scenario_name: str | None = None,
    federate_name: str | None = None,
    config_path: str | None = None,
) -> None:
    """Run one house federate, taking the names from the CLI unless given."""
    if scenario_name is None:
        args = parse_args()
        scenario_name = args.scenario
        federate_name = args.federate_name
        config_path = args.config

    if scenario_name is None or federate_name is None or config_path is None:
        raise ValueError("scenario_name, federate_name and config_path are required")

    use_meta_db, use_data_db = get_db_backends_from_env()

    federate = HouseFederate(federate_name, config_path)
    federate.run(
        scenario_name,
        use_meta_db=use_meta_db,
        use_data_db=use_data_db,
    )


if __name__ == "__main__":
    main()
