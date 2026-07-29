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

### S5. `generated/` is written as root — **OPEN**
The composegen container leaves `generated/docker-compose.yaml` owned by
`root:root`. Inspecting, cleaning, or regenerating it needs sudo.

### S6. Hard-coded `10.5.0.0/16` subnet and per-federate IPs — **OPEN**
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
