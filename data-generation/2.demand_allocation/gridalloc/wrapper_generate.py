import sys
import argparse
import os
import generate_electricity_minimal as gem


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("inputfile_id")
    args = parser.parse_args()

    # Find file logic
    if not os.path.exists("data/grids"):
        print("Error: data/grids directory not found")
        sys.exit(1)

    all_entries = os.listdir("data/grids")
    h5_files = [fname for fname in all_entries if fname.endswith(".h5")]
    input_id_str = str(args.inputfile_id)
    matched_files = [
        fname for fname in h5_files if fname.split("_", 1)[0] == input_id_str
    ]

    if not matched_files:
        print(f"ERROR: No grid file found with ID '{input_id_str}' in data/grids/")
        sys.exit(1)

    inputfile = matched_files[0]

    print(f"Processing {inputfile}...")

    # Run generation
    df_buildings, df_elec_demand = gem.generate_electricity_demands(inputfile)

    # Ensure results dir exists
    os.makedirs("results", exist_ok=True)

    # Save results
    output_prefix = f"results/{args.inputfile_id}_electricity"
    df_buildings.to_csv(f"{output_prefix}_buildings.csv", index=False)
    df_elec_demand.to_csv(f"{output_prefix}_demands.csv")
    print(f"Saved results to {output_prefix}...")


if __name__ == "__main__":
    main()
