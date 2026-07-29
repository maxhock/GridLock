# Issues to MVP / 1.0.0

Status legend: **OPEN** / **FIXED** / **PARTIALLY FIXED** / **DEFERRED** / **DROPPED**

Audited end-to-end on 2026-07-28 by running `./run.sh config/experiment-LV.yml` and
`./run.sh config/experiment-local-grid.yml`. Both complete without warnings, which is
why several of the issues below are silent-wrong-result problems rather than crashes.

---

## Blockers

### B1. House federate has no effect on the grid, in either direction — **FIXED** (b107324)
`federates/grid/main.py` matches inputs with the regex `/load_(\d+)/`. The house
publishes `local-grid/house_0/active_power`, so `update_internal_model` hits
`continue` and never writes it into `net.load`. The publish loop filters the same
way, so `house_0/voltage` is never produced either.

Verified in Timescale: every `local-grid/load_*/voltage` key has 26 rows;
`local-grid/house_0/voltage` has zero. Meanwhile `sanitize_net_for_power_flow`
zeroes all static loads and the fill-exclusion (issue 13) correctly removes load
indices 4 and 6 from the load player — so the two buses the house "occupies" carry
0 W for the whole run while the house swings +/-5 kW into the void.

Fix direction: give `house` the same per-load key wiring the `load` class gets in
`_wire_grid_child`, or make the grid resolve federate -> load index by name instead
of by regex on the key.

### B2. Every run writes into the same scenario — **FIXED** (069a304)
`composegen/load.py` builds a timestamped scenario name (`TestGrid_YYYYMMDD_HHMMSS`)
and stores metadata for it, then constructs `FederationConfig(f"{name}Scenario", ...)`
— so the federates all run under the constant `TestGridScenario`.

Verified: `"TestGridAnalysis".hdt_double` held 13,138,220 rows, all tagged
`TestGridScenario`, spanning 2026-06-02 to today, alongside 118 orphan scenario
documents in Mongo that nothing references. Runs cannot be told apart except by
`created_at`, and the table grows without bound.

### B3. A crashing federate hangs the run forever — **FIXED** (2c51946)
Stage 4 of `run.sh` is a bare `docker compose up` — no `--abort-on-container-exit`,
no `--exit-code-from`, no timeout. Stages 2 and 3 both handle this correctly.

Verified by killing the grid federate at startup: the surviving federates sat on the
broker until a 180 s external timeout fired. Nothing exited, and the status `run.sh`
returns is not the simulation's. Same gap in `run.ps1`.

### B4. Federates do not share a time axis — **FIXED** (2c243be, supersedes issue 5)

Fixed by dropping the depth offsets and using HELICS
`wait_for_current_time_update` on the grid. All federates now report the same
`sim_time` values and runs stop exactly at `end_time`.

Two follow-ups came out of it, neither blocking:

- **HEMS optimises from a two-step-stale house state** — GitHub issue #55. The
  flag is a single federation-wide slot (HELICS rejects a second holder), so it
  went to the grid and the house/hems round trip grew from one step to two.
  Proposed fix is dead-reckoning in the controller.
- **Multi-grid federations cannot get same-step coupling at all.** With more
  than one grid federate the slot goes unused and every grid runs its power flow
  on the previous step's loads. HELICS iteration
  (`helicsFederateRequestTimeIterative`) is the only thing that lifts this; it
  would also remove the one-step voltage feedback delay to children and the
  hems lag above. Belongs in the CST federate loop.

Original report follows.


`_offset_for_node` in `composegen/load.py` staggers grant times by the tree-depth
difference in whole seconds, independent of `time_step`. Measured `sim_time` ranges
from the local-grid run:

```
local-grid              0 .. 86402   (offset 2)
local-grid.house_0      0 .. 86401   (offset 1)
local-grid.loadhouse_0  0 .. 86401   (offset 1)
```

After t=0 no two federates ever share a timestamp, so any join on `sim_time` returns
nothing, and every run overshoots `end_time` by the offset. It also does not scale:
the default `time_step` in `transform.py` is `1.0`, where a +1 s or +2 s offset is a
full step or more.

Fix direction: enforce ordering with a mechanism that does not move the clock —
HELICS input ordering, sub-period offsets, or an explicit measure/control two-phase
step.

