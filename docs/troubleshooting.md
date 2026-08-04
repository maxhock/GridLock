# Troubleshooting

GridLock fails loudly on purpose.
Most of the checks below exist because a run that "succeeded" with stale, zero or substituted data is the failure mode this project keeps hitting — so a refusal to start is usually the check working, not a bug.

`run.sh` prints a `=== Stage N ===` banner before each stage, and each stage gates the next.
The banner you last saw tells you which section applies.

The last section is the one to read when nothing failed but the numbers look wrong.

---

## Before stage 1

**`Missing env file: config/preflight.env`**
Copy `config/preflight.env.example` to `config/preflight.env` and fill it in.

**`generated/ still holds files owned by another user`**
Left by runs from before the containers were pinned to the invoking user.
The message prints the `docker run ... chown` command that fixes it.
Without the check this surfaces as a bare `Permission denied` from deep inside a metadata writer.

---

## Stage 1 — CST databases

**The `database` container exits 3 on a fresh clone**, logging `ERROR: database "copper" already exists`.
`databases/cstdb/init-db.sql` runs `CREATE DATABASE copper`, but the Postgres entrypoint has already created `POSTGRES_DB=${CST_POSTGRES_DB}` — and init SQL runs under `ON_ERROR_STOP=1`.
Existing installs survive only because their `cst_postgres` volume predates the file, so this hits precisely the case of someone cloning the repository.
Drop the `CREATE DATABASE` line, or set `CST_POSTGRES_DB` to something other than `copper`.
Tracked as N1 in issue #56.

