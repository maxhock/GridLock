# controller/main_mpc.py
import json
import logging
import argparse
import re
import jax.numpy as jnp
import equinox as eqx

from energysim.sim.simulator import JAXSimulator
from energysim.control.mpc_solver import JAX_MPC_Solver
from energysim.core.data.dataset import SimulationDataset
from energysim.core.shared.data_structs import (
     BatteryConfig, RewardConfig, HeatPumpConfig, AirConditionerConfig,
     ThermalStorageConfig, SolarConfig,
     SystemActions,
     SystemState, ThermalState, BatteryState, ThermalStorageState,
     HeatPumpState, AirConditionerState,
 )
import house.sample_data_generator
from house.common_config import create_common_configs

from house.build_my_house import create_2_room_house

import helics as h

logging.basicConfig(
     level=logging.INFO,
     format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
 )
logger = logging.getLogger(__name__)



def deserialize_system_state(state_dict: dict) -> SystemState:
     """Rekonstruiere SystemState aus dem JSON-Dict vom House-Federate."""
     thermal = ThermalState(
         T_vector=jnp.array(state_dict["thermal"]["T_vector"])
     )
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
         current_thermal_w=jnp.array(
             state_dict["heat_pump"]["current_thermal_w"]
         ),
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


 # ------------ HELICS Setup ------------

def setup_federate(federate_name: str, dt_seconds: int):
     logger.info(f"Creating HELICS federate: {federate_name}")
     fed_info = h.helicsCreateFederateInfo()
     h.helicsFederateInfoSetCoreTypeFromString(fed_info, "zmq")
     h.helicsFederateInfoSetIntegerProperty(
         fed_info,
         h.helics_property_int_log_level,
         h.helics_log_level_debug
     )
     h.helicsFederateInfoSetTimeProperty(
         fed_info,
         h.helics_property_time_delta,
         dt_seconds
     )
     fed = h.helicsCreateValueFederate(federate_name, fed_info)

     
     match = re.search(r"\d+$", federate_name)
     if not match:
         raise ValueError(
             f"Federate name '{federate_name}' must end in an index (e.g., 'controller_0')"
         )
     fed_index = match.group(0)

     # 1. Subscribe from House
     sub_house_result = h.helicsFederateRegisterSubscription(
         fed, f"house_{fed_index}/timestep_result", "string"
     )

     # 2. Publish: to House
     pub_house_action = h.helicsFederateRegisterGlobalPublication(
         fed, f"house_{fed_index}/action", h.HELICS_DATA_TYPE_STRING, ""
     )

     # 3. Publish: to Battery
     pub_battery_action = h.helicsFederateRegisterGlobalPublication(
         fed, f"battery_{fed_index}/action", h.HELICS_DATA_TYPE_STRING, ""
     )

     logger.info(f"Federate '{federate_name}' subscribing to:")
     logger.info(f"  - house_{fed_index}/timestep_result")
     logger.info(f"Federate '{federate_name}' publishing to:")
     logger.info(f"  - house_{fed_index}/action")
     logger.info(f"  - battery_{fed_index}/action")

     return fed, sub_house_result, (pub_house_action, pub_battery_action)



def setup_mpc_and_data(dt_seconds: int):
     """
     Erzeuge MPC-Solver, Dataset etc., ähnlich wie im energysim-MPC-Beispiel.
     """
     dt = dt_seconds

     # Dataset with sample data (same as House federate)
     dataset = SimulationDataset(house.sample_data_generator.FILE_NAME, dt)

     # Common Configs (same as House federate)
     configs = create_common_configs(dt_seconds=dt_seconds)

     # Thermal Config + number of rooms
     t_config = configs["t_config"]
     n_rooms = int(len(t_config.room_air_indices))


     # MPC with 4h horizon
     HORIZON = 16
     mpc = JAX_MPC_Solver(N_horizon=HORIZON, **configs)

     split_factors = jnp.array([0.6, 0.4])

     b_config: BatteryConfig = configs["b_config"]

     return mpc, dataset, t_config, n_rooms, split_factors, b_config



