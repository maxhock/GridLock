# Grid federate

Solves the pandapower power flow for one low-voltage grid.
It is the root of every experiment tree and the only federate that sees the whole network.

## What it does

At startup it reads its pandapower net out of the CST metadata store under `custom_metadata/<its own federate name>`, where the infdb stage wrote it — it never talks to InfDB or reads a workbook itself.

The imported net is then sanitised (`sanitize_net_for_power_flow`):

- Missing ZIP-load columns are added with zero defaults, because nets serialised by older or external pandapower versions omit columns `runpp` now expects.
- **Every static load is zeroed.** Demand in this project comes from HELICS, so an imported `p_mw` would double-count. This is also why an unclaimed load index is harmless: it simply stays at 0 W.
- **Every load's `scaling` is reset to 1.0.** A federate publishes the actual power of its load and pandapower multiplies `p_mw` by `scaling`, so any other value (DSO exports use 0, 0.3, 0.7) would silently change what was published. A note reports how many loads were rescaled.
- **Every static `sgen` is zeroed** (`p_mw` and `q_mvar`; `in_service` is untouched). Only federates drive the grid, and a static sgen would otherwise inject constant feed-in while the loads sit at 0 W. A loud note reports the count and the nominal MW removed. This runs even for a net without loads.

Each step it applies the powers it received, runs `runpp`, and publishes each driven load's bus voltage.
Key roles are classified once, on the first step (`_GridKeys`), because CST fills the key dicts after construction.

## HELICS interfaces

| Direction | Key | Type |
|---|---|---|
| in | `<child>/load_<idx>/active_power` | double, W |
| in | `<child>/load_<idx>/reactive_power` | double, VAr |
| out | `<grid>/load_<idx>/voltage` | double, V |

Incoming power is matched by the `load_<idx>` segment alone (`_LOAD_RE`), and the index is a pandapower load index.
A federate that publishes a bare `active_power` key is silently ignored — which is why composegen refuses to generate one.

Values above `1e40` are HELICS's "no data yet" sentinel and are skipped.

## As a child of another grid

A grid federate can hang off a load of a parent grid, exactly like a house, so an MV grid can feed LV grids.
The roles follow from direction and key suffix; no extra config is needed.

| Direction | Key suffix | Meaning |
|---|---|---|
| in | `/voltage` | the parent's per-unit voltage at the load this grid sits on; drives this grid's slack |
| in | `/active_power`, `/reactive_power` | a child's load, as always |
| out | `/active_power`, `/reactive_power` | this grid's total draw, published as the parent's load (`<parent>/<grid>/load_<parent idx>/…`) |
| out | `/voltage` | per-load bus voltage, as always |

- Each step the parent's voltage is written to `ext_grid.vm_pu` before `runpp`. `None` and the HELICS sentinel are skipped, so at t=0 the net keeps its own setpoint.
- After `runpp` it publishes `res_ext_grid.p_mw` and `q_mvar` (in W and VAr, positive = drawn from the parent). That includes the transformer and line losses inside this grid, not just the child loads.
- A coupled grid must have **exactly one `ext_grid`**: the parent's voltage drives it, and there is no way to say which of several slacks it means.
- The upstream voltage input, active_power publication and reactive_power publication must all be present or all absent, and each at most once; anything else raises `ValueError` naming the keys.
- A root grid has none of these keys and behaves as before. The parent grid needs no change: it already applies any `load_<idx>/active_power` input to that load.

## Timing

This is the federate that gets the federation's single `wait_for_current_time_update` slot, so it steps after its children have published.
HELICS allows exactly one federate to hold it, so composegen grants it only when the experiment has exactly one grid; with several grids, every grid solves on the previous step's loads.

## Failure behaviour

A diverged power flow raises and fails the run, reporting the simulation time, the total applied P and Q, and pandapower's own message.
It is not caught: swallowing it would leave the previous step's voltages in `data_to_federation`, which would then be published as if current — the co-simulation would report success while every downstream federate reacted to a grid state that was never solved.

## Files

| File | Purpose |
|---|---|
| `main.py` | the federate |
| `requirements.txt` | pinned: pandapower 3.3.2, HELICS 3.6.1, CST 1.0.1, numba |
| `pyproject.toml` | leftover; no build reads it, and it names a different Python version than the image |

## Running one by hand

```bash
CST_USE_META_DB=json python3 main.py --scenario <name>_<timestamp> --federate_name <grid federate name> --net_key <custom_metadata key of its net>
```

The metadata store has to already contain this grid's net — run stages 2 and 3 first.
`.vscode/launch.json` has a working argument set.
