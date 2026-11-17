# Reinforcing grid federate

The [grid federate](../grid/README.md), with one addition: it reinforces its own net during the run whenever a line, a transformer or a bus voltage is outside its limits.
An experiment asks for it with a `reinforcement` block on a grid node; the class stays `grid`, so placement, nesting and keys work exactly as for any grid.
[`docs/experiment-reference.md`](../../docs/experiment-reference.md#grid-reinforcement) has the keys.

## What it does

Each step it applies its inputs and solves the power flow, as the grid federate does.
If a limit is violated it then reinforces the net, re-solves, and repeats until every limit holds, and only then publishes.
Children therefore always see the voltages of the grid as it now stands, and the measures stay in the net for the rest of the run.

The rules are those of [eDisGo](https://github.com/openego/eDisGo) (`flex_opt/reinforce_grid.py`, `reinforce_measures.py`), reimplemented on pandapower's tables in `reinforce.py`; eDisGo itself is AGPL-3.0 and built on PyPSA, so none of its code is used.
The order is eDisGo's: overloading, then voltage issues in MV, at the MV/LV station busbars and in LV, then overloading once more.
Each of these loops stops after `max_iterations` rounds; if a limit is still violated the run fails and names the elements.

| Problem | Measure |
|---|---|
| Transformer overloaded, and one more of its kind covers the missing power | `parallel` + 1 |
| Transformer overloaded beyond that | replaced by as many standard transformers as the load needs |
| Standard line overloaded | parallel standard lines, `ceil(overload × parallel)` |
| Single non-standard cable overloaded by less than 2× | one parallel cable of its own type |
| Any other line overloaded | replaced by as many parallel standard lines as carry its load |
| Station busbar voltage out of band | one more standard transformer |
| Feeder voltage out of band | the line into the bus at 2/3 of the way to the farthest critical bus is reattached to the station busbar as a standard line over the whole distance |
| ... and that bus is the feeder's first | lines on the path are reinforced one at a time, best R·I gain per kEUR first, until half the excess drop is gone; the next round takes the rest |

Standard equipment is eDisGo's, as pandapower standard types: `NAYY 4x150 SE` in LV, `NA2XS2Y 1x240 RM/25 12/20 kV` at 20 kV and `NA2XS2Y 1x185 RM/25 6/10 kV` at 10 kV, `0.63 MVA 20/0.4 kV` or `0.63 MVA 10/0.4 kV` for MV/LV stations, and `40 MVA 110/20 kV` or `40 MVA 110/10 kV` for HV/MV.
A level outside this catalogue fails the run when it first needs reinforcing.

Costs are eDisGo's, in kEUR: cable 9 per km in LV and 20 in MV, cable with earthwork 60 / 100 per km in LV and 80 / 140 in MV (rural / urban), 10 per MV/LV and 1000 per HV/MV transformer.
Earthwork is paid once per line; a second cable in the same trench costs only the cable.

### Where it differs from eDisGo

- **Voltage limits in MV are relative to the feeding busbar**, as LV limits are to their station, rather than absolute around 1.0 pu.
  eDisGo assumes the HV/MV station holds its busbar at 1.0 pu; where it does, the two agree.
  Where it does not, as on the synthetic MV net without an OLTC, the absolute band blamed the cables for the transformer's drop and piled up 58 parallel cables.
- **Reinforcement reacts step by step.**
  eDisGo sizes each measure for the worst time step of a whole run; here a measure is sized for the step that first violates a limit, and a later, higher peak adds to it.
  The result depends on the order of the load profile, and can cost more than sizing for the peak would.
- **Each grid federate reinforces only its own net.**
  In a nested federation the MV grid sees an LV reinforcement one step later, through the LV grid's slack draw, and every clone of an LV template is reinforced on its own.
- **There are no house connections.**
  eDisGo neither splits feeders inside buildings nor reinforces house connections; pandapower has no such notion, so every LV bus counts.
- **MV feeders split at stations or loads.**
  eDisGo splits MV feeders only at LV stations.
  GridLock's MV nets often stand in for a station with a load, so a load bus counts too.
- Three-winding transformers, impedances and HV buses are not checked; nor is N-1.

### Limits of reacting during the run

- Reinforcement needs a solved power flow to start from.
  A load jump large enough to make the unreinforced net diverge fails the step before any measure can run, as it does in the grid federate; a ramp, or more frequent steps, lets the net grow with the load.
- Feeder measures assume radial feeders, as eDisGo's do.
  Path reinforcement weighs each line by its current from the last power flow and holds it fixed while choosing; in a meshed net reinforcing one line shifts current onto others, so a round can do more or less than planned until the next power flow corrects it.
- A replaced transformer keeps its row, so a stored tap controller (`ContinuousTapControl`, `DiscreteTapControl`) keeps working on it; the standard type brings its own tap range, and the controller re-sets the tap position on the next power flow.

## HELICS interfaces

All the grid federate's keys, plus five source-only publications that exist to be recorded.
The loading and voltage metrics describe the net as the step's load found it, before any measure, so a violation shows in the timeseries; the voltages the grid publishes to its children are those after reinforcement.

| Key suffix | Type | Value |
|---|---|---|
| `max_line_loading` | double, % | highest `res_line.loading_percent` |
| `max_trafo_loading` | double, % | highest `res_trafo.loading_percent` |
| `min_vm` | double, pu | lowest bus voltage |
| `max_vm` | double, pu | highest bus voltage |
| `reinforcement_cost` | double, kEUR | everything spent since the run started |

## What a run leaves behind

At the end of the run the federate writes `custom_metadata/<scenario>__<federate name>__reinforced`, keyed by the run's timestamped scenario like its timeseries, so every run keeps its own report.
It holds the scenario, the settings, the total cost, every violation found before a measure (time, element, index, `value` as loading in % or voltage in pu, and the `limit` it crossed), every measure (time, element, index, action, reason, severity, cables or transformers added, standard type, cost), and the reinforced net as pandapower JSON (`net_json`).
Violations and measures are separate lists because one measure can answer many violations: a split feeder fixes every bus behind it.
The net's loads and sgens are zeroed, as at import; load the JSON with `pandapower.from_json_string` and save it into `data/input/` to run it again as a `layout`.

## Files

| File | Purpose |
|---|---|
| `main.py` | the federate: a subclass of the grid federate's `GridFederate` |
| `reinforce.py` | the rules, with no HELICS in them |
| `Dockerfile` | built from the repository root; reuses `federates/grid/main.py` and `requirements.txt`, so the power flow and its pins cannot drift from the grid federate's |

## Running one by hand

```bash
CST_USE_META_DB=json python3 main.py --scenario <name>_<timestamp> --federate_name <grid federate name> --net_key <custom_metadata key of its net> --reinforcement max_loading_percent=100,voltage_band=split,earthwork=rural,max_iterations=20
```

`grid_federate.py` must sit next to `main.py`, as it does in the image.
Every key of `--reinforcement` is required: composegen fills in the defaults.
Repeat the `--federate_name`/`--net_key`/`--reinforcement` triple to run several grids in one process; composegen does this for the reinforcing grids under one parent, which share a container.

## Tests

```bash
docker build -f federates/grid_reinforce/Dockerfile --target test .
```
