# HEMS controller federate

Model-predictive home energy management for one house.
This is the `hems` class in an experiment YAML (`controller` maps to the same image).

## What it does

Each step it reads the battery SoC out of the house's published `state`, solves an MPC over a forecast window, and publishes a battery power command back to that house.

The solver is EnergySim's `JAX_MPC_Solver`, running a **battery-only** optimisation.
Heat pump, air conditioner and thermal storage are not part of it and are left at zero — the same scope this had before it was split out of the monolithic house simulation.

Before the house has published a usable state it publishes a zero action rather than guessing.

## HELICS interfaces

| Direction | Key | Type |
|---|---|---|
| in | `<house>/state` | string, JSON |
| out | `<house>/<hems>/control` | string, JSON — `{"battery_power_w": ...}` |

Keys are discovered by suffix (`_find_pub_key` / `_find_sub_key`).
A HEMS is only recognised nested **directly under a house**; one placed a level deeper is wired but never gets its container arguments, and dies on startup (N8 in issue #56).

## The forecast

The controller reads **the house's own exogenous dataset**, fetched from `custom_metadata/<house federate>` — which is why composegen passes `--house_federate`.
Reading any other dataset would mean optimising against a building that does not exist; this used to be a hardcoded path, and the two agreed only by coincidence of file naming.

`--horizon` is passed by composegen and defaults to 24 steps.
That number is owned by composegen, because it is the side that validates each controlled house's dataset covers the run *plus* the window (`transform.MPC_FORECAST_HORIZON_STEPS`).
If the window would run past the end of the dataset the federate raises rather than shortening it: the QP is built for a fixed horizon, and publishing a zero action instead would be a battery that quietly stops being controlled.

`control_strategy` and `accessible` in the experiment YAML are validated and then discarded — the strategy is always this MPC.

## Files

| File | Purpose |
|---|---|
| `main.py` | the federate; the whole class lives here |
| `Dockerfile` | builds from the **repository root** and reuses `federates/house/` and its `requirements.txt` |

This is the one federate that is not self-contained: it imports `house.common_config` and `house.exogenous_data` so its simulator template and its dataset handling are identical to the house it drives.

## Running one by hand

```bash
CST_USE_META_DB=json python3 controller/main.py \
  --scenario <name>_<timestamp> \
  --federate_name <dotted hems name> \
  --house_federate <dotted house name>
```
