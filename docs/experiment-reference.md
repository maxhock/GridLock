# Experiment reference

Everything an experiment YAML can say, what the input CSVs have to contain, and what a run writes.
[`README.md`](../README.md) covers the concept and the quick start — this is the detail you need once you write your own experiment rather than copy one.

Two experiments in `config/` are known to work and are the best starting points: `experiment-local-grid.yml` (local grid workbook, no InfDB access needed) and `experiment-LV.yml` (grid resolved from InfDB by postcode).

> **Physical parameters are not applied yet.**
> `model`, `capacity`, `power`, `max_production`, `control_strategy` and `accessible` are checked for presence and then dropped — no federate reads them.
> The house that runs is the hardcoded two-room RC network in `federates/house/build_my_house.py`, with the battery and heat pump from `federates/house/common_config.py`, whatever the YAML says.
> Changing `resistance: "5 Ohm"` changes nothing about the simulation.
> This is tracked as S1 in [`archive/issues-to-mvp.md`](archive/issues-to-mvp.md); the sections below mark each affected key.

## `general:`

| Key | Required | Default | Meaning |
|---|---|---|---|
| `name` | no | `GridLock` | Names the run. The scenario is `<name>_<YYYYMMDD_HHMMSS>` and the results schema is `"<name>Analysis"`. |
| `start_time` | no | `0` | Seconds, converted to a wall clock offset from `2023-01-01T00:00:00`. An ISO 8601 string is also accepted. |
| `end_time` | **yes** | — | Seconds after `start_time`, or an ISO 8601 string. |
| `time_step` | no | `1` | Simulation period in **whole** seconds, shared by every federate. |
| `use_meta_db` | no | `json` | `mongo`, or `json` to write metadata into `generated/` instead. |
| `use_data_db` | no | `postgres` | Where the timeseries go. |
| `meta_store_path` | no | `generated` | Directory the `json` metadata backend writes into. |

`time_step` has to be a whole number of seconds.
CST type-checks the HELICS `period` against an integer default, so a fractional step is refused up front rather than failing deep inside composegen.

`duration:` appears in both shipped experiment files and is read by nothing — the run length comes from `end_time` alone.

## `federation:`

A federate node has five keys, and nests through `sub_federates`:

```yaml
federation:
  id: "local-grid"          # required, unique among its siblings
  name: "Local Grid"        # optional label, used for display only
  class: "grid"             # required, see the table below
  config: { ... }           # class-specific, see the table below
  sub_federates: [ ... ]    # optional, same shape recursively
```

`id` builds the node's dotted path — a `house_0` under `local-grid` is `local-grid.house_0`, and that path becomes the federate name.
`name` is only a label.

`config.type` may be set to `empty` to keep a node in the tree without registering HELICS keys for it; it defaults to `value`.

### Classes

| `class` | Image | Required `config` | Optional |
|---|---|---|---|
| `grid` | `federates/grid` | exactly one of `layout` or `location` | — |
| `load` | `federates/house_player` | `electrical_load` (a `.csv`), `placement` | — |
| `house` | `federates/house` | `exogenous_data` (a `.csv`), `model`†, `placement` | — |
| `pv` | `federates/house` | `capacity`†, `max_production`† | — |
| `battery` | `federates/house` | `capacity`†, `power`† | — |
| `hems` / `controller` | `federates/controller` | `control_strategy`† | `accessible`† |
| `recorder` | `federates/recorder` | — | — |
| `empty` | — | — | — |

† Validated, then discarded — see the note at the top.

Anything not in this list is rejected by name.

`pv` and `battery` produce no federate at all today: their physics runs inside the house simulator.
Under a house they are accepted and skipped with a note; directly under a grid they raise `NotImplementedError`, because they would otherwise be mapped to the house image and start a house simulation with no dataset.
`recorder` is a leftover of the legacy config format and is not usable from a tree experiment — results come from the CST timeseries store.

### Grid source

A grid names exactly one source. Naming both, or neither, is an error.

```yaml
config:
  layout: "kerber_landnetz_freileitung_1.xlsx"   # a pandapower workbook in data/input/
```

```yaml
config:
  location:                # one or more InfDB queries
    - plz: 91359           # required
      kcid: 1              # optional, but kcid and bcid come as a pair
      bcid: 4
```

Every net a location query resolves to becomes its own grid federate, named `<id>_<plz>_<kcid>_<bcid>`.
A `plz`-only query therefore expands into one federate per grid InfDB holds for that postcode.
A layout grid keeps the node's `id` as its federate name.

### Placement

`placement` is a pandapower **load index**, a list of them, or `"fill"`, and it is required for everything that moves power: `load`, `house`, `pv`, `battery`.

The grid federate identifies incoming power purely by the `load_<idx>` segment of the HELICS key.
A federate wired onto a bare `active_power` key is silently dropped from the power flow, which is why composegen refuses to guess here.

