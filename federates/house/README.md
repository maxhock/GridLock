# House federate

Simulates one building — thermal envelope, heat pump, air conditioner, battery, PV — and publishes the power it draws from the grid.

Physics comes from [EnergySim](https://github.com/Hosseini97/EnergySim), pinned to a commit.

## What it does

One house federate is one building on one pandapower load index.
`placement: [4, 6]` on a house node therefore produces two independent federates, `house_4` and `house_6`, each with its own sub-federates.

Each step it reads one row of its exogenous dataset, applies whatever action its HEMS published, advances the EnergySim simulator, and publishes:

```
total_load_w = base_load + heat_pump + air_conditioner + battery - pv_generation
```

Sign convention is positive-consumption, and reactive power is always published as zero.

## HELICS interfaces

| Direction | Key | Type |
|---|---|---|
| out | `<house>/load_<idx>/active_power` | double, W |
| out | `<house>/load_<idx>/reactive_power` | double, VAr (always 0) |
| out | `<house>/state` | string, JSON |
| in | `<house>/<hems>/control` | string, JSON |
| in | `<grid>/load_<idx>/voltage` | double, V |

The voltage subscription is registered but not read: the house simulates open-loop and does not react to grid conditions.

Keys are discovered from the CST federation config by suffix (`_find_pub_key` / `_find_sub_key`), never constructed — composegen derives the names from the experiment tree, not from the federate's own name.

`state` is a versioned JSON record: the grid exchange, the load breakdown by device, the action applied, the serialised simulator state (thermal vector, battery SoC/SoH, storage temperatures, heat-pump and AC power), and the exogenous row.
The HEMS reads battery SoC out of it; everything else is there for analysis.

At `t=0` the house publishes a zero-load state so the grid's first power flow has something defined to solve.

## Configuration

From the experiment YAML, only `exogenous_data` has any effect — the CSV is read by composegen and stored in `custom_metadata/<house federate>`, and the house materialises it at startup.
The dataset is resampled onto the federation's `time_step` (coarser data averaged, finer interpolated), so it does not have to match.

**`model` does nothing.** `setup_simulator` takes a `config_path` and ignores it.
Every house is the hardcoded two-room RC network in `build_my_house.py`, with a 13 kWh battery and a 4 kW heat pump and AC from `common_config.py`.
`federates/house/config.yaml` is dead for the same reason — nothing reads it, and it describes a different thermal model than the one that runs.
Tracked as S1 in issue #56.

Required columns for the exogenous CSV are in [`docs/experiment-reference.md`](../../docs/experiment-reference.md); they are decided by EnergySim's `SimulationDataset`, not by this repository.

## Files

| File | Purpose |
|---|---|
| `main.py` | the federate |
| `build_my_house.py` | the RC thermal network every house simulates |
| `common_config.py` | the device configs every house shares |
| `exogenous_data.py` | materialising and resampling the dataset |
| `requirements.txt` | pinned, including EnergySim to a commit; shared with the `controller` image |
| `config.yaml` | dead — see above |
| `run_simple_simulation.py`, `run_fake_controller.py`, `start_broker.py` | debugging aids, not part of a run |

The image builds from the **repository root**, not this directory, because the controller reuses this package.

## Running one by hand

```bash
CST_USE_META_DB=json python3 house/main.py --scenario <name>_<timestamp> --federate_name <dotted house name>
```

Its exogenous dataset has to already be in the metadata store, so run stages 2 and 3 first.
`run_simple_simulation.py` skips HELICS entirely and exercises just the physics.