**You changed the credentials in `preflight.env` and every federate dies on connect.**
Those values configure the database *servers*.
The federates authenticate through CST's own `CST_USER` / `CST_PASSWORD` / `CST_DB`, which the generated compose file does not set — so they still use `worker` / `worker` / `copper`.
`databases/cstdb/init-mongo.js` hardcodes the same pair and `getSiblingDB('copper')`, ignoring `CST_MONGO_DB`.
Keep the defaults until this is fixed (N2 in #56).

---

## Stage 2 — infdb

This stage resolves the grid and writes it into the metadata store.
Nothing else needs InfDB access.

**`Experiment config not found: ...`**
`CONFIG_PATH` is a *container* path. `run.sh` derives it from your argument; if you are running the stage by hand it has to look like `/config/experiment-LV.yml`.

**`Grid configuration must define either 'location' or 'layout', not both.`**
**`No grid source defined in experiment config.`**
A grid names exactly one source.
See [`experiment-reference.md`](experiment-reference.md#grid-source).

**`Local grid layout not found: ...`**
The workbook goes in `data/input/`, and `layout:` is just its file name.

**`InfDB returned no grids for location queries: PLZ ...`**
The postcode has no entry in `pylovo.grid_result`.
Check the PLZ, and that you are pointed at the InfDB instance you think you are.

**Connection failures**
`INFDB_HOST` / `INFDB_USER` / `INFDB_PASSWORD` in `preflight.env` have no public default — they come from whoever operates the instance.
An experiment using `layout:` does not need them at all.

---

## Stage 3 — composegen

This stage is where a bad experiment should die, because nothing has been created yet.

**`Configuration invalid:` followed by a bulleted list**
`validate_tree` collects *every* missing or unsupported field before raising, so fix the whole list in one pass.
The prefix names the class: `[Structure]`, `[Grid]`, `[Load]`, `[House]`, `[PV]`, `[Battery]`, `[HEMS]`.

**`Timeseries coverage invalid:`**
A CSV is shorter than the run.
The message gives what it covers, what is needed, and why.
A house with a HEMS needs 24 steps *beyond* `end_time`, because the MPC still asks for a full forecast window at the last step.
Provide a longer profile or shorten the experiment.
See the two known holes in this check under [Silent failures](#silent-failures).

**`Placement error: pandapower load index N does not exist. Valid indices: [...]`**
The index is a pandapower load index, not a bus.
If the valid list is `[]`, the grid has no loads — with a nested MV/LV config that means the nested grid was never resolved (H8 in #56; `experiment-MV-LV.yml` is not expected to run).

**`Unsupported placement value: None`**
A `load`, `house`, `pv` or `battery` node with no `placement`.
It is required for everything that moves power.

**`Placement 'fill' ... resolved to no pandapower load index.`**
Every load is already claimed by a sibling.
The message lists them.

**`Grid '...' has multiple children with placement 'fill'`**
At most one child per grid may fill.

**`'<node>' is a pv/battery placed directly in grid '...'`** (`NotImplementedError`)
No standalone PV or battery federate exists — that physics lives inside the house simulator.
Declare it as a sub-federate of a house.

**`No custom_metadata entry '<grid>' in backend '<backend>'`**
Stage 2 did not run, or did not run for this experiment.
Stage 3 reads what stage 2 wrote.
If you are running stages by hand, run both, with the same `CONFIG_PATH`.

**`Exogenous dataset for '<house>' not found: ...`**
The house's `exogenous_data` file is missing from `data/input/`.

**`'time_step' must be a whole number of seconds`**
CST type-checks the HELICS `period` against an integer default.
Sub-second steps are not supported.

**`Scenario '...' not found in metadata store.`** at compose-writing time
The scenario document was not written.
Look further up the log — a failed metadata write is currently reported as success, so the real cause is above this line (N12 in #56).

---

## Stage 4 — simulation

Any federate exiting non-zero tears the whole federation down.

**A federate exits 2 immediately**
argparse.
The generated command did not match what the federate requires.
Known case: a `hems` nested deeper than directly under a house, or a node declared `class: controller`, gets the pre-migration CLI and dies (N8 in #56).

**`Missing DB backend env vars. Expected CST_USE_META_DB and CST_USE_DATA_DB`**
You started the federate outside the generated compose file.
Set both, e.g. `CST_USE_META_DB=json`.

**`No grid data found in metadata store for '<name>'. Ensure infdb data setup has run.`**
The grid federate's name has to match the `custom_metadata` key stage 2 wrote — for a location grid that is `<id>_<plz>_<kcid>_<bcid>`, not `<id>`.

**`No exogenous dataset found in metadata store for '<house>'`**
composegen did not store it.
Re-run stage 3.

**`No publication ending in '<suffix>' found`** / **`Multiple publications ending in ...`**
The federate discovers its HELICS keys by suffix and found none, or found an ambiguous set.
This means composegen wired it differently than the federate expects — check the class's branch in `_wire_grid_child`.

**`Power flow did not converge at simulation time Ns`**
Real divergence.
The message carries the total applied P and Q and pandapower's own reason.
It is deliberately fatal: continuing would publish the previous step's voltages as if they were current.

**`Forecast window [a, b) runs past the end of the exogenous dataset`**
The federation was generated against a different MPC horizon than the controller is running with. `--horizon` is passed by composegen and has to match `transform.MPC_FORECAST_HORIZON_STEPS`.

**Every federate dies inside `set_metadata` on `strptime`**
`start_time` or `end_time` was given as a string that is not exactly `YYYY-MM-DDTHH:MM:SS`.
composegen passes strings through unvalidated (N13 in #56).
Use numbers.

**`CSV ... missing required 'timestamp' column`** / **`'base_load'`**
A load player's profile.
Required columns are in [`experiment-reference.md`](experiment-reference.md#input-data).

**The run hangs**
Use `./run.sh --timeout 600 ...`; `run.sh` exits 124 and tears the federation down.
A federate that crashed before entering executing mode leaves its siblings blocked on the broker.

---

## Messages that are not failures

- **`N of M load(s) in '<grid>' are not driven by any federate and stay at 0 W: [...]`** — legal, since the grid zeroes every static load at import. Printed because an unclaimed index looks exactly like a placement typo.
- **`Skipping '<node>' (pv|battery): not yet implemented as a standalone federate`** — expected under a house.
- **`Note: N grid federates in this federation.`** — HELICS allows only one federate to wait for the current time update, so with several grids each solves on the previous step's loads.
- **`Normalized epoch timestamps to simulation-relative seconds`** — a load player shifting its CSV so the first row sits at `t=0`.

---

## Silent failures

Cases where the run exits 0 and the results are wrong anyway.
All are tracked in issue #56; they are listed here because no error message will point you at them.

**Physical parameters in the YAML are ignored.**
`model`, `capacity`, `power`, `max_production`, `control_strategy`, `accessible` are validated and discarded.
Every house is the same hardcoded two-room network with a 13 kWh battery and a 4 kW heat pump.
Changing them changes nothing (S1).

**The coverage check can be switched off by accident.**
It returns early and silently if `start_time` or `end_time` is a string, so an ISO 8601 time means no CSV is validated at all (N5).
It also sizes the run as `end_time - start_time` while a numeric `end_time` is a duration *from* `start_time`, so a non-zero `start_time` under-checks (N4).
Both reinstate the sample-and-hold this check exists to prevent.

**A house past the end of its dataset re-publishes its last values.**
CST does not reset `data_to_federation` between steps, so the stale load and state are logged as fresh rows every step (N7).

**Power can land on the wrong load.**
The grid matches the *first* `/load_<n>/` in a key.
A child with `id: load_1` placed on load 58 publishes `.../load_1/load_58/active_power`, and the power is applied to load 1 (N3).
Avoid `load_<n>` as a node id.

**The `sim_time = 0` row comes from a zero-load solve.**
The grid's initial power flow runs before its siblings are guaranteed to have published, so t=0 voltages are solved from a partially or fully empty load table while the children's own t=0 rows show real power (N6).

**A stale grid can be picked up.**
infdb never clears `custom_metadata`, and PLZ-only queries prefix-scan that collection, so a run can find a grid a previous query resolved.
Kept deliberately — it is also what lets a re-run skip the InfDB round trip (R12).

**One load player drives every index it fills with the same profile.**
`placement: "fill"` is a single federate, so `experiment-LV.yml` runs 113 identical loads.
Structural, not a bug.

---

## Getting more detail

Stages 2 and 3 can be run on their own — see the commands in [`AGENTS.md`](../AGENTS.md#commands).

A single federate can be run on the host against a JSON metadata store from a previous generation:

```bash
CST_USE_META_DB=json python3 main.py --scenario <name>_<timestamp> --federate_name <dotted name>
```

`.vscode/launch.json` has working argument sets, and each `federates/*/README.md` documents its own.

`databases/db-access.txt` covers querying a run's timeseries, including how to tell one run's scenario from another's.
