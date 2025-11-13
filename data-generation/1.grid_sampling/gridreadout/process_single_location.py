#!/usr/bin/env python3
"""
Process a single location (lat/lon) by finding the nearest census cell,
assigning a representative pylovo grid, and performing all postprocessing.

Usage:
    python process_single_location.py --lat 48.51009 --lon 11.47325
    python process_single_location.py --lat 52.520008 --lon 13.404954  # Berlin
"""

import argparse
import sys

import src.db_read as dbrd
import src.save_grid as svgrd
import src.grid_topol as grdtpl
import src.weather as wth

import pandas as pd
import numpy as np
import geopandas as gpd
from shapely.geometry import Point
from pyproj import Transformer


def find_nearest_census_cell(lat, lon, df_census):
    """
    Find the nearest census cell to the given lat/lon coordinates.

    Args:
        lat: Latitude (EPSG:4326)
        lon: Longitude (EPSG:4326)
        df_census: DataFrame with census data (columns: x_mp_1km, y_mp_1km, Einwohner)

    Returns:
        Series with the nearest census cell data
    """
    # Transform from WGS84 (EPSG:4326) to EPSG:3035
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:3035", always_xy=True)
    x_census, y_census = transformer.transform(lon, lat)

    print(f"Input coordinates: lat={lat}, lon={lon}")
    print(f"Transformed to EPSG:3035: x={x_census:.2f}, y={y_census:.2f}")

    # Find the nearest census cell by Euclidean distance
    distances = (df_census["x_mp_1km"] - x_census) ** 2 + (
        df_census["y_mp_1km"] - y_census
    ) ** 2
    nearest_idx = distances.idxmin()
    nearest_cell = df_census.loc[nearest_idx]

    print(
        f"Nearest census cell: x={nearest_cell['x_mp_1km']}, y={nearest_cell['y_mp_1km']}, "
        f"population={nearest_cell['Einwohner']}"
    )

    return nearest_cell


def assign_grid(x_cell, y_cell, N_inh, df_grids, match_by="population"):
    """
    Assign a representative pylovo grid to a census cell.

    Logic:
    2a) Check if cell contains sampled grids already - if yes, sample one and use trafo position
    3a-c) Otherwise, find grids by matching strategy and use cell center as position

    Args:
        x_cell: Census cell center X coordinate (EPSG:3035)
        y_cell: Census cell center Y coordinate (EPSG:3035)
        N_inh: Number of inhabitants in the census cell
        df_grids: DataFrame with pylovo grid data
        match_by: Matching strategy - 'population' (default) or 'distance'

    Returns:
        DataFrame row with the assigned grid
    """
    df_g = df_grids.copy()

    ### 2a) Check if cell contains sampled grids already:
    df_same_cell = df_g[(df_g["x_census"] == x_cell) & (df_g["y_census"] == y_cell)]
    if len(df_same_cell) != 0:
        sample_grid = df_same_cell.sample(n=1, random_state=int(x_cell))

        # 2b) Assign trafo pos as grid pos
        sample_grid.drop(columns=["x_census", "y_census"], inplace=True)
        print(
            f"Found {len(df_same_cell)} grid(s) in census cell, sampled one with trafo position"
        )
        return sample_grid

    ### 3a) Find grids based on matching strategy:
    if match_by == "population":
        # Match by closest population density
        df_g["diff"] = (df_g["Einwohner"] - N_inh).abs()
        min_diff = df_g["diff"].min()
        closest_rows = df_g[df_g["diff"] == min_diff].drop(columns="diff")

        print(
            f"No grid in census cell. Found {len(closest_rows)} grid(s) with closest population density "
            f"(diff={min_diff:.1f}), using cell center as position"
        )

    elif match_by == "distance":
        # Match by geographic distance to census cell center
        df_g["dist"] = (
            (df_g["x_census"] - x_cell) ** 2 + (df_g["y_census"] - y_cell) ** 2
        ) ** 0.5
        min_dist = df_g["dist"].min()
        closest_rows = df_g[df_g["dist"] == min_dist].drop(columns="dist")

        print(
            f"No grid in census cell. Found {len(closest_rows)} grid(s) at closest distance "
            f"({min_dist:.1f}m from cell center), using cell center as position"
        )

    else:
        raise ValueError(
            f"Invalid match_by value: {match_by}. Must be 'population' or 'distance'"
        )

    # 3b) Sample one grid out of selection
    sample_grid = closest_rows.sample(n=1, random_state=int(x_cell))

    # 3c) Take cell center as trafo pos
    sample_grid[["x", "y"]] = (x_cell, y_cell)
    sample_grid.drop(columns=["x_census", "y_census"], inplace=True)

    return sample_grid


