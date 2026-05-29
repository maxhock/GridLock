import jax.numpy as jnp
from energysim.core.network_builder import RCNetworkBuilder

def create_2_room_house():
    """
    Creates a simple 2-room house configuration (Living Room + Bedroom).
    """
    # 1. Initialize builder
    builder = RCNetworkBuilder(n_rooms=2)

    # 2. Add nodes (Capacities in J/K)
    # Living Room (Zone 0)
    builder.add_node("room_air_0", capacity_j_k=5.0e5) 
    builder.add_node("wall_0", capacity_j_k=1.0e7)
    
    # Bedroom (Zone 1)
    builder.add_node("room_air_1", capacity_j_k=3.0e5)
    builder.add_node("wall_1", capacity_j_k=0.8e7)
    
    # Shared Wall
    builder.add_node("shared_wall", capacity_j_k=0.5e7)

    # 3. Add connections (Resistances in K/W)
    # Living Room Connectivity
    builder.add_resistor("wall_0", "ambient", R_k_w=2.0)
    builder.add_resistor("room_air_0", "wall_0", R_k_w=1.0)
    builder.add_resistor("room_air_0", "ambient", R_k_w=4.0) # Ventilation

    # Bedroom Connectivity
    builder.add_resistor("wall_1", "ambient", R_k_w=2.0)
    builder.add_resistor("room_air_1", "wall_1", R_k_w=1.0)
    builder.add_resistor("room_air_1", "ambient", R_k_w=4.0)

    # Inter-zone coupling
    builder.add_resistor("room_air_0", "shared_wall", R_k_w=1.6)
    builder.add_resistor("room_air_1", "shared_wall", R_k_w=1.6)

    # 4. Map Inputs (HVAC & Gains)
    # Map heating/cooling actions to specific rooms
    for i in range(2):
        builder.add_input_mapping("heating_w", f"room_air_{i}", room_index=i)
        builder.add_input_mapping("cooling_w", f"room_air_{i}", room_index=i)
        builder.add_input_mapping("occupancy_gains_w", f"room_air_{i}", room_index=i)
        builder.add_input_mapping("device_gains_w", f"room_air_{i}", room_index=i)
    
    # Solar hits walls mostly (70%), air slightly (30%)
    builder.add_input_mapping("solar_gains_w", "wall_0", room_index=0, fraction=0.7)
    builder.add_input_mapping("solar_gains_w", "room_air_0", room_index=0, fraction=0.3)
    builder.add_input_mapping("solar_gains_w", "wall_1", room_index=1, fraction=1.0)

    return builder.compile()