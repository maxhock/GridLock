"""Run the house simulator standalone, without HELICS, to check its physics."""

import jax
import jax.numpy as jnp
import pandas as pd

from energysim.sim.simulator import JAXSimulator
from energysim.core.data.dataset import SimulationDataset
from energysim.core.shared.data_structs import (
    BatteryConfig, RewardConfig, HeatPumpConfig, AirConditionerConfig, 
    ThermalStorageConfig, PVConfig, SystemActions, SystemOutputs
)
import tools.sample_data_generator as sample_data_generator
from build_my_house import create_2_room_house

def run():
    """Simulate ten days under a thermostat controller and write the history to CSV."""
    # 1. Setup Data & Config
    sample_data_generator.create_sample_data(n_days=10)
    dataset = SimulationDataset(sample_data_generator.FILE_NAME, sample_data_generator.DT_SECONDS)
    t_config = create_2_room_house()

    # Extract the full exogenous trace for the horizon as a PyTree of arrays (Time, ...)
    total_steps = len(dataset)
    full_exo_seq = dataset.get_forecast(0, total_steps)

    sim = JAXSimulator(
        dt_seconds=sample_data_generator.DT_SECONDS,
        t_config=t_config,
        r_config=RewardConfig(),
        b_config=BatteryConfig(capacity_kwh=13.0),
        hp_config=HeatPumpConfig(model_type="ramping", max_electrical_power_w=4000.0),
        ac_config=AirConditionerConfig(model_type="ramping", max_electrical_power_w=4000.0),
        ts_config=ThermalStorageConfig(),
        pv_config=PVConfig()
    )

    # Reset returns the fresh simulator instance and the initial empty actions
    initial_sim = sim.reset()
    
    # Pre-allocate indices to avoid creating arrays inside the fast loop
    room_indices = jnp.array(t_config.room_air_indices)

    # 2. Define the Compiled Scan Loop
    @jax.jit
    def run_simulation(sim_carry, exo_sequence):
        """Compile the whole run into one JAX scan over the exogenous trace."""

        def scan_step(carry, exo_step):
            """Advance one step: thermostat decides, simulator steps."""
            (current_sim, )  = carry
            assert isinstance(current_sim, JAXSimulator)

            # --- Controller ---
            temps = current_sim.thermal.T_vector[room_indices]
            hp_w = jnp.where(temps < 20.0, 2000.0, 0.0)
            ac_w = jnp.where(temps > 23.0, 4000.0, 0.0)
            
            action = SystemActions(
                battery_power_w=jnp.array(0.0),
                heat_pump_power_w=hp_w,
                ac_power_w=ac_w,
                storage_discharge_w=hp_w * 3.0
            )
            
            # --- Step Simulator ---
            next_sim, outputs = current_sim.step(action, exo_step)
            
            # --- Output Metrics ---
            # Pack a flat dictionary here. JAX will stack these into arrays of shape (Time, ...)
            assert isinstance(outputs, SystemOutputs)
            step_metrics = {
                "temp_room_0": temps[0],
                "temp_room_1": temps[1],
                "pv_generation_w": outputs.pv.pv_generation_w,
                "hp_elec_w": jnp.sum(outputs.hp.electrical_power_w),
                "ac_elec_w": jnp.sum(outputs.ac.electrical_power_w),
                "battery_soc": current_sim.battery.soc,
            }
            
            return (next_sim,), step_metrics

        # Run the scan over the leading time dimension of exo_sequence
        final_carry, history = jax.lax.scan(scan_step, (sim_carry, ), exo_sequence)
        
        return history

    print("Compiling and Running Simulation...")
    
    # 3. Execute
    # The first time this runs, JAX translates the entire 10-day loop to C++/CUDA.
    history = run_simulation(initial_sim, full_exo_seq)

    # 4. Process Results
    df = pd.DataFrame(history)
    
    df.to_csv("simulation_history.csv", index=False)
    print(df.head())

if __name__ == "__main__":
    run()