def lookup_zip_code(lon, lat, gdf_zip):
    """Look up postal code (PLZ) for given lat/lon coordinates."""
    point = Point(lon, lat)

    # Ensure the CRS is set and transform if necessary
    if gdf_zip.crs is None:
        gdf_zip.set_crs(epsg=4326, inplace=True)
    elif gdf_zip.crs.to_epsg() != 4326:
        gdf_zip = gdf_zip.to_crs(epsg=4326)

    # Spatial join to find the matching PLZ polygon
    matching_row = gdf_zip[gdf_zip.contains(point)]

    # Extract PLZ
    if not matching_row.empty:
        return matching_row.iloc[0]["plz"]
    else:
        return np.nan


def lookup_gemeindeschluessel(lat, lon, gdf_munic):
    """
    Look up Gemeindeschlüssel (AGS) and municipality name for given lat/lon.

    Args:
        lat: Latitude (WGS84)
        lon: Longitude (WGS84)
        gdf_munic: GeoDataFrame with municipality polygons

    Returns:
        Tuple of (AGS, municipality_name) or None if not found
    """
    pt = Point(lon, lat)

    # Build spatial index
    sindex = gdf_munic.sindex

    # First find candidate polygons
    idx_candidates = list(sindex.intersection(pt.bounds))
    candidates = gdf_munic.iloc[idx_candidates]

    # Then test which one contains the point
    match = candidates[candidates.contains(pt)]
    if match.empty:
        return None

    row = match.iloc[0]
    return row["AGS"], row["GEN"]


def get_regiostar_region(AGS, df_regiostar):
    """Get RegioStaR7 typology from Gemeindeschlüssel (AGS)."""
    row = df_regiostar[df_regiostar["gem_20"] == AGS]
    try:
        return int(row["RegioStaR7"].values[0])
    except:
        return np.nan


