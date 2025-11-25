import pandas as pd
from pathlib import Path

print("Processing helics_recorder TSV log...")

# Load file
df = pd.read_csv(
    Path("/app/logger_raw.log"),
    sep="\t",
    header=None,
    names=["time", "empty", "tag", "type", "value"],
    comment="#",
    # dtype={"time": float, "tag": str, "type": str, "value": float},
    keep_default_na=False,
)

# realign values and remove empty columns
df.loc[df["value"] == "", "value"] = df.loc[df["value"] == "", "type"]
df.drop(columns=["empty", "type"], inplace=True)
df["value"] = pd.to_numeric(df["value"])


# Reshape to have tags as columns
df = df.set_index(["time", "tag"])
df = df.unstack(level="tag")
df.columns = [col[-1] for col in df.columns]
df = df.replace("", pd.NA)


# Ensure output directory exists and save to CSV
out_csv = Path("/data/output/federation.csv")
out_csv.parent.mkdir(parents=True, exist_ok=True)
df.to_csv(out_csv, index=True)
print(f"Saved processed CSV log to {out_csv}")

# Optional: also save HDF5 (commented out)
df.to_hdf("/data/output/federation.h5", key="df", mode="w")
print("Saved processed HDF5 log to /data/output/federation.h5")