---

## Serious

### S1. The experiment YAML's physical parameters are silently discarded — **OPEN**
`model: 1R1C / 5 Ohm / 8 F`, `battery: 5kWh / 5 kW`, `pv: capacity / max_production`,
`hems: control_strategy / accessible` — none of it reaches a federate.
`setup_simulator()` in `federates/house/main.py` accepts `config_path` and ignores
it; the real house is the hard-coded 2-room RC network in `build_my_house.py` with a
13 kWh battery and 4 kW heat pump from `common_config.py`.

`validate_tree` checks these fields exist and then drops them, which is worse than
not validating — it signals the config was understood. There is no unit parsing
anywhere, which is why the shipped `capacity: "5 5 kW"` typo passes validation.

### S2. The HEMS forecasts a different dataset than the house simulates — **FIXED** (961b021)
`federates/controller/main.py` hard-codes `tools.sample_data_generator.FILE_NAME`
(`/data/input/sample_data.csv`), while the house pulls its exogenous data from the
metadata store per `exogenous_data:`. They agree today only by coincidence of
naming; change the house's CSV and the MPC silently optimizes against the wrong
forecast.

### S3. Power-flow divergence is swallowed — **FIXED** (45cd089)
`federates/grid/main.py` catches every `runpp` exception, prints, and returns —
stale voltages stay published and the run reports success. A diverged step must
either fail the run or be recorded as flagged/NaN.

### S4. Silent wrong-data fallback for non-CSV load profiles — **FIXED** (e5e499c)

Also rejects a load defined with only `heat_load`, which is equally
unimplemented. Note this makes `experiment-MV-LV.yml` fail validation earlier
than before, on `electrical_load: "H0"` — that config was already broken (H8).

`_resolve_timeseries_path` in `composegen/load.py` maps any non-`.csv`
`electrical_load` — including the `"H25"` / `"H0"` standard-profile names used in
the shipped configs — to `/data/input/sample_house.csv` without a word.

### S5. `generated/` is written as root — **FIXED** (cd1fd00)
Existing installs need a one-time reclaim of old root-owned files; run.sh
detects this and prints the command.

The composegen container leaves `generated/docker-compose.yaml` owned by
`root:root`. Inspecting, cleaning, or regenerating it needs sudo.

### S6. Hard-coded `10.5.0.0/16` subnet and per-federate IPs — **FIXED** (189ff4c)
Two experiments cannot run concurrently on a host, a stale `generated_cst_net`
blocks the next run (hit during the audit), and the range can collide with VPN/VM
ranges. The `cnt`-based IP assignment also caps the federation near 250 federates.

---

## Housekeeping

### H1. Dead code — **DEFERRED**
Kept deliberately for now; revisit before 1.0.

- The entire legacy `extract`/`transform`/`load` path (~400 lines) is unreachable:
  Stage 2 rejects any config without a `federation:` key, so `config/experiment.yml`
  cannot run at all.
- `expand_grid_nodes` and `_clone_subtree_with_new_root` never execute, because
  `_extract_tree_config` sets every `grid_nodes` value to `None`.
- Phase 2 of `load_tree_cst_outputs` and `_add_generic_tree_pubsub_groups` do not
  fire for any shipped config.
- Unused federates: `grid-pypsa`, `forecasting`, `template`, `recorder`,
  `house/run_simple_simulation.py`, `run_fake_controller.py`, `start_broker.py`.

### H2. Config keys that are read by nothing — **OPEN**
`duration:` is read by nothing in any experiment file. `experiment-MV-LV.yml` omits
`use_meta_db` / `use_data_db`, so it silently defaults to `json` while the databases
hold Mongo state — no consistency check.

### H10. Omitting `time_step` crashes composegen — **FIXED** (852dd43)
`transform.py` defaults a missing `time_step` to the float `1.0`, but CST's
`HelicsMsg.verify` type-checks against its defaults with exact type equality and
`period` defaults to the int `1`. So any experiment without an explicit
`time_step` dies with `Diction type '<class 'float'>' not allowed for period`.
Latent today because both shipped configs set it to an int. Found while testing
sub-second offsets, which fail the same check.