def process_location(
    lat,
    lon,
    df_census,
    df_grid_set,
    gdf_zip,
    gdf_munic,
    df_regiostar,
    DB,
    match_by="population",
):
    """
    Process a single location: find nearest census cell, assign grid, perform postprocessing.

    Args:
        lat: Latitude (WGS84)
        lon: Longitude (WGS84)
        df_census: Census data DataFrame
        df_grid_set: Pylovo grid data DataFrame
        gdf_zip: GeoDataFrame with postal code polygons
        gdf_munic: GeoDataFrame with municipality polygons
        df_regiostar: RegioStar7 reference DataFrame
        DB: Database object for reading pylovo grid data
        match_by: Matching strategy - 'population' or 'distance'

    Returns:
        DataFrame with the processed grid information
    """
    print("\n" + "=" * 70)
    print("STEP 1: Find nearest census cell")
    print("=" * 70)

    # Find nearest census cell
    nearest_cell = find_nearest_census_cell(lat, lon, df_census)

    print("\n" + "=" * 70)
    print(f"STEP 2: Assign representative pylovo grid (match by: {match_by})")
    print("=" * 70)

    # Assign a representative grid to this cell
    df_grid = assign_grid(
        nearest_cell["x_mp_1km"],
        nearest_cell["y_mp_1km"],
        nearest_cell["Einwohner"],
        df_grid_set,
        match_by=match_by,
    )

    # Rename columns to match notebook convention
    df_grid = df_grid.rename(
        columns={"plz": "plz_pylovo", "Einwohner": "pop_density_1km2_cell"}
    )

    print("\n" + "=" * 70)
    print("STEP 3: Convert coordinates to lat/lon")
    print("=" * 70)

    # Transform grid position to WGS84
    transformer = Transformer.from_crs("EPSG:3035", "EPSG:4326", always_xy=True)
    grid_lon, grid_lat = transformer.transform(
        df_grid["x"].values[0], df_grid["y"].values[0]
    )
    df_grid["lon"] = grid_lon
    df_grid["lat"] = grid_lat

    print(f"Grid position: lat={grid_lat}, lon={grid_lon}")

    print("\n" + "=" * 70)
    print("STEP 4: Look up postal code (PLZ)")
    print("=" * 70)

    # Look up postal code
    plz = lookup_zip_code(grid_lon, grid_lat, gdf_zip)
    df_grid["plz"] = plz

    if pd.isna(plz):
        print("WARNING: Could not find postal code for this location!")
        return None
    else:
        print(f"Postal code: {plz}")

    print("\n" + "=" * 70)
    print("STEP 5: Look up municipality (Gemeindeschlüssel)")
    print("=" * 70)

    # Look up Gemeindeschlüssel
    result = lookup_gemeindeschluessel(grid_lat, grid_lon, gdf_munic)
    if result is None:
        print("WARNING: Could not find Gemeindeschlüssel for this location!")
        return None

    ags, municipality_name = result
    df_grid["gemeindeschlüssel"] = ags
    df_grid["name"] = municipality_name

    print(f"Municipality: {municipality_name} (AGS: {ags})")

    print("\n" + "=" * 70)
    print("STEP 6: Look up RegioStar7 region")
    print("=" * 70)

    # Look up RegioStar7
    regio7 = get_regiostar_region(int(ags), df_regiostar)
    df_grid["regio7"] = regio7

    if pd.isna(regio7):
        print("WARNING: Could not find RegioStar7 for this location!")
        return None
    else:
        print(f"RegioStar7: {int(regio7)}")

    print("\n" + "=" * 70)
    print("STEP 7: Retrieve pylovo grid data and weather")
    print("=" * 70)

    # Prepare grid specs for database lookup
    grid_specs = {
        "cell_id": f"N{int(df_grid['y'].values[0])}E{int(df_grid['x'].values[0])}",
        "plz": df_grid["plz_pylovo"].values[0],
        "kcid": df_grid["kcid"].values[0],
        "bcid": df_grid["bcid"].values[0],
    }

    print(f"Grid specs: {grid_specs}")

    # Read out pandapower grid
    print("  - Reading pandapower grid...")
    net = DB.read_single_ppgrid(grid_specs)
    net = grdtpl.assign_min_linelen(net)
    net = grdtpl.remove_duplicate_loads(net)
    print(
        f"    Found {len(net.bus)} buses, {len(net.line)} lines, {len(net.load)} loads"
    )

    # Retrieve buildings
    print("  - Reading buildings...")
    df_buildings = DB.read_buildings(grid_specs, net.bus)
    print(f"    Found {len(df_buildings)} buildings")

    # Get weather data
    print("  - Fetching weather data from PVGIS...")
    location = {"lat": grid_lat, "lon": grid_lon}
    df_weather_raw, altitude, selected_months = wth.get_pvgis_tmy_sarah3_dataframe(
        location["lat"], location["lon"]
    )
    print(f"    Altitude: {altitude}m, {len(df_weather_raw)} hourly records")

    # Add dew point temperature
    print("  - Calculating dew point...")
    df_weather_raw["dew_point"] = wth.get_dew_point(
        df_weather_raw["temp_air"], df_weather_raw["relative_humidity"]
    )

    # Add soil temperature
    print("  - Fetching soil temperature from Open-Meteo...")
    df_weather_raw["soil_temp"] = wth.get_open_meteo_soil_temperature(
        location["lat"], location["lon"], selected_months
    )

    df_grid["altitude"] = altitude

    print("\n" + "=" * 70)
    print("STEP 8: Save to files")
    print("=" * 70)

    # Save to files
    SF = svgrd.SaveFile(grid_specs)

    print(f"  - Saving topology to results/{grid_specs['cell_id']}.h5")
    SF.save_topology(net, "/raw_data/")

    print(f"  - Saving region specs to results/{grid_specs['cell_id']}.h5")
    SF.save_df(df_grid, "/raw_data/region")

    print(f"  - Saving buildings to results/{grid_specs['cell_id']}.h5")
    SF.save_df(df_buildings, "/raw_data/buildings")

    print(f"  - Saving weather to results/{grid_specs['cell_id']}.h5")
    SF.save_df(df_weather_raw, "/raw_data/weather")

    print("\n" + "=" * 70)
    print("SUCCESS: Processing complete!")
    print("=" * 70)

    return df_grid


