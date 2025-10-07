import logging
from typing import Dict, Any, Tuple
import yaml
from dacite import from_dict, Config
from enum import Enum
import gymnasium as gym
import numpy as np
import json

# Assuming 'energysim' is an installed library
from energysim.rl.factory import EnvironmentFactory, EnvironmentConfig

import helics as h

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# (Your YAML string remains the same, so it's omitted here for brevity)
yaml_str = """
components:
  battery_1:
    type: battery
    model:
      type: simple
      efficiency: 0.9
      max_power: 10.0
      capacity: 20.0
      init_soc: 0.5
      deadband: 0.1
    sensor:
      observe_electrical_soc: true
      observe_thermal_soc: false
      observe_electrical_flow: true
      observe_heating_flow: false
      observe_cooling_flow: false
      soc_noise_std: 0.01
      flow_noise_std: 0.1
    actuator:
      type: pi
      space:
        action:
          type: continuous
          lower_bound: -1.0
          upper_bound: 1.0

  battery_2:
    type: battery
    model:
      type: degrading
      efficiency: 1.0
      max_power: 1.0
      capacity: 1.0
      init_soc: 0.0
      deadband: 0.0
      degradation_mode: "linear"
      degradation_rate: 0.001
      min_capacity_fraction: 0.5
      poly_exponent: 2.0     
    sensor:
      observe_electrical_soc: true
      observe_thermal_soc: false
      observe_electrical_flow: true
      observe_heating_flow: false
      observe_cooling_flow: false
      soc_noise_std: 0.01
      flow_noise_std: 0.1
    actuator:
      type: simple
      space:
        action:
          type: discrete
          n_actions: 3           
    config:
      efficiency: 0.95
      max_power: 5.0
      capacity: 15.0
      init_soc: 0.3
      deadband: 0.05

  battery_4:
    type: battery
    model:
      type: simple
      efficiency: 0.9
      max_power: 10.0
      capacity: 20.0
      init_soc: 0.5
      deadband: 0.1
    sensor:
      observe_electrical_soc: true
      observe_thermal_soc: false
      observe_electrical_flow: true
      observe_heating_flow: false
      observe_cooling_flow: false
      soc_noise_std: 0.01
      flow_noise_std: 0.1
    actuator:
      type: simple
      space:
        action:
          type: discrete
          n_actions: 3
    config:
      efficiency: 0.95
      max_power: 5.0
      capacity: 15.0
      init_soc: 0.3
      deadband: 0.05

thermal_sensor:
  observe_indoor_temp: true
  observe_temp_error: false
  observe_comfort_violation: false
  observe_zone_temps: false
  temp_noise_std: 0.0

thermal_model:
  thermal_model_type: simple_air
  params:
    building_volume: 300.0

dataset:
  data_source:
    type: file
    file_path: dataset.csv
    time_column: time
  params:
    feature_columns: 
      price: price_$
      pv: pv_kW
      load: load_kW
    dt_seconds: 900
    use_time_features: true

reward_manager:
  rewards:
    energy_cost:
      weight: -1.0
    comfort_violation:
      weight: -10.0
    battery_degradation:
      weight: -0.1
      
params:
  random_seed: 42
"""

class NumpyJSONEncoder(json.JSONEncoder):
    """
    A JSON encoder that can handle NumPy data types.
    Converts numpy arrays to lists, and numpy floats/ints to python native types.
    """
    def default(self, obj):
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        if isinstance(obj, (np.float_, np.float16, np.float32, np.float64)):
            return float(obj)
        if isinstance(obj, (np.int_, np.intc, np.intp, np.int8, np.int16, np.int32, np.int64)):
            return int(obj)
        return super().default(obj)


# Unchanged helper function for publications
def create_helics_pubs_from_gym_space(
    fed: h.HelicsFederate, space: gym.Space, prefix: str = ""
) -> Dict[str, h.HelicsPublication]:
    pubs = {}
    if isinstance(space, gym.spaces.Dict):
        for key, subspace in space.spaces.items():
            pubs.update(create_helics_pubs_from_gym_space(fed, subspace, prefix=f"{prefix}{key}."))
    elif isinstance(space, (gym.spaces.Box, gym.spaces.Discrete)):
        clean_name = prefix.rstrip(".")
        if isinstance(space, gym.spaces.Box):
            helics_type = h.HELICS_DATA_TYPE_DOUBLE
        else: # Discrete
            helics_type = h.HELICS_DATA_TYPE_INT
        pubs[clean_name] = h.helicsFederateRegisterGlobalPublication(fed, clean_name, helics_type, "")
    else:
        raise ValueError(f"Unsupported Gym space type: {type(space)}")
    return pubs