### H3. Key nomenclature — **PARTIALLY FIXED** (0fc1fda)
The duplicated house segment is gone: `local-grid/house_4/hems_0/house_4/control`
is now `local-grid/house_4/hems_0/control`. The remaining composite keys
(`<grid>/<federate>/load_<idx>/<quantity>`) were left as-is — each segment carries
distinct information rather than repeating one.

`local-grid/house_0/hems_0/house_0/control` duplicates the house segment, and
`lv-grid_91074_1_4/loadhouse_0/load_58/active_power` fuses grid identity, federate
identity and pandapower index into one opaque string. Worth settling before 1.0
because it is the schema every downstream query binds to.

### H4. Reproducibility / dependency pinning — **FIXED** (d741713)
Only pandapower is pinned. `cosim-toolbox`, `helics`, `pandas`, `numpy`, `jax` and
`git+https://github.com/Hosseini97/EnergySim.git@main` all float — the house
federate's physics can change with no commit on this side, which makes the
`git_commit` field in run metadata misleading.

### H5. No results export — **DROPPED**
Resolved by dropping the `data/output/` notion rather than building an export.
Results live in the CST timeseries store, and since B2 each run is identified by
its own scenario name, so they are queryable there. The `.gitignore` entry for a
directory nothing creates is removed.

Two references remain inside the legacy composegen path (`create_recorder_runner`
and `config/experiment.yml`), which is unreachable and deliberately kept for now
— see H1. `AGENTS.md` still documents `data/output/` in the folder structure; left
alone because that file is not to be edited without a direct instruction.

### H6. Dangling test wiring — **FIXED** (cc65fa7)
The old test suite was removed wholesale rather than repaired, ahead of a rewrite.

`run-tests.sh` references a `docker-compose.test.yml` that is not in the repo; CI
runs pre-commit only (pytest is commented out). Tests are being reworked separately;
this notes the dangling reference only.

### H7. Stray credentials file — **FIXED** (no commit; the file was untracked)
`config/preflight.env.bak` sits untracked next to the gitignored env file with real
credentials in it.

### H8. Nested grids (MV-LV) not implemented — **OPEN**
`config/experiment-MV-LV.yml` ships in the repo but fails in composegen: infdb only
resolves the top-level `federation.config`, so a nested grid has no metadata and the
error surfaces as the misleading `Placement error: pandapower load index 4 does not
exist. Valid indices: []`. Either implement nested grids or mark the config
explicitly as unsupported.

### H9. Test house as well — **OPEN** (was issue 9)
B1 has landed, so the house now genuinely drives the grid and is exercised by
`config/experiment-local-grid.yml`. What remains is test coverage, which falls
under the test rework (H6).

---

## Release pass (audited 2026-07-29)

A second audit, this time of the repository as a release artefact rather than of
simulation results: what a person who is not the author hits when they clone it,
run it, or run it on Windows. Both shipped experiments were re-run from HEAD
first and both complete with exit 0, so — as in the first audit — the entries
below are mostly things that pass silently rather than crash.

### R1. CI fails on every push — **FIXED**
`black --line-length=88 --check .` reformats 19 files and `ruff check` reports 14
errors (unused imports in `federates/house/main.py`, `composegen/transform.py`,
`databases/infdb/main.py`, `federates/controller/main.py`; `F402` loop-variable
shadowing in `composegen/transform.py:393` and `tools/yaml_graph_tui.py:318`;
`F841` in `tools/yaml_graph_tui.py:770`). Every workflow run since 2026-07-22 is
red, so the signal is worth nothing.

Decision: remove the workflow. Formatting stays a pre-commit concern until the
test rework (H6) gives CI something worth gating on.

### R2. No LICENSE, no tag, no CHANGELOG — **OPEN**
244 commits, no license file, no tags. Without a license the default is "all
rights reserved", so nobody outside the group may legally use, publish or build
on the code — which is the opposite of what a research platform release is for.
Blocking for a public 1.0.0; see the licence note at the end of this section.

### R3. README describes a workflow that no longer exists — **DEFERRED**
It documents `config/tmp/docker-compose.yaml`, `num_houses`,
`federates.house.csv`, recorder output under `data/`, and top-level `broker/`,
`grid/`, `house/` folders — none of which exist. It also never mentions
`config/preflight.env`, which `run.sh` requires before anything runs. Explicitly
out of scope for this pass; to be rewritten together with the 1.0 docs.

