"""Build a house's exogenous dataset from measured load, PV and price series."""

import pandas as pd
import numpy as np
import os
from pathlib import Path

FILE_NAME = (
    "/data/input/sample_data.csv"
    if os.path.exists("/.dockerenv")
    else "data/input/sample_data.csv"
)
DT_SECONDS = 900 # 15 minutes
TIMESTEP_HOURS = DT_SECONDS / 3600.0
STEPS_PER_DAY = 96
HEETEN_DATA_FILE = (
    Path(__file__).resolve().parents[1] / "data" / "input" / "heeten_building_df14.csv"
)
PRICE_DATA_FILE = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "input"
    / "ee_day_ahead_prices_2018_till_2020.csv"
)
WATTS_PER_KWH_PER_TIMESTEP = 1000.0 / TIMESTEP_HOURS
EUROS_PER_MWH_TO_EUROS_PER_KWH = 1.0 / 1000.0

def create_sample_data(n_days: int = 7):
    """Write `n_days` of 15-minute exogenous data, tiling the measured series to fit."""
    n_steps = n_days * STEPS_PER_DAY
    print(f"Generating {n_steps} steps of sample data...")
    
    t = np.linspace(0, n_days * 2 * np.pi, n_steps)
    heeten_df = pd.read_csv(HEETEN_DATA_FILE, usecols=["load", "pv"])
    price_df = pd.read_csv(PRICE_DATA_FILE, usecols=["germany"])
    # Heeten load and pv are kWh per timestep; the simulator expects average W.
    heeten_load_w = np.resize(heeten_df["load"].to_numpy(dtype=float), n_steps) * WATTS_PER_KWH_PER_TIMESTEP
    heeten_pv_w = np.resize(heeten_df["pv"].to_numpy(dtype=float), n_steps) * WATTS_PER_KWH_PER_TIMESTEP
    germany_price_eur_per_kwh = np.resize(price_df["germany"].to_numpy(dtype=float), n_steps) * EUROS_PER_MWH_TO_EUROS_PER_KWH
    
    # Synthetic Data
    df = pd.DataFrame({
        "timestamp": pd.date_range(start="2024-01-01", periods=n_steps, freq="15min"),
        "ambient_temp": 10 + 10 * np.sin(t),
        "solar_irradiance_w_m2": heeten_pv_w,
        "price": germany_price_eur_per_kwh,
        "load": heeten_load_w,
        "internal_gains_w": np.maximum(0, 150 * np.sin(t + np.pi*1.5)),
        "solar_gains_w": np.maximum(0, 1000 * np.sin(t)),
        "wind_speed_m_s": np.abs(2 * np.sin(t))
    })
    
    os.makedirs("examples", exist_ok=True)
    df.to_csv(FILE_NAME, index=False)
    return df

if __name__ == "__main__":
    create_sample_data()
