# controller/main.py
import json
import logging
import argparse
import re
import os

from cosim_toolbox.sims import Federate
import helics as h
import jax.numpy as jnp
import equinox as eqx

from energysim.sim.simulator import JAXSimulator
from energysim.control.mpc_solver import JAX_MPC_Solver
from energysim.core.data.dataset import SimulationDataset
from energysim.core.shared.data_structs import (
    BatteryConfig,
    RewardConfig,
    HeatPumpConfig,
    AirConditionerConfig,
    ThermalStorageConfig,
    PVConfig,
    ThermalConfig,
    SystemActions,
    SystemState,
    ThermalState,
    BatteryState,
    ThermalStorageState,
    HeatPumpState,
    AirConditionerState,
)
import tools.sample_data_generator
from house.common_config import create_common_configs
from house.build_my_house import create_2_room_house
from house.exogenous_data import prepare_aligned_timeseries

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def deserialize_system_state(state_dict: dict) -> SystemState:
    """Reconstruct SystemState from the JSON dict published by the house federate."""
    thermal = ThermalState(T_vector=jnp.array(state_dict["thermal"]["T_vector"]))
    battery = BatteryState(
        soc=jnp.array(state_dict["battery"]["soc"]),
        soh=jnp.array(state_dict["battery"]["soh"]),
    )
    storage = ThermalStorageState(
        temperatures_c=jnp.array(state_dict["storage"]["temperatures_c"])
    )
    hp = HeatPumpState(
        current_electrical_w=jnp.array(
            state_dict["heat_pump"]["current_electrical_w"]
        ),
        current_thermal_w=jnp.array(state_dict["heat_pump"]["current_thermal_w"]),
    )
    ac = AirConditionerState(
        current_electrical_w=jnp.array(
            state_dict["air_conditioner"]["current_electrical_w"]
        ),
        current_thermal_w=jnp.array(
            state_dict["air_conditioner"]["current_thermal_w"]
        ),
    )
    return SystemState(
        thermal=thermal,
        battery=battery,
        storage=storage,
        heat_pump=hp,
        air_conditioner=ac,
    )


def build_simulator_from_state(
    template: JAXSimulator, state: SystemState
) -> JAXSimulator:
    """Return a simulator template with its internal state replaced."""
    sim = template

    sim = eqx.tree_at(
        lambda s: s.battery,
        sim,
        eqx.tree_at(
            lambda b: (b.soc, b.soh),
            sim.battery,
            (state.battery.soc, state.battery.soh),
        ),
    )

    sim = eqx.tree_at(
        lambda s: s.thermal,
        sim,
        eqx.tree_at(
            lambda t: t.T_vector,
            sim.thermal,
            state.thermal.T_vector,
        ),
    )

    sim = eqx.tree_at(
        lambda s: s.storage,
        sim,
        eqx.tree_at(
            lambda st: st.temperatures_c,
            sim.storage,
            state.storage.temperatures_c,
        ),
    )

    sim = eqx.tree_at(
        lambda s: s.heat_pump,
        sim,
        eqx.tree_at(
            lambda hp: (hp.current_electrical_w, hp.current_thermal_w),
            sim.heat_pump,
            (
                state.heat_pump.current_electrical_w,
                state.heat_pump.current_thermal_w,
            ),
        ),
    )

    sim = eqx.tree_at(
        lambda s: s.ac,
        sim,
        eqx.tree_at(
            lambda ac: (ac.current_electrical_w, ac.current_thermal_w),
            sim.ac,
            (
                state.air_conditioner.current_electrical_w,
                state.air_conditioner.current_thermal_w,
            ),
        ),
    )

    return sim


def setup_mpc_and_data(dt_seconds: int):
    """Setup MPC solver and aligned exogenous data."""
    dataset_info = prepare_aligned_timeseries(
        tools.sample_data_generator.FILE_NAME,
        dt_seconds,
    )
    logger.info(
        f"Using exogenous dataset {dataset_info.path} "
        f"(source_dt={dataset_info.source_dt_seconds}s, "
        f"target_dt={dataset_info.target_dt_seconds}s, "
        f"rows={dataset_info.source_rows}->{dataset_info.aligned_rows})."
    )
    dataset = SimulationDataset(dataset_info.path, dt_seconds)

    configs = create_common_configs(dt_seconds=dt_seconds)
    t_config = configs["t_config"]
    n_rooms = int(len(t_config.room_air_indices))

    sim_template = JAXSimulator(
        dt_seconds=dt_seconds,
        t_config=configs["t_config"],
        r_config=configs["r_config"],
        b_config=configs["b_config"],
        hp_config=configs["hp_config"],
        ac_config=configs["ac_config"],
        ts_config=configs["ts_config"],
        pv_config=configs["pv_config"],
    )

    mpc = JAX_MPC_Solver(N_horizon=24, simulator_template=sim_template)
    split_factors = jnp.array([0.6, 0.4])
    b_config: BatteryConfig = configs["b_config"]

    return mpc, dataset, t_config, n_rooms, split_factors, b_config, sim_template