### R4. `run.ps1` and `run.sh` have drifted apart — **FIXED**
`--cleanup-dbs` now wraps the actual stages, `--timeout` is implemented with a
child process and the same 124 convention, and help, unknown-argument handling,
the ownership check and stage 2's `--exit-code-from` all match. Verified in a
`mcr.microsoft.com/powershell` container: the script parses, `--help` prints and
exits 0, and `--timeout 60 config/experiment-LV.yml extra-arg` consumes the
timeout value, keeps the experiment and reports the stray argument without
aborting.

`--cleanup-dbs` is actively broken on Windows: the `try` block is empty and only
carries a `# Main logic below` comment, so the `finally` stops the databases
immediately and stages 2-4 then run against stopped containers. On top of that
`-h` exits 0 without printing help, `--timeout` (advertised by `run.sh --help`)
does not exist, unknown arguments are rejected where bash warns and continues,
the root-owned `generated/` check is missing, and stage 2 does not pin its exit
code. A Windows user is running a different program.

### R5. The HEMS stops controlling before the run ends — **FIXED**
`_clamped_forecast` repeats the last available sample to fill the window, so the
MPC keeps solving to the last step. Verified against a 30-step dataset with a
24-step horizon: step 0 takes a full window untouched, step 10 gets 20 real
samples plus 4 held ones, the final step holds the whole window, and the QP
solves in every case (previously all of these published `0.0`).

`federates/controller/main.py` publishes `battery_power_w = 0.0` whenever
`step_idx + N_horizon >= max_steps`, where `max_steps` is the length of the
house's exogenous dataset and `N_horizon` is 24. The MPC therefore goes silent
for the last 24 steps of any run whose dataset ends with it — and
`validate_timeseries_coverage` only demands `covered >= duration`, so a dataset
sized exactly to the experiment (precisely what that validator asks users to
provide) yields an MPC that publishes zero for the entire run, at INFO level,
exit code 0. The shipped configs escape only because `sample_data.csv` holds 7
days for a 1-day run.

Fix direction: clamp the forecast window to the tail of the dataset — repeat the
last available sample to fill the horizon — instead of dropping control. The run
still ends at `end_time`; a dataset longer than the experiment is simply not
read past it.

### R6. Loads nothing is placed on, and grids whose load table cannot be read — **FIXED**
`_read_load_list` now raises for a missing entry or a missing `net_json`,
`_resolve_load_placement` refuses to resolve a load-placed child to nothing, and
`_report_unclaimed_loads` names the indices that stay at 0 W. Verified by
running `experiment-local-grid.yml` with the fill load player removed: composegen
reports "11 of 13 load(s) in 'local-grid' are not driven by any federate and stay
at 0 W: [0, 1, 2, 3, 5, 7, 8, 9, 10, 11, 12]", and the run completes with the two
houses as the only sources of load. Leaving connection points empty therefore
works as intended, and no dummy federates were needed to do it.

Two different situations that currently share one silent code path.

*Unclaimed load indices are legitimate.* `sanitize_net_for_power_flow` zeroes
every static load at import, so a pandapower load that no federate claims is
already a zero-power load: the bus stays in the power flow, the topology is
unchanged, and nothing is published for it. That is the intended way to leave a
connection point empty and needs no new mechanism (see the dummy-load argument
below). What it does need is to be *stated* — composegen should report which
indices ended up unclaimed, so an accidental placement typo does not look like a
deliberately empty bus.

*A grid with no readable load table is a defect.* `_read_load_list` prints a
warning and returns `[]` when `custom_metadata` is missing or carries no
`net_json`; `load_indices` then stays `None` (`load.py:1350` guards on
`and all_loads`) and `_wire_grid_child` falls through to the branch that wires a
bare `active_power` key — which the grid's `/load_(\d+)/` regex ignores. The
whole federation then runs with every load at 0 W and reports success. This is
B1's failure mode reachable by a different route, and must be a hard error.

