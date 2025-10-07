import pandas as pd
import numpy as np

num_rows = 100
dt_seconds = 900
start_time = pd.Timestamp("2025-01-01 00:00:00")

time_index = pd.date_range(start=start_time, periods=num_rows, freq=f'{dt_seconds}s')

np.random.seed(42)
price = np.random.uniform(20, 100, size=num_rows)
pv = np.random.uniform(0, 50, size=num_rows)
load = np.random.uniform(10, 100, size=num_rows)

df = pd.DataFrame({
    'time': time_index,
    'price_$': price,
    'pv_kW': pv,
    'load_kW': load
})

# Convert numeric columns to integers
df['price_$'] = df['price_$'].round().astype(int)
df['pv_kW'] = df['pv_kW'].round().astype(int)
df['load_kW'] = df['load_kW'].round().astype(int)

# Convert time column to integer (seconds since epoch)
df['time'] = df['time'].astype('int64') // 10**9

df.to_csv('dataset.csv', index=False)
print("Random dataset.csv with integer time created!")