- `"fill"` takes every load index no sibling claimed explicitly. At most one child per grid may use it.
- An index that does not exist in the net is an error, and so is a placement that resolves to nothing.
- Load indices left unclaimed are legal — the grid zeroes every static load at import, so they sit at 0 W — but composegen prints them, because an unclaimed index looks exactly like a typo.

A **house** simulates one building, so `placement: [4, 6]` becomes two independent federates named after the load each drives: `house_4` and `house_6`, not `house_0_*`.
A **load player** replays one profile across all of its indices and stays a single federate.

## Input data

Files named in an experiment are resolved against `data/input/`.

### `electrical_load` — the load player's profile

| Column | Required | Unit |
|---|---|---|
| `timestamp` | yes | Unix epoch seconds or ISO 8601. Epoch stamps are shifted so the first row is `t=0`. |
| `base_load` | yes | W |
| `reactive_power` | no | VAr, zero if absent |

Rows are held, not interpolated: a lookup returns the nearest previous sample, and the first row for anything before it.

Standard load profile names such as `H0` or `H25` are accepted by the schema but not implemented — give it a CSV.

### `exogenous_data` — the house's dataset

The columns are decided by EnergySim's `SimulationDataset`, which is a pinned external dependency and not part of this repository.
`data/input/sample_data.csv` is the known-good shape, and `tools/sample_data_generator.py` is what produces it:

| Column | Unit |
|---|---|
| `timestamp` | a datetime **string** (`2024-01-01 00:00:00`) |
| `ambient_temp` | °C |
| `solar_irradiance_w_m2` | W/m² |
| `price` | €/kWh |
| `load` | W |
| `internal_gains_w` | W |
| `solar_gains_w` | W |
| `wind_speed_m_s` | m/s |

The timestamp column is the trap here.
The load player accepts epoch integers, but a house parses the column as a datetime, so an epoch integer is read as *nanoseconds* since 1970 and the dataset collapses to a sub-microsecond span.
Use datetime strings for a house.

The house resamples the dataset onto the federation's `time_step` at startup — coarser data is averaged, finer data interpolated — so the CSV does not have to match `time_step`.

### Coverage

Every `electrical_load` and `exogenous_data` CSV has to cover the whole run, and composegen checks this before anything starts.
Without the check a load player silently repeats its last known value once the data runs out, producing a flat load that still reports success.

A house with a HEMS needs more than the run itself: at the last step the MPC still asks for a full forecast window, so it needs a further 24 steps (`transform.MPC_FORECAST_HORIZON_STEPS`) beyond `end_time`.
A CSV longer than needed is fine.

### What is in `data/input/` today

| File | Use |
|---|---|
| `sample_data.csv` | house `exogenous_data`; the working example |
| `sample_house.csv` | load player `electrical_load`; the working example |
| `kerber_landnetz_freileitung_1.xlsx`, `kerber_landnetz_kabel_1.xlsx` | grid `layout`, exported by `tools/pandapower2xlsx.py` |
| `heeten_building_df14.csv`, `ee_day_ahead_prices_2018_till_2020.csv` | measured inputs to `tools/sample_data_generator.py`, not usable directly |
| `building_timeseries.csv`, `h25_profile_2025_power.csv` | orphans — the schema of neither matches what a house or a load player reads |

## What a run writes

`databases/db-access.txt` covers how to reach the databases and what the tables look like.
This is what to expect *inside* them.

### Federate names

A federate is named by its dotted path from the root: `local-grid`, `local-grid.loadhouse_0`, `local-grid.house_4`, `local-grid.house_4.hems_0`.
For a house the last segment is the load index it drives, not the id declared in the YAML.

### HELICS keys

A key is `<publisher federate name, dots replaced by slashes>/<topic>`.
Federates never build these themselves — they discover them from the CST federation config at startup, because the names come from the tree rather than from a federate's own name.

| Topic | Type | Published by | Example key |
|---|---|---|---|
| `load_<idx>/active_power` | double, W | whatever is placed on that load | `local-grid/house_4/load_4/active_power` |
| `load_<idx>/reactive_power` | double, VAr | same | `local-grid/loadhouse_0/load_7/reactive_power` |
| `load_<idx>/voltage` | double, V | the grid | `local-grid/load_4/voltage` |
| `state` | string, JSON | a house | `local-grid/house_4/state` |
| `control` | string, JSON | a HEMS | `local-grid/house_4/hems_0/control` |

The `state` payload carries the thermal vector, battery SoC/SoH, storage temperatures, and heat-pump and AC power — it is what the HEMS reads to plan against.

### Provenance

The scenario document in the metadata store carries the git commit and a verbatim copy of the experiment YAML, so a stored run can be traced back to what produced it.
Generated artefacts land in `generated/`.
