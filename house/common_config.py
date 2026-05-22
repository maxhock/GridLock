# common_config.py
from energysim.core.shared.data_structs import (
    BatteryConfig, RewardConfig, HeatPumpConfig, AirConditionerConfig,
    ThermalStorageConfig, PVConfig
)
from house.build_my_house import create_2_room_house
'''
def create_common_configs(dt_seconds: float):
    t_config = create_2_room_house()
    return {
        "dt_seconds": dt_seconds,
        "t_config": t_config,
        "r_config": RewardConfig(price_weight=10.0, comfort_weight=50.0),
        "b_config": BatteryConfig(),
        "hp_config": HeatPumpConfig(),
        "ac_config": AirConditionerConfig(),
        "ts_config": ThermalStorageConfig(),
        "s_config": SolarConfig(),
    }
'''
def create_common_configs(dt_seconds: float):
    t_config = create_2_room_house()
    return {
        "dt_seconds": dt_seconds,
        "t_config": t_config,
        "r_config": RewardConfig(),
        "b_config": BatteryConfig(capacity_kwh=13.0),
        "hp_config": HeatPumpConfig(model_type="ramping", max_electrical_power_w=4000.0),
        "ac_config": AirConditionerConfig(model_type="ramping", max_electrical_power_w=4000.0),
        "ts_config": ThermalStorageConfig(),
        "pv_config": PVConfig(),
    }