# <<< CHANGE: This function now also returns the determined data type
def create_helics_subs_from_gym_space(
    fed: h.HelicsFederate, space: gym.Space, prefix: str = ""
) -> Dict[str, Tuple[Any, int]]:
    """
    Creates HELICS subscriptions from a Gym space and returns them along with their data type.
    Returns:
        A dictionary mapping topic names to a tuple of (subscription_object, helics_data_type).
    """
    subs = {}
    if isinstance(space, gym.spaces.Dict):
        for key, subspace in space.spaces.items():
            subs.update(create_helics_subs_from_gym_space(fed, subspace, prefix=f"{prefix}{key}."))
    elif isinstance(space, (gym.spaces.Box, gym.spaces.Discrete)):
        clean_name = prefix.rstrip(".")
        if isinstance(space, gym.spaces.Box):
            helics_type = h.HELICS_DATA_TYPE_DOUBLE
        else: # Discrete
            helics_type = h.HELICS_DATA_TYPE_INT
        
        sub_obj = h.helicsFederateRegisterSubscription(fed, clean_name, "")
        subs[clean_name] = (sub_obj, helics_type) # Store as a tuple
    else:
        raise ValueError(f"Unsupported Gym space type: {type(space)}")
    return subs


def setup_environment_and_federate() -> tuple:
    logging.info("Loading environment configuration from YAML.")
    yaml_cfg = yaml.safe_load(yaml_str)
    env_config = from_dict(data_class=EnvironmentConfig, data=yaml_cfg, config=Config(cast=[Enum]))
    logging.info("Environment configuration loaded.")

    logging.info("Creating environment.")
    environment = EnvironmentFactory.create_environment(env_config)
    obs, info = environment.reset()
    logging.info("Environment created and reset successfully.")

    logging.info("Creating HELICS federate.")
    fed_info = h.helicsCreateFederateInfo()
    h.helicsFederateInfoSetCoreTypeFromString(fed_info, "zmq")
    h.helicsFederateInfoSetIntegerProperty(fed_info, h.helics_property_int_log_level, h.helics_log_level_debug)
    h.helicsFederateInfoSetFlagOption(fed_info, h.helics_flag_uninterruptible, True)
    time_delta = env_config.dataset.params.dt_seconds
    h.helicsFederateInfoSetTimeProperty(fed_info, h.helics_property_time_delta, time_delta)
    fed = h.helicsCreateValueFederate("building_environment", fed_info)
    logging.info("HELICS federate 'building_environment' created.")

    logging.info("Mapping action space to HELICS subscriptions.")
    action_subs = create_helics_subs_from_gym_space(fed, environment.action_space, prefix="action.")
    logging.info(f"Action subscriptions created: {list(action_subs.keys())}")

    logging.info("Mapping observation space to HELICS publications.")
    obs_pubs = create_helics_pubs_from_gym_space(fed, environment.observation_space, prefix="obs.")
    logging.info(f"Observation publications created: {list(obs_pubs.keys())}")

    reward_pub = h.helicsFederateRegisterGlobalPublication(fed, "reward", h.HELICS_DATA_TYPE_DOUBLE, "")
    done_pub = h.helicsFederateRegisterGlobalPublication(fed, "done", h.HELICS_DATA_TYPE_BOOLEAN, "")
    logging.info("Registered 'reward' and 'done' publications.")
    
    return fed, environment, obs, action_subs, obs_pubs, reward_pub, done_pub