def run_simulation_loop(
     fed,
     sub_house_result,
     pubs,
     stop_time,
     dt_seconds,
     mpc: JAX_MPC_Solver,
     dataset: SimulationDataset,
     t_config,
     n_rooms: int,
     split_factors,
     b_config: BatteryConfig,
 ):
     pub_house_action, pub_battery_action = pubs

     h.helicsFederateEnterExecutingMode(fed)
     logger.info("Controller entering execution mode.")

     current_state: SystemState | None = None

     warm_start_actions = mpc.zonal_warm_start

     current_time = 0.0
     while current_time < stop_time:

         current_time = h.helicsFederateRequestNextStep(fed)
         logger.debug(f"Granted time: {current_time}s")

         state_str = h.helicsInputGetString(sub_house_result)
         if state_str:
             try:
                 msg = json.loads(state_str)
                 if "state" in msg:
                     current_state = deserialize_system_state(msg["state"])
                 else:
                     logger.warning(
                         f"Time {current_time}s | 'state' key missing in house result."
                     )
             except json.JSONDecodeError:
                 logger.warning(
                     f"Time {current_time}s | Could not decode house result JSON."
                 )

         if current_state is None:
             logger.info(
                 f"Time {current_time}s | No valid state yet, publishing zero actions."
             )
             house_action = {}
             battery_action = {"normalized_power": 0.0}
             h.helicsPublicationPublishString(
                 pub_house_action, json.dumps(house_action)
             )
             h.helicsPublicationPublishString(
                 pub_battery_action, json.dumps(battery_action)
             )
             continue

         step_idx = int(current_time // dt_seconds)

         H = mpc.N
         if step_idx + H >= len(dataset):
             logger.info(
                 f"Time {current_time}s | Not enough forecast horizon left, stopping MPC control."
             )
             break

         raw_forecast = dataset.get_forecast(step_idx, H)

         exo_forecast = eqx.tree_at(
             lambda e: (e.solar_gains_w, e.occupancy_gains_w, e.device_gains_w),
             raw_forecast,
             (
                 jnp.outer(raw_forecast.solar_gains_w, split_factors),
                 jnp.outer(raw_forecast.occupancy_gains_w, split_factors),
                 jnp.zeros((H, n_rooms)),
             ),
         )

         action: SystemActions = mpc.solve(
             current_state, exo_forecast, warm_start_actions=warm_start_actions
         )

         warm_start_actions = mpc.zonal_warm_start

         house_action = {
             "battery_power_w": float(action.battery_power_w)*1000.0,
             "heat_pump_power_w": action.heat_pump_power_w.tolist(),
             "ac_power_w": action.ac_power_w.tolist(),
             "storage_discharge_w": action.storage_discharge_w.tolist(),
         }

         max_p = b_config.max_power_w
         if max_p <= 0:
             norm_power = 0.0
         else:
             norm_power = float(jnp.clip((action.battery_power_w*1000) / max_p, -1.0, 1.0))

         battery_action = {"normalized_power": norm_power}

         h.helicsPublicationPublishString(
             pub_house_action, json.dumps(house_action)
         )
         h.helicsPublicationPublishString(
             pub_battery_action, json.dumps(battery_action)
         )

         logger.info(
             f"Time {current_time}s | MPC actions: "
             f"House battery_power_w={house_action['battery_power_w']:.1f}, "
             f"norm_bat={battery_action['normalized_power']:.3f}"
         )

     logger.info("Controller simulation finished. Finalizing federate.")
     h.helicsFederateFinalize(fed)
     h.helicsFederateFree(fed)


def main():
     parser = argparse.ArgumentParser(description="MPC Controller HELICS Federate")
     parser.add_argument(
         "--name", type=str, required=True,
         help="Name of the federate (e.g., controller_0)"
     )
     parser.add_argument(
         "--stop_time", type=float, default=86400,
         help="Simulation stop time in seconds"
     )
     parser.add_argument(
         "--dt", type=int, default=900,
         help="Time step in seconds"
     )

     args = parser.parse_args()
     logger.info(f"Starting MPC federate '{args.name}' with dt={args.dt}s")

     fed = None
     try:
         # HELICS
         fed, sub_house_result, pubs = setup_federate(
             federate_name=args.name,
             dt_seconds=args.dt,
         )

         # MPC / Dataset
         mpc, dataset, t_config, n_rooms, split_factors, b_config = \
             setup_mpc_and_data(args.dt)

         # Loop
         run_simulation_loop(
             fed=fed,
             sub_house_result=sub_house_result,
             pubs=pubs,
             stop_time=args.stop_time,
             dt_seconds=args.dt,
             mpc=mpc,
             dataset=dataset,
             t_config=t_config,
             n_rooms=n_rooms,
             split_factors=split_factors,
             b_config=b_config,
         )

     except Exception as e:
         logger.error(f"An error occurred in {args.name}: {e}", exc_info=True)
         if fed:
             h.helicsFederateFinalize(fed)
     finally:
         h.helicsCloseLibrary()


if __name__ == "__main__":
     main()