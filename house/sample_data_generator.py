import pandas as pd
import numpy as np
import os

FILE_NAME = "examples/sample_data.csv"
DT_SECONDS = 900 # 15 minutes
STEPS_PER_DAY = 96

def create_sample_data(n_days: int = 7):
    n_steps = n_days * STEPS_PER_DAY
    print(f"Generating {n_steps} steps of sample data...")
    
    t = np.linspace(0, n_days * 2 * np.pi, n_steps)
    
    # Synthetic Data
    df = pd.DataFrame({
        "timestamp": pd.date_range(start="2024-01-01", periods=n_steps, freq="15min"),
        "ambient_temp": 10 + 10 * np.sin(t),
        "solar_irradiance_w_m2": np.maximum(0, 800 * np.sin(t)),
        "price": np.maximum(0.05, 0.20 + 0.15 * np.sin(t + np.pi/2) + np.random.rand(n_steps)*0.05),
        "load": np.maximum(200, 500 + 300 * np.sin(t + np.pi*1.5)),
        "internal_gains_w": np.maximum(0, 150 * np.sin(t + np.pi*1.5)),
        "solar_gains_w": np.maximum(0, 1000 * np.sin(t)),
        "wind_speed_m_s": np.abs(2 * np.sin(t))
    })
    
    os.makedirs("examples", exist_ok=True)
    df.to_csv(FILE_NAME, index=False)
    return df

if __name__ == "__main__":
    create_sample_data()