def publish_observation(obs_pubs: Dict, observation: Dict[str, Any]):
    for key, pub in obs_pubs.items():
        try:
            nav_keys = key.replace("obs.", "").split('.')
            value = observation
            for k in nav_keys:
                value = value[k]
            
            if isinstance(value, dict):
                value_to_publish = json.dumps(value, cls=NumpyJSONEncoder)
                h.helicsPublicationPublishString(pub, value_to_publish)
            elif isinstance(value, (np.float64, np.float32, float)):
                h.helicsPublicationPublishDouble(pub, float(value))
            elif isinstance(value, (np.int64, np.int32, int)):
                h.helicsPublicationPublishInteger(pub, int(value))
            # Add other types as needed
        except KeyError:
            # This is now a debug message, not an error, as not all obs may be present
            logging.debug(f"Key path '{key}' not found in observation dict. Skipping publication.")
        except Exception as e:
            logging.error(f"Error publishing for key '{key}': {e}")



# <<< CHANGE: get_action is now fully dynamic and robust
def get_action(action_subs: dict) -> dict:
    """Retrieve actions from HELICS subscriptions using stored type information."""
    action_dict = {}
    # Unpack the tuple of (subscription_object, helics_data_type)
    for key, (sub, sub_type) in action_subs.items():
        nav_keys = key.replace("action.", "").split('.')
        temp_dict = action_dict
        for k in nav_keys[:-1]:
            temp_dict = temp_dict.setdefault(k, {})
        
        if not h.helicsInputIsUpdated(sub):
            logging.warning(f"Subscription '{key}' was not updated for this time step.")
            continue
        
        # Use the stored type to call the correct getter function
        if sub_type == h.HELICS_DATA_TYPE_DOUBLE:
            val = h.helicsInputGetDouble(sub)
        elif sub_type == h.HELICS_DATA_TYPE_INT:
            val = h.helicsInputGetInteger(sub)
        elif sub_type == h.HELICS_DATA_TYPE_BOOLEAN:
            val = h.helicsInputGetBoolean(sub)
        else: # Default to string for any other types
            val = h.helicsInputGetString(sub)
        
        temp_dict[nav_keys[-1]] = val
    return action_dict

def run_federate(fed, env, initial_obs, action_subs, obs_pubs, reward_pub, done_pub, stop_time):
    logging.info("Entering HELICS execution mode.")
    h.helicsFederateEnterExecutingMode(fed)

    # Publish the initial state of the environment for t=0
    publish_observation(obs_pubs, initial_obs)
    
    # Immediately request the first time step. This is the key to avoiding the t=0 race condition.
    # The federate will now wait here until the broker grants it t=900s.
    current_time = h.helicsFederateRequestNextStep(fed)
    logging.info(f"First time step granted: {current_time}s")

    while current_time < stop_time:
        # At t=900s, get the action the agent published at t=0s. It will be waiting.
        action = get_action(action_subs)
        if not action or any(not d for d in action.values()):
            logging.error(f"Invalid or incomplete action received: {action}. Terminating.")
            break
        logging.info(f"Time: {current_time}s | Received action: {action}")
        
        # Step the environment
        obs, reward, terminated, truncated, info = env.step(action)
        done = terminated or truncated
        logging.info(f"Step Result -> Reward: {reward:.4f}, Done: {done}")
        
        # Publish results for the *next* time step
        publish_observation(obs_pubs, obs)
        h.helicsPublicationPublishDouble(reward_pub, float(reward))
        h.helicsPublicationPublishBoolean(done_pub, done)
        
        if done:
            logging.info(f"Episode finished at time {current_time}s. Terminating.")
            break
            
        # Request the next time step
        current_time = h.helicsFederateRequestNextStep(fed)

    logging.info("Simulation finished. Finalizing federate.")
    h.helicsFederateFinalize(fed)
    h.helicsFederateFree(fed)


if __name__ == "__main__":
    STOP_TIME = 48 * 3600
    fed = None
    try:
        (
            fed,
            env,
            initial_obs,
            action_subs,
            obs_pubs,
            reward_pub,
            done_pub,
        ) = setup_environment_and_federate()
        run_federate(
            fed=fed,
            env=env,
            initial_obs=initial_obs,
            action_subs=action_subs,
            obs_pubs=obs_pubs,
            reward_pub=reward_pub,
            done_pub=done_pub,
            stop_time=STOP_TIME,
        )
    except Exception as e:
        logging.error(f"An error occurred: {e}", exc_info=True)
        if fed:
            h.helicsFederateFinalize(fed)
    finally:
        h.helicsCloseLibrary()