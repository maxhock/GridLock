import os
import sys
import subprocess
import shutil
from pathlib import Path
from omegaconf import OmegaConf
import pandapower as pp
import h5py

# Define paths
BASE_DIR = Path("/app")
GRIDREADOUT_DIR = BASE_DIR / "gridreadout"
GRIDALLOC_DIR = BASE_DIR / "gridalloc"
DATA_INPUT_DIR = Path("/data/input")
CONFIG_PATH = Path("/config/experiment.yml")


def main():
    # 1. Read Config
    if not CONFIG_PATH.exists():
        print(f"Config file not found at {CONFIG_PATH}")
        sys.exit(1)

    conf = OmegaConf.load(CONFIG_PATH)
    dg_conf = conf.get("data_generation", {})

    lat = dg_conf.get("latitude")
    lon = dg_conf.get("longitude")
    mode = dg_conf.get("matching_mode", "closest")

    if lat is None or lon is None:
        print("Latitude or Longitude not specified in config.")
        sys.exit(1)

    print(f"Running Data Generation for Lat: {lat}, Lon: {lon}, Mode: {mode}")

    # 2. Run Grid Sampling (Step 1)
    print("\n--- Step 1: Grid Sampling ---")
    cmd = [
        "python",
        "process_single_location.py",
        "--lat",
        str(lat),
        "--lon",
        str(lon),
        "--match-by",
        "distance" if mode == "closest" else "population",
    ]

    # Run in gridreadout directory
    result = subprocess.run(cmd, cwd=GRIDREADOUT_DIR, capture_output=False)
    if result.returncode != 0:
        print("Grid sampling failed.")
        sys.exit(result.returncode)

    # Find the generated result file
    results_dir = GRIDREADOUT_DIR / "results"
    h5_files = list(results_dir.glob("*.h5"))
    if not h5_files:
        print("No result file found in gridreadout/results")
        sys.exit(1)

    # Get the latest file
    grid_file = max(h5_files, key=os.path.getmtime)
    grid_file_id = grid_file.stem.split("_")[
        0
    ]  # e.g. N2822500E4429500 from N..._plz_...
    print(f"Generated grid file: {grid_file.name} (ID: {grid_file_id})")

    # 3. Run Demand Allocation (Step 2)
    print("\n--- Step 2: Demand Allocation ---")

    cmd = ["python", "wrapper_generate.py", grid_file_id]

    result = subprocess.run(cmd, cwd=GRIDALLOC_DIR, capture_output=False)
    if result.returncode != 0:
        print("Demand allocation failed.")
        sys.exit(result.returncode)

    # 4. Process Outputs
    print("\n--- Step 3: Processing Outputs ---")

    # Convert Grid to Excel
    print("Converting grid to Excel...")
    try:
        with h5py.File(grid_file, "r") as f:
            json_net = f["/raw_data/net"][()]
            if isinstance(json_net, bytes):
                json_net = json_net.decode("utf-8")
            net = pp.from_json_string(json_net)

        output_grid_path = DATA_INPUT_DIR / "grid.xlsx"
        pp.to_excel(net, output_grid_path)
        print(f"Saved grid to {output_grid_path}")

        # Update experiment.yml to point to this grid file?
        # The experiment.yml has `grid_file: kerber_landnetz_freileitung_1.xlsx`
        # We should probably overwrite that file or ensure experiment.yml points to `grid.xlsx`.
        # The user said "Output: Pandapower Excel file and Demand CSVs in data/input".
        # I'll assume the user will update experiment.yml or I should name it `grid.xlsx` and update experiment.yml to use `grid.xlsx`.
        # I'll stick to `grid.xlsx`.

    except Exception as e:
        print(f"Failed to convert grid: {e}")
        sys.exit(1)

    # Move Demands
    print("Moving demand files...")
    # The wrapper saves to gridalloc/results/{id}_electricity_demands.csv
    alloc_results_dir = GRIDALLOC_DIR / "results"
    demand_file = alloc_results_dir / f"{grid_file_id}_electricity_demands.csv"
    buildings_file = alloc_results_dir / f"{grid_file_id}_electricity_buildings.csv"

    if demand_file.exists():
        shutil.copy(demand_file, DATA_INPUT_DIR / "demands.csv")
        print(f"Saved demands to {DATA_INPUT_DIR / 'demands.csv'}")
    else:
        print(f"Demand file not found: {demand_file}")

    if buildings_file.exists():
        shutil.copy(buildings_file, DATA_INPUT_DIR / "buildings.csv")
        print(f"Saved buildings info to {DATA_INPUT_DIR / 'buildings.csv'}")

    print("\nData Generation Complete.")


if __name__ == "__main__":
    main()
