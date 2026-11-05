# Grid federate

Solves the pandapower power flow for one grid.
It is the root of every experiment tree and the only federate that sees the whole network of its level; an experiment may also nest grids under it (see [As a child of another grid](#as-a-child-of-another-grid)).

## What it does

At startup it reads its pandapower net out of the CST metadata store under `custom_metadata/<its `--net_key`>`, where the infdb stage wrote it — it never talks to InfDB or reads a layout file itself.
The key is not the federate name for a nested grid: several clones of one LV template run under different names on the same net (`mv-grid_0.lv-grid_3` and `mv-grid_0.lv-grid_5` both read `mv-grid_0.lv-grid_0`).

The imported net is then sanitised (`sanitize_net_for_power_flow`):

- Missing ZIP-load columns are added, because nets serialised by older or external pandapower versions omit columns `runpp` now expects.
- **Every load is made constant-power** (all `const_z_*_percent` / `const_i_*_percent` set to 0), for the same reason as `scaling`: a federate publishes the power its load actually draws, and a nested grid's draw is already resolved by its own power flow, so a voltage-dependent share would be applied twice. A note reports how many loads were changed.
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
| out | `<grid>/load_<idx>/voltage` | double, pu |

Incoming power is matched by the `load_<idx>` segment alone (`_LOAD_RE`), and the index is a pandapower load index.
A federate that publishes a bare `active_power` key is silently ignored — which is why composegen refuses to generate one.

The voltage is `res_bus.vm_pu` of the load's bus, in per unit rather than volts.

Values above `1e40` are HELICS's "no data yet" sentinel and are skipped.

## As a child of another grid

A grid federate can hang off a load of a parent grid, exactly like a house, so an MV grid can feed LV grids.
The roles follow from direction and key suffix; no extra config is needed.

| Direction | Key suffix | Meaning |
|---|---|---|
| in | `/voltage` | the parent's voltage in pu at the load this grid sits on; drives this grid's slack |
| in | `/active_power`, `/reactive_power` | a child's load, as always |
| out | `/active_power`, `/reactive_power` | this grid's total draw, published as the parent's load (`<parent>/<grid>/load_<parent idx>/…`) |
| out | `/voltage` | per-load bus voltage, as always |

- Each step the parent's voltage is written to `ext_grid.vm_pu` before `runpp`. `None` and the HELICS sentinel are skipped, so at t=0 the net keeps its own setpoint.
- After `runpp` it publishes `res_ext_grid.p_mw` and `q_mvar` (in W and VAr, positive = drawn from the parent). That includes the transformer and line losses inside this grid, not just the child loads.
- A coupled grid must have **exactly one `ext_grid`**: the parent's voltage drives it, and there is no way to say which of several slacks it means.
- The voltage is exchanged in per unit, so the LV slack bus has to be rated like the parent bus the load sits on: the `vn_kv` of both must be equal. composegen refuses to generate the federation otherwise (`_check_coupling`); `tools/make_lv_variant.py` re-rates a layout, and the committed `*_20kv.xlsx` files fit a 20 kV MV net.
- The upstream voltage input, active_power publication and reactive_power publication must all be present or all absent, and each at most once; anything else raises `ValueError` naming the keys.
- A root grid has none of these keys and behaves as before. The parent grid needs no change: it already applies any `load_<idx>/active_power` input to that load.

For a clone `mv-grid_0.lv-grid_3` on MV load 3 the full keys are `mv-grid_0/lv-grid_3/load_3/active_power` and `.../reactive_power` (out, W and VAr) and `mv-grid_0/load_3/voltage` (in, pu). [`docs/experiment-reference.md`](../../docs/experiment-reference.md#nested-grids) has the placement and clone rules.

## Timing

The root grid gets the federation's single `wait_for_current_time_update` slot, so it steps after its children have published.
HELICS allows exactly one federate to hold it, so composegen grants it only when the experiment has exactly one root grid; with several, every root grid solves on the previous step's loads.
A nested grid never holds it. It applies its own children's loads, and its parent's voltage, one step late, so a coupled federation lags by one step per grid level below the root.

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

Repeat the `--federate_name`/`--net_key` pair to run several grids in one process, one thread each.
composegen does this for every set of grids under the same parent, which share one container: a separate process per grid loaded pandapower, numba and CST again for each, about 166 MB per grid before it solved anything.
If one grid fails, the process exits at once, since its siblings would otherwise wait forever for its time grant.
