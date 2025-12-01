import sys
import src.db_read as dbrd
import src.save_grid as svgrd
import src.grid_topol as grdtpl
import src.weather as wth

import pandas as pd
import numpy as np

import pandapower as pp

from shapely.geometry import box
from shapely.geometry import Point
import geopandas as gpd
from pyproj import Transformer
from omegaconf import OmegaConf
import warnings
from pathlib import Path
import src.classes.grid as grd

warnings.simplefilter(action="ignore", category=FutureWarning)

# ### Load census and grids


conf = OmegaConf.load("/config/experiment.yml")
out_path = Path(f"/data/input/{conf.federates.grid.grid_file}")
if out_path.exists():
    raise FileExistsError(
        f"Grid file already exists at {out_path}. Aborting to avoid overwrite."
    )

try:
    lat, lon = conf.data_generation.latitude, conf.data_generation.longitude
    if conf.data_generation.matching_mode not in ["population", "distance"]:
        raise ValueError(
            f"Invalid matching_mode: {conf.data_generation.matching_mode}, must be 'population' or 'distance'"
        )
    match_by = conf.data_generation.matching_mode
except AttributeError:
    print("No data generation configuration found. Exiting.")
    sys.exit(0)

df_grid_set = pd.read_hdf("input_data/valid_grids")  # Source: pylovo
df_grid_set.head()


df_census = pd.read_csv(
    "input_data/Zensus2022_Bevoelkerungszahl_1km-Gitter.csv", sep=";"
)  # Source: https://atlas.zensus2022.de/
df_census.head()


# ### Sample a set of representative grids (N~1500)


# 1. Sample census cell based on number of inhabitants (p(cell)~N_inhabitants)
# 2. If cell contains 1 or more simulated grids:
#
#     a) Sample one of the contained grids randomly (all with equal probability)
#
#     b) Assign as grid position the transformer position
#
#
# 3. If cell contains no simulated grid:
#
#     a) Take all grids with the closest population density to the census cell
#
#     b) Sample one grid out of this subset
#
#     c) Take as grid position the center of the census cell
#
# 4. From position: assign zip code and regiostar region


# ##### 1. Sample 1500 census cells


