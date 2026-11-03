# Experiment reference

Everything an experiment YAML can say, what the input CSVs have to contain, and what a run writes.
[`README.md`](../README.md) covers the concept and the quick start — this is the detail you need once you write your own experiment rather than copy one.

Three experiments in `config/` are the best starting points: `experiment-local-grid.yml` (local grid workbook, no InfDB access needed), `experiment-LV.yml` (grid resolved from InfDB by postcode) and `experiment-MV-LV.yml` (an MV grid with LV grids on its loads, see [Nested grids](#nested-grids); no InfDB access needed).

> **Physical parameters are not applied yet.**
> `model`, `capacity`, `power`, `max_production`, `control_strategy` and `accessible` are checked for presence and then dropped — no federate reads them.
> The house that runs is the hardcoded two-room RC network in `federates/house/build_my_house.py`, with the battery and heat pump from `federates/house/common_config.py`, whatever the YAML says.
> Changing `resistance: "5 Ohm"` changes nothing about the simulation.
> This is tracked as S1 in issue #56; the sections below mark each affected key.

## `general:`

| Key | Required | Default | Meaning |
|---|---|---|---|
| `name` | no | `GridLock` | Names the run. The scenario is `<name>_<YYYYMMDD_HHMMSS>` and the results schema is `"<name>Analysis"`. |
| `start_time` | no | `0` | When the run starts: seconds after `2023-01-01T00:00:00`, or an ISO 8601 time. |
| `end_time` | one of these two | — | When the run ends, **absolute** like `start_time` — not a length. |
| `duration` | one of these two | — | Run length in seconds. |
| `time_step` | no | `1` | Simulation period in **whole** seconds, shared by every federate. |
| `use_meta_db` | no | `json` | `mongo`, or `json` to write metadata into `generated/` instead. |
| `use_data_db` | no | `postgres` | Where the timeseries go. |
| `meta_store_path` | no | `generated` | Directory the `json` metadata backend writes into. |

Give at most two of `start_time`, `end_time` and `duration`; any two fix the third (`run_window.resolve_run_window`):

| Given | Run |
|---|---|
| `duration` | 0 to `duration` |
| `end_time` | 0 to `end_time` |
| `start_time` + `end_time` | as given; the duration is their difference |
| `start_time` + `duration` | `start_time` to `start_time + duration` |
| `end_time` + `duration` | `end_time - duration` to `end_time` |

All three together are refused, even when they agree: a third value can only repeat the other two or contradict them.
`start_time: 1800, end_time: 7200` is a 5400 s run from 00:30 to 02:00.

Times may be numbers or ISO 8601 times, quoted or not, mixed freely.
They are normalised to `YYYY-MM-DDTHH:MM:SS`, so a bare date works; one with a UTC offset (`+01:00`) or a fraction of a second is refused.
The run has to end after it starts.

Simulation time starts at 0 whatever `start_time` is, and the CSVs are read from their first row, so `start_time` shifts only the wall clock the run is stamped with.

`time_step` has to be a whole number of seconds.
CST type-checks the HELICS `period` against an integer default, so a fractional step is refused up front rather than failing deep inside composegen.

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
| `grid` | `federates/grid` | exactly one of `layout` or `location`; nested under another grid: a `layout` and a `placement` | — |
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

A grid names exactly one source.
Naming both, or neither, is an error.

```yaml
config:
  layout: "kerber_landnetz_freileitung_1.xlsx"   # a pandapower net in data/input/
```

A `layout` is a pandapower net as a `.xlsx` workbook (what `pandapower.from_excel` reads, and what `tools/pandapower2xlsx.py` writes) or a `.json` file (pandapower's own JSON, `pandapower.to_json`).
The path is relative to `data/input/` and may include a subdirectory.
composegen checks the suffix and that the file exists before anything runs.

The repository is public, so real DSO grids do not belong in it.
Keep them in `data/input/private/` and the experiments that use them in `config/private/` (both git-ignored), and name them as `layout: "private/<file>.json"`.
The committed stand-ins are `mv_synthetic_20kv.json` (a synthetic 20 kV MV net) and the `*_20kv.xlsx` Kerber variants (see [What is in `data/input/` today](#what-is-in-datainput-today)).

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

The infdb stage resolves every `class: grid` node in the tree, not only the root, and stores each net under the node's dotted tree id (`mv-grid_0`, `mv-grid_0.lv-grid_0`).
A root grid's net is read under its federate name, which is the same string for a layout grid.

#### Grid import

When a grid federate loads its net, it clears everything that is not driven by a federate:

- every static load is zeroed (`p_mw`, `q_mvar`), and every load's `scaling` is reset to 1.0 — DSO exports use values such as 0, 0.3 and 0.7, and a federate publishes the actual power of its load;
- every static `sgen` is zeroed (`p_mw`, `q_mvar`), with a warning that names how many and how many MW of nominal feed-in were removed.

So only federates drive the grid, and the layout's own demand and generation never count.
This is also why an unclaimed load index is harmless.

### Nested grids

A grid may sit under another grid (an LV feeder on an MV net). It attaches to the parent's loads, so it takes `placement` like the other load-placed classes: an int, a non-empty list of ints (each an int >= 0, no duplicates) or `"fill"`. The values are pandapower load indices of the **parent** grid.
These are rejected before anything is written:

- a nested grid without `placement`;
- `placement` on the root grid, which has nothing to attach to;
- a nested grid with `location` — it must use a `layout` for now, because an InfDB query returns an unknown number of LV feeders with no mapping onto the parent's loads (auto-placement is a later phase, issue #65);
- a grid under any non-grid node, which would never be wired.

A nested grid takes part in the parent's `fill`, overlap checks and unclaimed-load report exactly like a `house` or a `load` does.
At most one child of the parent may use `"fill"`, whatever its class; two children claiming the same load index is an error.

**Clones.** Like a house, a nested grid stands in for one connection point: an LV net has one slack bus, so it can hang off exactly one parent load.
A grid placed on several loads (a list, or `"fill"`) is therefore cloned once per index, and every clone is named after the load it sits on: `lv-grid_0` at `placement: [1, 3]` becomes `mv-grid_0.lv-grid_1` and `mv-grid_0.lv-grid_3`.
The whole subtree under the grid is replicated for every clone, with the clone's name as prefix — `mv-grid_0.lv-grid_3.house_4`, `mv-grid_0.lv-grid_3.house_4.hems_0` — and each clone's houses are split per LV load as in any other grid.
The clones share one net: it is read from the template node's dotted tree id (`mv-grid_0.lv-grid_0`), and every clone's command points at it with `--net_key`, so the federate name and the net key differ.
The same goes for the subtree's data: a house's `exogenous_data` is stored once per clone.

A grid that only exists once (a single feeder, with its own layout) is simply its own node in the tree with its own `layout` and an int `placement`.
It is still named after its load, so `lv-feeder_0` at `placement: 5` runs as `mv-grid_0.lv-feeder_5`, and its net is stored under `mv-grid_0.lv-feeder_0`.

Several nested grids under one parent can have different layouts; each template node is its own net.

`config/experiment-MV-LV.yml` is the MV/LV reference: a synthetic MV net as the root, an LV template placed on a list of MV loads (so it is cloned, subtree included), a unique LV feeder on another load, and an MV load player with `placement: "fill"` for the loads nobody else claims.

#### Coupling contract

The LV grid's slack bus stands in for the MV load, and the two grids exchange power and voltage through HELICS like a house does.
composegen reads the LV net before wiring and refuses to continue unless:

- the LV net has **exactly one `ext_grid`** — the parent's voltage drives that one slack, and there is no way to say which of several it means;
- the `vn_kv` of the MV bus the load sits on **equals** the `vn_kv` of the bus the LV `ext_grid` sits on (relative tolerance 1e-6), checked against each load a clone is placed on.

The voltage base has to match because voltage travels as per unit (`res_bus.vm_pu` out, `ext_grid.vm_pu` in).
An LV slack rated for 10 kV under a 20 kV MV bus would silently scale every LV voltage by two.
The stock Kerber layouts have a 10 kV slack, so re-rate them to the MV voltage with `tools/make_lv_variant.py` (see [Tools](#tools)); the committed `*_20kv.xlsx` files are the result for a 20 kV MV net.

The keys a coupling adds, for a clone `mv-grid_0.lv-grid_3` on MV load 3:

| Key | Type | Direction | Meaning |
|---|---|---|---|
| `mv-grid_0/lv-grid_3/load_3/active_power` | double, W | LV → MV | the LV slack's `res_ext_grid.p_mw`, positive = drawn from the MV grid; includes the losses inside the LV grid |
| `mv-grid_0/lv-grid_3/load_3/reactive_power` | double, VAr | LV → MV | the LV slack's `res_ext_grid.q_mvar`, same sign and losses |
| `mv-grid_0/load_3/voltage` | double, pu | MV → LV | the MV bus voltage at load 3, written to the LV `ext_grid.vm_pu` before each solve |

The MV grid applies the LV power to load 3 as it would a house's, and the LV grid keeps publishing `mv-grid_0/lv-grid_3/load_<n>/voltage` to its own children.
Until the MV grid has published, the LV net keeps its own slack setpoint.

### Placement

`placement` is a pandapower **load index**, a list of them, or `"fill"`, and it is required for everything that moves power: `load`, `house`, `pv`, `battery`, and a grid nested under another grid.

The grid federate identifies incoming power purely by the `load_<idx>` segment of the HELICS key.
A federate wired onto a bare `active_power` key is silently dropped from the power flow, which is why composegen refuses to guess here.

- `"fill"` takes every load index no sibling claimed explicitly. At most one child per grid may use it.
- An index that does not exist in the net is an error, and so is a placement that resolves to nothing.
- Load indices left unclaimed are legal — the grid zeroes every static load at import, so they sit at 0 W — but composegen prints them, because an unclaimed index looks exactly like a typo.

A **house** simulates one building, so `placement: [4, 6]` becomes two independent federates named after the load each drives: `house_4` and `house_6`, not `house_0_*`.
A **nested grid** is cloned the same way, with its subtree (see [Nested grids](#nested-grids)).
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

The run length checked is the resolved duration, however the times were given.
A house with a HEMS needs more than the run itself: at the last step the MPC still asks for a full forecast window, so it needs a further 24 steps (`transform.MPC_FORECAST_HORIZON_STEPS`) beyond the end of the run.
A CSV longer than needed is fine.

### What is in `data/input/` today

| File | Use |
|---|---|
| `sample_data.csv` | house `exogenous_data`; the working example |
| `sample_house.csv` | load player `electrical_load`; the working example |
| `kerber_landnetz_freileitung_1.xlsx`, `kerber_landnetz_kabel_1.xlsx` | grid `layout`, exported by `tools/pandapower2xlsx.py`; LV nets with a 10 kV slack |
| `kerber_landnetz_freileitung_1_20kv.xlsx`, `kerber_landnetz_kabel_1_20kv.xlsx` | the same nets re-rated to a 20 kV slack by `tools/make_lv_variant.py`; the LV grids for a 20 kV MV net |
| `mv_synthetic_20kv.json` | grid `layout`: a synthetic 20 kV MV net (110 kV infeed, HV/MV trafo, two radial feeders, 6 loads) built by `tools/make_synthetic_mv.py`; the committed stand-in for a real MV net |
| `heeten_building_df14.csv`, `ee_day_ahead_prices_2018_till_2020.csv` | measured inputs to `tools/sample_data_generator.py`, not usable directly |
| `building_timeseries.csv`, `h25_profile_2025_power.csv` | orphans — the schema of neither matches what a house or a load player reads |

## Tools

| Script | Purpose |
|---|---|
| `tools/make_lv_variant.py` | re-rates an LV layout to another slack voltage so it can hang off an MV load: `--input`, `--output` (`.xlsx` or `.json`) and `--slack-kv`. Buses, lines, loads and indices stay as they are; the slack bus `vn_kv` and the trafo's HV winding change. It runs a power-flow smoke test and says whether the result attaches to every load of the synthetic MV net. |
| `tools/make_synthetic_mv.py` | rebuilds `data/input/mv_synthetic_20kv.json` and checks it against the committed 20 kV Kerber variant |
| `tools/grid_contract.py` | the coupling check the two scripts use; composegen carries its own copy of the same rules |

The scripts need pandapower; `tools/make_synthetic_mv.py` has the `docker run` line for the pinned image.

## Timing

Every federate shares one period and no offset, so a simulation step has the same `sim_time` everywhere.
Within a step, HELICS allows exactly one federate to wait until all the others have finished (`wait_for_current_time_update`); composegen gives that slot to the **root grid**, so it solves from the loads published in the same step.
Nested grids never get it, and with several root grids (a `plz`-only query can resolve to many) nobody does, so every root grid solves on the previous step's loads.

A nested grid therefore applies its own children's loads one step late and takes its parent's voltage from the previous step.
That adds up to **one step of lag per grid level below the root**: a house on an LV grid reaches the MV power flow one step late, a house on a grid two levels down two steps late.
Loads placed directly on the root have no lag.
Closing the loop within a step needs HELICS iteration, which is not implemented (a later phase of issue #65).

## What a run writes

`databases/db-access.txt` covers how to reach the databases and what the tables look like.
This is what to expect *inside* them.

### Federate names

A federate is named by its dotted path from the root: `local-grid`, `local-grid.loadhouse_0`, `local-grid.house_4`, `local-grid.house_4.hems_0`.
For a house or a nested grid the last segment is the load index it drives, not the id declared in the YAML: `mv-grid_0.lv-grid_3`, `mv-grid_0.lv-grid_3.house_4`.

### HELICS keys

A key is `<publisher federate name, dots replaced by slashes>/<topic>`.
Federates never build these themselves — they discover them from the CST federation config at startup, because the names come from the tree rather than from a federate's own name.

| Topic | Type | Published by | Example key |
|---|---|---|---|
| `load_<idx>/active_power` | double, W | whatever is placed on that load | `local-grid/house_4/load_4/active_power` |
| `load_<idx>/reactive_power` | double, VAr | same | `local-grid/loadhouse_0/load_7/reactive_power` |
| `load_<idx>/voltage` | double, pu | the grid | `local-grid/load_4/voltage` |
| `state` | string, JSON | a house | `local-grid/house_4/state` |
| `control` | string, JSON | a HEMS | `local-grid/house_4/hems_0/control` |

The voltage is the bus voltage in per unit (`res_bus.vm_pu`), not volts.
The extra keys of an MV/LV coupling are in the table under [Coupling contract](#coupling-contract).

The `state` payload carries the thermal vector, battery SoC/SoH, storage temperatures, and heat-pump and AC power — it is what the HEMS reads to plan against.

### Provenance

The scenario document in the metadata store carries the git commit and a verbatim copy of the experiment YAML, so a stored run can be traced back to what produced it.
Generated artefacts land in `generated/`.