**Argument: should composegen auto-generate a zero dummy load federate?** No.
The point of a dummy load would be to make an empty connection point explicit,
but it buys nothing that zeroing does not already give: pandapower solves the
identical network either way, since a load of 0 MW and no load at that index
produce the same bus injection. Against it: every dummy is a container, a HELICS
federate, a broker slot and two timeseries keys per step, so a 200-load LV grid
with a handful of houses would spend most of the federation publishing zeros —
and a "fill" placement already covers the common case of wanting every load
driven. It would also make the federate count depend on grid size rather than on
what the experiment declares, which is exactly the coupling `placement` exists to
avoid. The cost of *not* having it is that an unclaimed index is invisible, and
that is better addressed by reporting the unclaimed set at generation time, which
is what this issue does. Revisit only if a use case appears that needs a real
timeseries recorded for an empty point.

### R7. A grid-level `pv` is placed on a bus, not a load index — **FIXED**
`pv` and `battery` joined `load` and `house` in `LOAD_PLACED_CLASSES` — the same
argument covers both, since a battery injects and draws power at a connection
point exactly as generation does. Since neither has a federate of its own yet,
generating one is now a `NotImplementedError` naming the reason instead of a
house container that dies on a missing exogenous dataset. Verified: a grid-level
PV at load index 99 is rejected against the grid's valid load indices, and one at
index 5 fails with the not-implemented message; both abort composegen before any
federate starts.

`_resolve_placement` is applied to every child of a grid, but only `load` and
`house` children have their placement resolved against pandapower load indices.
A `pv` child (`experiment-MV-LV.yml` places `pv_0` at 5) is therefore validated
against the load table by the claim loop while being wired as if it sat on a bus,
so it reaches the grid on a key the grid ignores — the same silent path as R6.
Generation belongs on a load index like everything else that injects power; a PV
plant is a negative load.

Note that no standalone PV federate exists: `map_params_to_class("pv")` returns
the *house* image, which would start a house simulation with no `exogenous_data`
and crash on startup. Placement semantics get fixed here; running one has to
fail loudly until the federate exists.

### R8. Generic image names, and a compose project called `generated` — **FIXED**
Images are tagged `gridlock-*` and the generated file carries an explicit
`name:` derived from the experiment's analysis name, so a run of
`experiment-local-grid.yml` produces `gridlock-testgrid-local-grid-1` and
friends. Two checkouts running experiments with the same `general.name` still
share a project — they cannot run at once anyway, since both write the same
generated file — but they no longer overwrite each other's image tags, and
neither does an unrelated project that happens to build an image called `house`.
Old untagged `broker`/`grid`/`house`/`controller`/`house_player` images are left
behind on hosts that ran earlier versions and can be removed by hand.

The generated compose file tags its images `broker`, `grid`, `house`,
`controller`, `house_player` — names with no owner — and Compose derives the
project name from the file's directory, so it is always `generated`. Any other
project on the host that builds an image called `house` or `grid` silently
swaps out a federate image, and two GridLock checkouts collide on both image
tags and container names. S6 removed the fixed subnet that blocked concurrent
runs; this is the other half of the same problem.

### R9. `run.sh` rewrites any experiment path to `/config/<basename>` — **DROPPED**
`./run.sh ~/elsewhere/foo.yml` runs `config/foo.yml` if that exists. Deliberate:
the experiment directory is mounted at `/config`, so an experiment outside it
cannot be read by the containers anyway.

### R10. Stage 2 does not pin its exit code — **FIXED**
Verified after the change by pointing `experiment-LV.yml` at a PLZ that does not
exist (99999): infdb raises "No grids found in InfDB pylovo.grid_result", the
stage aborts, `run.sh` exits 1 and stage 3 never starts.

`run.sh` stage 3 passes `--exit-code-from composegen`; stage 2 relies on
`--abort-on-container-exit` alone propagating infdb's exit code. It does on
Compose v5.3.1 (verified: a service exiting 3 gives `rc=3` either way), so the
"run.sh aborts before stage 3 when infdb fails" fix does hold today — but it is
undocumented behaviour to build an abort path on, and the two stages should not
differ.

### R11. Credentials in the repo — **OPEN**
`databases/db-access.txt` is tracked and spells out passwords (`worker`/`worker`,
`SuperSecret`) plus pgadmin credentials that do not even match
`config/preflight.env.example` (`admin@gridlock.local`/`admin`) — so it is both a
credentials file and wrong. They are development defaults rather than live
secrets, but a public release should carry no password list at all: the file
should describe how to reach the databases and point at the env file for values.
`preflight.env.example` separately hardcodes an internal InfDB host,
`10.162.28.40`.

