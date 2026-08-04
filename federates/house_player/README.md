# Load player federate

Replays a measured or synthetic load profile from a CSV onto one or more pandapower load indices.
This is the `load` class in an experiment YAML.

Use it wherever a building's behaviour does not need to be simulated — a whole feeder can be driven by load players, with a simulated `house` only where the study needs one.

## What it does

Unlike a house, a load player is **one federate covering all of its indices**: it reads one profile and publishes the same value to every load it was placed on.
`placement: "fill"` therefore gives one federate driving every otherwise-unclaimed load in the grid with an identical profile — 113 of them in `experiment-LV.yml`.
That is a known structural limitation, not a bug.

Each step it looks up the current simulation time in the profile and publishes.
Lookup holds the nearest previous sample rather than interpolating, and returns the first row for anything before it.

At `t=0` it publishes the profile's first value, so the grid's initial power flow sees realistic loads rather than zeros.

## HELICS interfaces

| Direction | Key | Type |
|---|---|---|
| out | `<player>/load_<idx>/active_power` | double, W |
| out | `<player>/load_<idx>/reactive_power` | double, VAr |

One pair per placed index.
Keys are discovered from the CST federation config after `create_federate` (`_build_pub_keys`) — anything ending in `/active_power` or `/reactive_power` — because composegen derives the names from the experiment tree rather than from this federate's own name.

composegen also registers a `load_<idx>/voltage` subscription for each of its indices, but the federate does not read it — the profile is played back open-loop.

## Input CSV

| Column | Required | Unit |
|---|---|---|
| `timestamp` | yes | Unix epoch seconds or ISO 8601 |
| `base_load` | yes | W |
| `reactive_power` | no | VAr, zero if absent |

Epoch timestamps are shifted so the first row sits at `t=0`, matching HELICS granted time.
The CSV is the one input in this project passed as a **file path** (`--timeseries`) rather than through the metadata store, so it is read from the `data/` mount at runtime.

composegen refuses to start if the profile is shorter than the run: without that check the player would silently hold its last value once the data ran out, producing a flat load and a run that still reported success.
See [`docs/experiment-reference.md`](../../docs/experiment-reference.md) for the two known holes in that check.

## Files

| File | Purpose |
|---|---|
| `main.py` | entry point and CLI |
| `src/load_player.py` | the federate and the timeseries helpers |
| `requirements.txt` | pinned: HELICS 3.6.1, CST 1.0.1, pandas, numpy |

## Running one by hand

```bash
CST_USE_META_DB=json python3 main.py \
  --scenario <name>_<timestamp> \
  --federate_name <dotted federate name> \
  --timeseries data/input/sample_house.csv
```

`.vscode/launch.json` has a working argument set.