def main():
    """Main entry point for the script."""
    parser = argparse.ArgumentParser(
        description="Process a single location by finding the nearest pylovo grid",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Process location in Munich area (match by population density)
  python process_single_location.py --lat 48.51009 --lon 11.47325
  
  # Process location in Berlin (match by geographic distance)
  python process_single_location.py --lat 52.520008 --lon 13.404954 --match-by distance
        """,
    )
    parser.add_argument(
        "--lat", type=float, required=True, help="Latitude in WGS84 (EPSG:4326)"
    )
    parser.add_argument(
        "--lon", type=float, required=True, help="Longitude in WGS84 (EPSG:4326)"
    )
    parser.add_argument(
        "--match-by",
        type=str,
        default="population",
        choices=["population", "distance"],
        help="Grid matching strategy: 'population' (default, match by population density) "
        "or 'distance' (match by geographic proximity)",
    )

    args = parser.parse_args()

    print("\n" + "=" * 70)
    print("LOADING DATA")
    print("=" * 70)

    # Load all required data
    print("Loading census data...")
    df_census = pd.read_csv(
        "input_data/Zensus2022_Bevoelkerungszahl_1km-Gitter.csv", sep=";"
    )
    print(f"  Loaded {len(df_census)} census cells")

    print("Loading pylovo grid data...")
    df_grid_set = pd.read_hdf("input_data/valid_grids")
    print(f"  Loaded {len(df_grid_set)} grids")

    print("Loading postal code shapefile...")
    gdf_zip = gpd.read_file("input_data/plz-5stellig.shp")
    print(f"  Loaded {len(gdf_zip)} postal code areas")

    print("Loading municipality shapefile...")
    gdf_munic = gpd.read_file("input_data/VG250_GEM.shp")
    if gdf_munic.crs.to_epsg() != 4326:
        gdf_munic = gdf_munic.to_crs(epsg=4326)
    print(f"  Loaded {len(gdf_munic)} municipalities")

    print("Loading RegioStar7 reference data...")
    df_regiostar = pd.read_excel(
        "input_data/regiostar-referenzdateien.xlsx",
        sheet_name="ReferenzGebietsstand2020",
    )
    print(f"  Loaded {len(df_regiostar)} regions")

    print("Connecting to pylovo database...")
    DB = dbrd.DataBase()
    print("  Database connection established")

    # Process the location
    try:
        result = process_location(
            args.lat,
            args.lon,
            df_census,
            df_grid_set,
            gdf_zip,
            gdf_munic,
            df_regiostar,
            DB,
            match_by=args.match_by,
        )

        if result is None:
            print("\nERROR: Processing failed (see warnings above)")
            sys.exit(1)
        else:
            print("\nFinal grid information:")
            print(result.to_string())

    except Exception as e:
        print(f"\nERROR: {str(e)}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