### R12. infdb never clears `custom_metadata` — **DEFERRED**
Stale grid entries from earlier runs persist, and `discover_grid_federates`
prefix-scans that collection for PLZ-only queries, so a run can pick up a grid
that the current query no longer resolves. Kept deliberately for now: the same
behaviour is what lets a re-run skip the InfDB round trip.

### Licence note (for R2)
Three candidates, all compatible with the dependency stack (HELICS is BSD-3,
pandapower and CST are BSD-3/MIT-family, EnergySim currently ships no licence of
its own — worth resolving before release, since an unlicensed dependency binds
tighter than anything chosen here):

- **MIT** — shortest, permissive, imposes nothing on users. Best if the goal is
  maximum uptake and citation.
- **BSD-3-Clause** — same permissions as MIT plus a no-endorsement clause; it is
  what HELICS and pandapower use, so it is the least surprising choice for a
  co-simulation tool and keeps the whole stack under one familiar licence.
- **Apache-2.0** — permissive plus an explicit patent grant and contributor
  terms. Best if the institution cares about patent protection or expects
  outside contributions.

Copyleft (GPL/AGPL) is possible but would restrict the industrial partners who
typically use grid co-simulation tooling, so it is only worth it if keeping
derivative work open is an explicit goal.

---

## Fixed

1. mongodb volume not clean, so mongodb does not start - FIXED: deleted volume gridlock2_mongo_data
2. infdb can return 0 grids for a location, which is not found until grid federate breaks - FIXED:
   - infdb now validates results and fails immediately with clear error message if no grids found
   - run.sh aborts before Stage 3 when infdb exits non-zero (re-verified 2026-07-28)
   - Use PLZ codes that exist in pylovo.grid_result (e.g., 91074, 91099, 91359 instead of 91301)
3. cst dbs are potentially reset every run - PARTIALLY FIXED, see **B2**: run metadata with
   timestamp, git commit and full experiment YAML is stored per run, but the runtime
   scenario name is still constant so the timeseries are not actually isolated.
4. when not forcing docker compose to abort when the first federate dies logger runs longer, blocking finish (Fixed, removed manual logger)
4b. check if you can also use local grid data. FIXED
5. synchronise clocks and runtimes - superseded by **B4**
6. Load nomenclature not correct 'lv-grid_91074_1_4/loadhouse_0/load_58/active_power' - moved to **H3**
7. unknown route for messages in last timestep of simulation (because house load is done and turns off) - FIXED: each federate calls destroy_federate() (which disconnects it) the instant its own `while granted_time < stop_time` loop ends; since sibling federates (e.g. house and its HEMS) reach that identical stop_time at slightly different wall-clock moments, one could disconnect before the other published its final-timestep message, and the broker silently dropped it. Confirmed via controlled tests that this is unrelated to timeseries length (reproduced identically with a right-sized 2-day dataset). Root cause fixed with HELICS 3.6+'s broker-level `--global_disconnect` flag ("delay disconnection until all are done"), added to the generated `helics_broker` command in composegen/monkeypatch.py - no per-federate code changes needed. Verified clean across repeated runs.
8. The databases are killed after each experiment even though they should stay running and be accessible - FIXED: run.sh now only stops databases if it started them; pre-existing database containers are left running
9. Test house as well - moved to **H9**
10. what happens if the timeseries and experiment timeframe are mismatched? - FIXED: previously the load-player silently held the last CSV value for the remaining runtime; composegen now validates every load timeseries covers the full experiment duration and aborts with a clear error before any federate starts (see composegen/transform.py: validate_timeseries_coverage)
11. make output less verbose (Fixed)
12. add numba for speed up at no loss (Fixed)
13. correctly filter for fill placement - FIXED: "fill" placement in composegen/load.py previously resolved to *all* pandapower load indices in the grid, even ones already explicitly claimed by a sibling federate (e.g. an explicit house at buses [4,6] alongside a "fill" load player), causing both to be wired to the same load index. `_resolve_placement` now excludes indices already claimed by sibling "load" children with explicit placement, and raises a clear error if two siblings explicitly claim the same index.
