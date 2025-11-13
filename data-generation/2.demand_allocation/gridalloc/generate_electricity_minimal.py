#!/usr/bin/env python3
"""
Minimal script to generate electrical demands for all houses in a grid.

Usage:
    python generate_electricity_minimal.py <grid_file_id>

Example:
    python generate_electricity_minimal.py N2822500E4429500
"""

import argparse
import sys
import time

import src.classes.save_grid as svgrd
import src.functions.electricity as elc


def generate_electricity_demands(grid_filename):
    """
    Generate electrical demands for all buildings in a grid file.

    Args:
        grid_filename: Name of the grid file (e.g., "N2822500E4429500_94113_6_1.h5")

    Returns:
        tuple: (df_buildings with demand info, df_elec_demand timeseries)
    """
    print(f"Loading grid file: {grid_filename}")

    # Load grid data
    SF = svgrd.SaveFile(grid_filename)
    df_buildings, df_region, df_weather_raw = SF.get_input_data()

    print(f"Found {len(df_buildings)} building(s)")
    print()

    # Step 1: Sample statistics (assign occupancy, total demands, use types)
    print("Step 1: Sampling building statistics...")
    print("  - Assigning household occupancy")
    print("  - Assigning total electricity demands")
    print("  - Assigning use types (residential vs commercial)")
    df_buildings = elc.sample_statistics(df_buildings)
    print("  ✓ Done")
    print()

    # Step 2: Generate hourly electricity demand timeseries
    print("Step 2: Generating hourly electricity demand timeseries...")
    df_buildings, df_elec_demand = elc.get_elec_demand(df_buildings)
    print("  ✓ Done")
    print()

    # Print summary
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"Total buildings: {len(df_buildings)}")
    print(f"Total apartments/units: {df_buildings['houses_per_building'].sum()}")

    residential_mask = df_buildings["use"] == "Residential"
    print(f"Residential buildings: {residential_mask.sum()}")
    print(f"Commercial buildings: {(~residential_mask).sum()}")

    # Calculate total annual demand
    annual_demand_kwh = df_elec_demand.sum().sum()
    print(f"\nTotal annual electricity demand: {annual_demand_kwh:.2f} kWh")
    print(f"Average hourly demand: {annual_demand_kwh/8760:.2f} kWh")
    print(f"Peak hourly demand: {df_elec_demand.sum(axis=1).max():.2f} kWh")
    print()

    # Show sample of building data
    print("Sample building data:")
    print(df_buildings[["bus", "type", "area", "houses_per_building", "use"]].head())
    print()

    print("Sample hourly demands (first 5 hours):")
    print(df_elec_demand.head())
    print()

    return df_buildings, df_elec_demand


def main():
    parser = argparse.ArgumentParser(
        description="Generate electrical demands for all houses in a grid",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python generate_electricity_minimal.py N2822500E4429500
  python generate_electricity_minimal.py N3194500E4475500
        """,
    )
    parser.add_argument(
        "inputfile_id", help="Input file ID (prefix before first underscore)"
    )
    args = parser.parse_args()

    # Find the matching file
    import os

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

    print("=" * 70)
    print("MINIMAL ELECTRICITY DEMAND GENERATOR")
    print("=" * 70)
    print()

    start_time = time.time()

    try:
        df_buildings, df_elec_demand = generate_electricity_demands(inputfile)

        # Optionally save results
        save_option = input("Save results to CSV files? (y/n): ").strip().lower()
        if save_option == "y":
            output_prefix = f"results/{args.inputfile_id}_electricity"
            df_buildings.to_csv(f"{output_prefix}_buildings.csv", index=False)
            df_elec_demand.to_csv(f"{output_prefix}_demands.csv")
            print(
                f"✓ Saved to {output_prefix}_buildings.csv and {output_prefix}_demands.csv"
            )

    except Exception as e:
        print(f"\nERROR: {str(e)}")
        import traceback

        traceback.print_exc()
        sys.exit(1)

    end_time = time.time()
    print(f"\nCompleted in {(end_time-start_time):.3f} seconds")


if __name__ == "__main__":
    main()