class ControllerFederate(Federate):
    """Controller federate using CST for lifecycle/time management."""

    def __init__(self, federate_name: str, stop_time: float, dt_seconds: int):
        super().__init__(federate_name)
        self.requested_stop_time = float(stop_time)
        self.dt_seconds = int(dt_seconds)
        self.house_result_key = ""
        self.house_action_key = ""
        self.battery_action_key = ""
        self.current_state: SystemState | None = None
        self.mpc: JAX_MPC_Solver | None = None
        self.dataset: SimulationDataset | None = None
        self.t_config = None
        self.n_rooms = 0
        self.split_factors = None
        self.b_config: BatteryConfig | None = None
        self.sim_template: JAXSimulator | None = None

    def create_federate(self):
        """Create the CST federate and register HELICS interfaces."""
        if self.dt_seconds <= 0:
            raise ValueError(f"dt_seconds must be > 0, got {self.dt_seconds}")

        self.federate_type = "value"
        self.period = float(self.dt_seconds)
        self.stop_time = float(self.requested_stop_time)
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
                "(e.g., 'controller_0')"
            )
        fed_index = match.group(0)

        self.house_result_key = f"house_{fed_index}/timestep_result"
        self.house_action_key = f"house_{fed_index}/action"
        self.battery_action_key = f"battery_{fed_index}/action"

        h.helicsFederateRegisterSubscription(
            self.hfed, self.house_result_key, "string"
        )
        self.inputs[self.house_result_key] = {
            "type": "string",
            "key": self.house_result_key,
        }
        self.data_from_federation["inputs"][self.house_result_key] = None

        h.helicsFederateRegisterGlobalPublication(
            self.hfed, self.house_action_key, h.HELICS_DATA_TYPE_STRING, ""
        )
        self.pubs[self.house_action_key] = {
            "type": "string",
            "key": self.house_action_key,
        }
        self.data_to_federation["publications"][self.house_action_key] = None

        h.helicsFederateRegisterGlobalPublication(
            self.hfed, self.battery_action_key, h.HELICS_DATA_TYPE_STRING, ""
        )
        self.pubs[self.battery_action_key] = {
            "type": "string",
            "key": self.battery_action_key,
        }
        self.data_to_federation["publications"][self.battery_action_key] = None

        logger.info(f"Federate '{self.federate_name}' subscribing to:")
        logger.info(f"  - {self.house_result_key}")
        logger.info(f"Federate '{self.federate_name}' publishing to:")
        logger.info(f"  - {self.house_action_key}")
        logger.info(f"  - {self.battery_action_key}")

        (
            self.mpc,
            self.dataset,
            self.t_config,
            self.n_rooms,
            self.split_factors,
            self.b_config,
            self.sim_template,
        ) = setup_mpc_and_data(self.dt_seconds)

    def update_internal_model(self):
        """Advance the controller logic by one CST-controlled time step."""
        state_str = self.data_from_federation["inputs"].get(self.house_result_key)
        if state_str and not isinstance(state_str, list):
            try:
                msg = json.loads(state_str)
                if isinstance(msg, dict) and "state" in msg:
                    self.current_state = deserialize_system_state(msg["state"])
                elif not isinstance(msg, dict):
                    logger.debug(
                        f"Time {self.granted_time}s | Ignoring non-dict house result: {msg}"
                    )
                else:
                    logger.warning(
                        f"Time {self.granted_time}s | 'state' key missing in house result."
                    )
            except json.JSONDecodeError:
                logger.warning(
                    f"Time {self.granted_time}s | Could not decode house result JSON."
                )

        if self.current_state is None:
            logger.info(
                f"Time {self.granted_time}s | No valid state yet, publishing zero actions."
            )
            self.data_to_federation["publications"][self.house_action_key] = json.dumps(
                {}
            )
            self.data_to_federation["publications"][
                self.battery_action_key
            ] = json.dumps({"normalized_power": 0.0})

            return

        current_sim = build_simulator_from_state(self.sim_template, self.current_state)

        step_idx = int(self.granted_time // self.dt_seconds)
        horizon = self.mpc.N
        if step_idx + horizon >= len(self.dataset):
            logger.info(
                f"Time {self.granted_time}s | Not enough forecast horizon left, "
                "stopping MPC control."
            )
            self.stop_time = self.granted_time
            return

        exo_forecast = self.dataset.get_forecast(step_idx, horizon)
        action: SystemActions = self.mpc.solve(
            current_sim,
            exo_forecast,
        )

        requested_battery_power_w = float(action.battery_power_w)
        current_soc = float(self.current_state.battery.soc)
        max_p = float(self.b_config.max_power_w)

        house_action = {
            "battery_power_w": requested_battery_power_w,
            "heat_pump_power_w": action.heat_pump_power_w.tolist(),
            "ac_power_w": action.ac_power_w.tolist(),
            "storage_discharge_w": action.storage_discharge_w.tolist(),
        }

        if max_p <= 0:
            norm_power = 0.0
        else:
            norm_power = requested_battery_power_w / max_p

        battery_action = {"normalized_power": norm_power}

        self.data_to_federation["publications"][self.house_action_key] = json.dumps(
            house_action
        )
        self.data_to_federation["publications"][self.battery_action_key] = json.dumps(
            battery_action
        )

        logger.info(
            f"Time {self.granted_time}s | MPC actions: "
            f"raw_bat={requested_battery_power_w:.1f}, "
            f"House battery_power_w={house_action['battery_power_w']:.1f}, "
            f"soc={current_soc:.3f}, "
            f"norm_bat={battery_action['normalized_power']:.3f}"
        )


def main():
    parser = argparse.ArgumentParser(description="MPC Controller HELICS Federate")
    parser.add_argument(
        "--name",
        type=str,
        required=True,
        help="Name of the federate (e.g., controller_0)",
    )
    parser.add_argument(
        "--stop_time",
        type=float,
        default=86400,
        help="Simulation stop time in seconds",
    )
    parser.add_argument("--dt", type=int, default=900, help="Time step in seconds")

    args = parser.parse_args()
    logger.info(f"Starting MPC federate '{args.name}' with dt={args.dt}s")

    federate = ControllerFederate(args.name, args.stop_time, args.dt)
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