def find_nearest_census_cell(lat, lon, df_census):
    """
        Find the nearest cenout_path = Path(f"/data/input/{conf.federates.grid.grid_file}")
    if out_path.exists():
        raise FileExistsError(f"Grid file already exists at {out_path}. Aborting to avoid overwrite.")sus cell to the given lat/lon coordinates.

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


# Normalize inhabitants to get probabilities
probabilities = df_census["Einwohner"] / df_census["Einwohner"].sum()

# Sample 1000 rows with replacement=False, weighted by inhabitants
df_sampled_cells = pd.DataFrame(
    find_nearest_census_cell(lat, lon, df_census)
).T  # Example for Berlin
# df_sampled_cells = df_census.sample(n=1500, weights=probabilities, replace=True, random_state=3)
df_sampled_cells.head()


df_sampled_cells.duplicated().sum()


# Step 1: Column definitions
df = df_sampled_cells.copy()
x_col = "x_mp_1km"  # centroid X [m]
y_col = "y_mp_1km"  # centroid Y [m]
pop_col = "Einwohner"  # population

half_size = 500  # half side‐length in metres

# Step 2: Build square polygons in EPSG:3035
polygons = df.apply(
    lambda row: box(
        row[x_col] - half_size,
        row[y_col] - half_size,
        row[x_col] + half_size,
        row[y_col] + half_size,
    ),
    axis=1,
)

gdf = gpd.GeoDataFrame(df[[pop_col]], geometry=polygons, crs="EPSG:3035")

# Step 3: Plot in EPSG:3035 so boxes remain true squares
# fig, ax = plt.subplots(figsize=(10, 10))

# Plot the grid cells
# gdf.plot(
#     ax=ax,
#     column=pop_col,
#     cmap="viridis",
#     linewidth=0,
#     edgecolor="none",
#     alpha=0.8,
#     legend=True,
#     legend_kwds={
#         "label": "Population per 1 km² grid cell",
#         "shrink": 0.6
#     }
# )

# Optional: add a basemap reprojected on the fly
# ctx.add_basemap(
#     ax,
#     source=ctx.providers.CartoDB.Positron,
#     crs=gdf.crs.to_string()
# )

# # Formatting
# ax.set_title(
#     "Deutschland – Bevölkerung pro 1 km × 1 km (Zensus 2022), EPSG:3035",
#     fontsize=14
# )
# ax.set_aspect("equal")      # ensure equal axis scales
# ax.set_axis_off()

# plt.tight_layout()
# plt.show()


# ##### 2. + 3. Assign Representative Grid To Cell


### Step 2 + 3 - Assign grid to cell: ###
def assign_grid(x_cell, y_cell, N_inh, df_grids, match_by="population"):
    df_g = df_grids.copy()

    ### 2a) Check if cell contains sampled grids already:
    df_same_cell = df_g[(df_g["x_census"] == x_cell) & (df_g["y_census"] == y_cell)]
    if len(df_same_cell) != 0:
        sample_grid = df_same_cell.sample(n=1, random_state=x_cell)

        # 2b) Assign trafo pos as grid pos
        sample_grid.drop(columns=["x_census", "y_census"], inplace=True)
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
    sample_grid = closest_rows.sample(n=1, random_state=x_cell)

    # 3c) Take cell center as trafo pos
    sample_grid[["x", "y"]] = (x_cell, y_cell)
    sample_grid.drop(columns=["x_census", "y_census"], inplace=True)
    return sample_grid


df_sampled_grids = df_sampled_cells.apply(
    lambda row: assign_grid(
        row["x_mp_1km"], row["y_mp_1km"], row["Einwohner"], df_grid_set, match_by
    ),
    axis=1,
)
df_sampled_grids = pd.concat(df_sampled_grids.values, ignore_index=True).rename(
    columns={"plz": "plz_pylovo", "Einwohner": "pop_density_1km2_cell"}
)
df_sampled_grids.head()


df_sampled_grids.duplicated().sum()


df_sampled_grids["pop_density_1km2_cell"].describe()


# #### 4. Assign zip-code and regiostar7


# Lattitude and Longitude


transformer = Transformer.from_crs("EPSG:3035", "EPSG:4326", always_xy=True)
df_sampled_grids[["lon", "lat"]] = df_sampled_grids.apply(
    lambda row: transformer.transform(row["x"], row["y"]), axis=1
).apply(pd.Series)
df_sampled_grids.head()


# zip code


shapefile_path = "input_data/plz-5stellig.shp"  # Source: https://www.suche-postleitzahl.org/downloads
gdf_zip = gpd.read_file(shapefile_path)


def lookup_zip_code(lon, lat, gdf_zip):
    point = Point(lon, lat)  # Note: shapely expects (x, y) = (lon, lat)

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


df_sampled_grids["plz"] = df_sampled_grids.apply(
    lambda row: lookup_zip_code(row["lon"], row["lat"], gdf_zip), axis=1
)
df_sampled_grids = df_sampled_grids.dropna(
    subset=["plz"]
)  # Drop those for which Gemeindeschlüssel could not be found (shouldn't be more than 1%)
df_sampled_grids.head()


# Gemeindeschlüssel


# Path to your downloaded VG250 shapefile:
MUNICI_SHP = "input_data/VG250_GEM.shp"  # Source: https://gdz.bkg.bund.de/index.php/default/verwaltungsgebiete-1-250-000-stand-01-01-vg250-01-01.html

# Read municipalities
gdf_munic = gpd.read_file(MUNICI_SHP)

# Make sure it’s in WGS84 (lat/lon)
if gdf_munic.crs.to_epsg() != 4326:
    gdf_munic = gdf_munic.to_crs(epsg=4326)

# Build spatial index
sindex = gdf_munic.sindex


def lookup_gemeindeschluessel(lat, lon):
    """
    Given WGS84 latitude and longitude,
    returns the matching Gemeinde-Schlüssel (AGS) and name.
    """
    pt = Point(lon, lat)

    # first find candidate polygons
    idx_candidates = list(sindex.intersection(pt.bounds))
    candidates = gdf_munic.iloc[idx_candidates]

    # then test which one contains the point
    match = candidates[candidates.contains(pt)]
    if match.empty:
        return None

    row = match.iloc[0]
    return row["AGS"], row["GEN"]  # Gemeindeschlüssel + municipality name


df_sampled_grids[["gemeindeschlüssel", "name"]] = df_sampled_grids.apply(
    lambda row: lookup_gemeindeschluessel(row["lat"], row["lon"]), axis=1
).apply(pd.Series)
df_sampled_grids = df_sampled_grids.dropna(
    subset=["gemeindeschlüssel"]
)  # Drop those for which Gemeindeschlüssel could not be found (shouldn't be more than 1%)
df_sampled_grids.head()


# RegioStar7


df_regiostar = pd.read_excel(
    "input_data/regiostar-referenzdateien.xlsx", sheet_name="ReferenzGebietsstand2020"
)
df_regiostar.head()


def get_regiostar_region(AGS, df_regiostar):
    """Get RegioStaR7 typology from Gemeindeschlüssel (AGS)"""
    row = df_regiostar[df_regiostar["gem_20"] == AGS]
    try:
        return int(row["RegioStaR7"].values[0])
    except (IndexError, KeyError):
        return np.nan


df_sampled_grids["regio7"] = df_sampled_grids.apply(
    lambda row: get_regiostar_region(int(row["gemeindeschlüssel"]), df_regiostar),
    axis=1,
)
df_sampled_grids = df_sampled_grids.dropna(
    subset=["regio7"]
)  # Drop those for which regiostar7 could not be found, shouldn't be more than 1%


# Drop general duplicate entries (this will slightly skew the distribution, but prevents simulating the same grid several times):
df_sampled_grids = df_sampled_grids.drop_duplicates().reset_index(drop=True)
df_sampled_grids


# ### Read out pylovo grid data


### Create database object to read grid data from pylovo database
DB = dbrd.DataBase()
DB.show_contents()


def retrieve_pylovo_grid(df_region_specs):
    """:: df_region_specs must at least include columns:
    - plz_pylovo
    - kcid, bcid
    - plz
    - regio7
    - lat, lon
    """

    grid_specs = {  # unique grid identifier (as duplicates were dropped)
        "cell_id": f"N{int(df_region_specs['y'])}E{int(df_region_specs['x'])}",
        "plz": df_region_specs["plz_pylovo"],
        "kcid": df_region_specs["kcid"],
        "bcid": df_region_specs["bcid"],
    }
    # Generate SaveFile object to find file path and test if file exists
    # SF=svgrd.SaveFile(grid_specs)
    # Path("/data/input/", f"grid_{SF.path.split('/')[-1].split('.')[0]}.xlsx")

    # if Path.exists(Path("/data/input/", f"grid_{SF.path.split('/')[-1].split('.')[0]}.xlsx")):
    #     print(f"Grid file already exists at grid_{SF.path.split('/')[-1].split('.')[0]}.xlsx, skipping retrieval.")
    #     return(SF.path)

    ######### Process Pylovo Grid #########
    # Read out pandapower grid associated with plz, kcid, bcid
    net = DB.read_single_ppgrid(grid_specs)
    net = grdtpl.assign_min_linelen(net)  # Adjust and save grid topology
    net = grdtpl.remove_duplicate_loads(net)

    # Retrieve buildings associated with plz, kcid, bcid
    df_buildings = DB.read_buildings(grid_specs, net.bus)

    ######## Process Weather ###########
    location = {"lat": df_region_specs["lat"], "lon": df_region_specs["lon"]}

    # Get TMY data from SARAH3 dataset as DataFrame
    df_weather_raw, altitude, selected_months = wth.get_pvgis_tmy_sarah3_dataframe(
        location["lat"], location["lon"]
    )
    # Add dew point temperature necessary for vehicle simulation
    df_weather_raw["dew_point"] = wth.get_dew_point(
        df_weather_raw["temp_air"], df_weather_raw["relative_humidity"]
    )
    # Add soil temperature (1.00-2.55m) necessary for ground source heat pumps
    df_weather_raw["soil_temp"] = wth.get_open_meteo_soil_temperature(
        location["lat"], location["lon"], selected_months
    )

    df_region_specs["altitude"] = altitude

    ######### Save to file #########
    SF = svgrd.SaveFile(grid_specs)
    SF.save_topology(net, "/raw_data/input")
    SF.save_df(df_region_specs, "/raw_data/region")
    SF.save_df(df_buildings, "/raw_data/buildings")
    SF.save_df(df_weather_raw, "/raw_data/weather")

    pp.convert_format(net)  # Convert to new pandapower format

    return SF.path, net


SF_paths, net = df_sampled_grids[0:100].apply(retrieve_pylovo_grid, axis=1)[0]
pp.to_excel(net, str(out_path))

# # Demand Generation


### Run Settings
settings = {
    "grid_filename": SF_paths.split("/")[-1],  # Name of input file
    # "grid_filename" = "N2827500E4503500_93426_5_41.h5",
    "weather_data_exists": True,  # Is weather data already included in input grid file's raw data? (recommended, as on HPC cluster no outside API access)
    "parallel": True,  # Parallelized run?
    "n_cpu": 12,  # cpus if parallel
}

# Setup grid which stores all relevant data for assigning demands
GRD = grd.Grid(settings)
# GRD.df_buildings = GRD.df_buildings.iloc[0:10].reset_index(drop=True)


# #Weather
# GRD.retrieve_weather()
# GRD.df_weather_raw.head()
# #Solar
# GRD.generate_solar()
# GRD.df_supim_solar.head()
# Electricity
GRD.generate_electricity()
GRD.df_demand_elec.head()
# # Heat
# GRD.generate_heat()
# GRD.df_demand_heat_space.head()
# # Mobility
# GRD.generate_mobility()
# GRD.df_demand_mobility.head()


# URBS Output Creation
#


# Weather for URBS
# GRD.create_weather_urbs()
# GRD.df_weather_urbs.head()
# # SUPIM
# GRD.create_supim()
# GRD.df_supim.head()

# Demands for URBS
GRD.create_demand()
GRD.df_demand.columns = GRD.df_demand.columns.droplevel(1)
GRD.df_demand = GRD.df_demand.reset_index(drop=True)


GRD.df_demand.head()

# # TVE
# GRD.create_tve()
# GRD.df_tve.head()

# # Bsp
# GRD.create_bsp()
# GRD.df_bsp.head()

# # Processes
# GRD.create_processes()
# GRD.df_pro.head()

# # Commodities
# GRD.create_commodities()
# GRD.df_com.head()

# # Process-Commodities mapping
# GRD.create_process_commodity()
# GRD.df_pro_com.head()

# # Storage
# GRD.create_storages()
# GRD.df_sto.head()


def replaceBusLoad(df_demand, net):
    """
    Replace column names in df_demand (which are bus indices) with corresponding load indices.

    Args:
        df_demand: DataFrame with bus indices as column names
        net: pandapower network containing bus and load information

    Returns:
        DataFrame with load indices as column names
    """
    # Create mapping from bus index to load index
    bus_to_load = {}
    for load_idx, bus_idx in net.load["bus"].items():
        bus_to_load[bus_idx] = load_idx

    # Get current column names (bus indices)
    current_columns = df_demand.columns.tolist()

    # Create new column names (load indices)
    new_columns = []
    for col in current_columns:
        if col in bus_to_load:
            new_columns.append(bus_to_load[col])
        else:
            # If bus has no load, keep original column name
            new_columns.append(col)

    # Rename columns
    df_demand.columns = new_columns

    return df_demand


df_demand = replaceBusLoad(GRD.df_demand, net)
# df_demand = GRD.df_demand
df_demand.to_csv(f"/data/input/demand_{out_path.stem.split('_',1)[1]}.csv", index=False)
print(
    f"Succesfully retrieved grid and electrical demand data under name: {out_path.stem.split('_',1)[1]}"
)


# df = GRD.save_grid_data()
