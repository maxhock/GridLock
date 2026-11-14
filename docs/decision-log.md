# Decision log

Why GridLock is built the way it is.
Each entry states a decision as it stands today and the reasoning behind it.
AGENTS.md says what the rules are; this log says why, so read the entry before changing a rule it covers.

## How to keep it

Change the log in the same pull request as the change that makes or alters a decision.
When a decision changes, rewrite its entry to the new decision, and move the old approach to **Rejected** with the reason it lost.
Entry numbers are stable: never renumber, and give a new decision the next free number.
State reasons in your own words and mark where they come from with a citation, such as (a7fecd6), (PR #71) or (issue #45), instead of quoting the source.
A reason that has no written source lists `maintainer` under **Sources**.

```markdown
### D<nn>: <short title>

**Decision.** What holds today, in one to three sentences.

**Why.** The reasoning, with the incident or measurement behind it where there was one.

**Rejected.** Alternatives that were tried or considered, and why each lost.

**Sources.** PR #, issue #, commits.
```

## Architecture

### D01: A HELICS co-simulation in Docker, with the whole topology generated

**Decision.** GridLock couples its simulators through HELICS publications and subscriptions only.
Every federate class is its own Docker image, and a generator turns one experiment YAML into the HELICS wiring, the CST metadata and `docker-compose.yaml`.
Hand-written compose files are not committed to main.

**Why.** HELICS was compared with other co-simulation frameworks such as Mosaik and is the one that can carry the project's compute demands long term.
Distributed compute was a set goal from the start, and HELICS supports it natively through its network stack.
Docker serves the same distributed goal, and it decouples and stabilises each federate's environment for the long term: the grid can need another Python version than the house, and both still run.
A hand-written compose file bypasses the generator; properly integrating a federate means building its container through composegen (PR #19).

**Rejected.** Mosaik and other co-simulation frameworks, which could not carry the long-term compute demands.
Running every simulator in one shared Python environment.

**Sources.** cf4a55f, PR #19, maintainer.

### D02: Federates are built on the CoSim Toolbox

**Decision.** Every federate subclasses CST's `Federate`, and composegen builds the federation with CST's `FederationConfig`, `FederateConfig` and `DockerRunner`.
Federates read their configuration from the CST metadata store, not from files or long command lines.
`federates/template/` is a working, pinned and tested federate shaped like `federates/grid/main.py`, and new federates are copied from it.

**Why.** We want to reuse existing code as much as possible and that means conforming to CST's pattern if possible.

**Rejected.** Raw HELICS API code in each federate.
`runner.json` files in `config/tmp/` plus command-line arguments (PR #26), replaced by CST metadata in 2a0984e.
Deleting the template, which had been listed for removal (d741713).

**Sources.** ecf8dcc, 4535256, PR #26, e12f480, 2a0984e, PR #48, issue #23, issue #38, d741713, 0c79104, cd0843a, PR #62, maintainer.

### D03: Federates discover their HELICS keys

**Decision.** A federate never builds its own HELICS keys.
It finds them in the CST federation config at startup (`_find_pub_key`, `_find_sub_key`, or the load player's key scan).

**Why.** composegen derives key names from the tree, not from a federate's own name.
Federates that guessed their key names missed what composegen actually wired (a67caf3).
It pays off on renames: the control key was renamed with no federate change, because both ends find the key by its suffix (0fc1fda).

**Rejected.** Keys built from the federate's own name.

**Sources.** a67caf3, 0fc1fda.

### D04: composegen is an extract, transform, load pipeline, and only load writes

**Decision.** `extract.py` reads, `transform.py` validates and normalises, `load.py` resolves and emits, run in that order by `main.py`.
Extract and transform never write and never touch the CST store, so new validation goes in `transform.py` and new output in `load.py`.
Placement resolution and the grid coupling check stay in `load.py`, because they need the net tables that only exist after the infdb stage.

**Why.** This structures the code to a known pattern, LoC per file is reduced and responsibilities are nicely split.

**Rejected.** One monolithic `main.py`.
An eight-module split by concern (PR #41, closed).
Resolving placement in transform: `transform.expand_grid_nodes` is that abandoned attempt.

**Sources.** 04ee680, a7f0d02, PR #37, PR #41, e11cbd1, AGENTS.md, maintainer.

### D05: Grids are resolved once, in their own stage, into the metadata store

**Decision.** The infdb stage resolves every grid in the tree, from an InfDB location query or a local layout, and writes each net into `custom_metadata/<net key>`.
Federates read nets only from there, so nothing but this stage needs InfDB access.
InfDB nets are keyed `<id>_<plz>_<kcid>_<bcid>`, layouts by their dotted tree id.

**Why.** Both composegen and the federates need the grid structure, so it is fetched once (issue #45).
The key carries the location so that grid names stay clear when only some grids are run (cf2716f).

**Rejected.** Each consumer fetching the grid itself.

**Sources.** 2ed991a, 69e49b5, cf2716f, PR #46, issue #45.

### D06: Configuration and results live in the two CST databases

**Decision.** Metadata (scenarios, federations, nets, datasets) goes to MongoDB and timeseries to TimescaleDB, chosen per experiment with `use_meta_db` and `use_data_db`; `json` writes into `generated/` instead.
Results exist only in the timeseries store; there is no export to `data/output`.

**Why.** Databases keep run data for the long term and let an experiment's configuration be replicated (issue #27).
Nothing ever wrote to `data/output/`, and every run is queryable by its own scenario (8a9d03f).

**Rejected.** JSON and raw files (issue #39).
A results export (H5 in the archived punch list).
A silent default for `use_meta_db`: today it falls back to `json`, a leftover from the CST integration that is to be changed (#56 H2).

**Sources.** efdb39a, d20b7c4, PR #48, issue #27, issue #39, 8a9d03f, issue #56, maintainer.

### D07: Every run is its own scenario, with provenance

**Decision.** Each run gets the scenario `<name>_YYYYMMDD_HHMMSS`, and the federates run under it.
The git commit, the experiment path and the full experiment YAML are stored with it, merged in after `write_config`, which would overwrite them.
A run whose metadata cannot be stored fails.

**Why.** With one fixed scenario name, two months of runs piled up as 13.1M rows under a single scenario tag, next to 118 orphaned scenario documents (069a304).
A run must be reproducible from what is stored with it.

**Rejected.** A constant `<name>Scenario` for every run.

**Sources.** 5b27083, 069a304, cbd0a3a, PR #79.

### D08: composegen creates the timeseries tables before the federation starts

**Decision.** Stage 3 creates the analysis schema and every CST timeseries table with CST's own DDL, at the address the federates use, and fails if it cannot.

**Why.** Concurrent `CREATE ... IF NOT EXISTS` from every federate races in Postgres, so the first run into a new `general.name` could lose a federate's records and still exit 0.
Measured: 1 of 8 runs complete without pre-creation, 8 of 8 with it (PR #68).

**Rejected.** Patching CST's writer in every federate image.

**Sources.** e0a970b, b789e14, PR #68, issue #67.

## Experiment configuration

### D09: One experiment YAML, a tree of federates

**Decision.** An experiment is one YAML file whose `federation:` key holds a recursive tree of federates (`id`, `name`, `class`, `config`, `sub_federates`), read with `yaml.safe_load` into a `treelib.Tree`.
Anything that changes per experiment is stated once in it; static settings live in the federate's folder or in code.
Input data lives in `data/input/`, apart from configuration.
The old flat `federates:` format is deprecated and stays only until the composegen cleanup.

**Why.** Code, static input tables and per-experiment configuration are kept apart (PR #1).
A tree shows the structure of the federation at a glance and matches the current electricity-only scope.
Whether it also carries multi-energy experiments is open: issue #44 asks whether it should become a DAG, so that electrical, heat and information networks can share houses.

**Rejected.** The flat OmegaConf `federates:` format with `${...}` interpolation, which in the end was not as flexible as hoped; its federates died on startup and nothing used it (22fd53a).

**Sources.** cdbd2b3, PR #1, PR #19, e12f480, 04ee680, PR #48, 22fd53a, issue #44, maintainer.

### D10: One env file for the system, and a static preflight compose

**Decision.** Database hosts, ports, credentials and the InfDB connection live in `config/preflight.env`, read by a static `docker-compose.preflight.yaml`.
The host port of the CST Postgres is a per-machine setting (`CST_POSTGRES_PORT`, 5432 in the example), and the federates reach the database on that same port; only the database container itself always listens on 5432.

**Why.** Preflight is a static task: it starts the simulation's databases if they are not up, and fetches from InfDB the grids the experiment needs.
Nothing in it depends on the experiment's topology, so it needs no generated file.
Whether InfDB pulls later move into composegen is open.

**Rejected.** A dynamic `preflight.sh`, and several env files (`.env`, `cosim.env`).
A fixed host port 5433, chosen to avoid port conflicts (5154522), since the port is now set per machine.

**Sources.** b562596, f490021, 8d5dcb3, 5154522, maintainer.

### D11: A grid comes from a layout file or an InfDB location, never a default

**Decision.** A root grid names exactly one source: a local pandapower layout in `data/input/` (`.xlsx` or `.json`), or an InfDB `location`.
A nested grid must come from a layout, given directly or as a file found through `placement: filename`.
With no source the run fails instead of falling back to a built-in grid.

**Why.** A file-defined grid lets composegen derive the grid's size itself (issue #13).
JSON because real MV nets arrive as pandapower JSON exports (fc44a52).
A nested `location` is refused because an InfDB query returns an unknown number of LV feeders that do not map onto the parent's loads (b7add08).

**Rejected.** A grid generated in code from a pandapower default.

**Sources.** 8b6125f, 7157a6b, issue #13, ca6b619, fc44a52, b7add08.

### D12: The run window

**Decision.** `start_time` and `end_time` are absolute times (a number is seconds after 2023-01-01T00:00:00); `duration` is the length.
Any two fix the third, `duration` alone runs from 0, and all three together are refused.
In tree mode the rule lives only in `composegen/run_window.py`, which the parity tools load by path.

**Why.** `end_time` used to mean two things, and its consumers agreed only when `start_time` was 0 (b4d63c4); a CSV sized for 82,800 s passed the check for an 86,400 s run (#56 N4).
All three together are refused even when they agree, as a safety: a third value can only repeat the other two or contradict them, and a contradiction points to an inconsistency in how the run was thought through.

**Rejected.** A numeric `end_time` as a length added to `start_time`.
Accepting all three when they agree.

**Sources.** b4d63c4, PR #66, issue #56, maintainer.

### D13: time_step is a whole number of seconds

**Decision.** `time_step` must be a whole number of seconds; a whole float is narrowed and a fraction is refused.

**Why.** CST's `HelicsMsg.verify` type-checks `period` against its int default with exact type equality (852dd43).

**Rejected.** Sub-second steps; not supported by CST.

**Sources.** 852dd43.

### D14: Experiments live under config/

**Decision.** `run.sh` resolves an experiment path relative to `config/`, subdirectories included, and refuses one outside it.

**Why.** `config/` is what the containers mount, so nothing outside it can be read.
Mapping every experiment to its basename broke `config/private/` (a883cb9).

**Rejected.** Rewriting the path to `/config/<basename>`.

**Sources.** a883cb9.

## Placement and HELICS keys

### D15: Anything that moves power is placed on a pandapower load index

**Decision.** `load`, `house`, `pv`, `battery` and nested `grid` are placed on pandapower load indices, by int, list or `"fill"`, and publish `.../load_<idx>/active_power` and `/reactive_power`.
The grid identifies power purely by the `load_<idx>` segment right before the quantity.
`pv` and `battery` follow this scheme once they have their own federates; until then they are refused directly under a grid (D18).

**Why.** The loads a pandapower net already carries are where the grid expects power to be drawn or injected; there is no point placing a federate where the grid has no load.
A federate on a bare bus with a plain `active_power` key is silently dropped from the power flow (B1, b107324).
Generation fits the same scheme, since a PV plant is a negative load and a battery a load of either sign (87708e1).

**Rejected.** `placement` and `fill_remaining` on grid nodes with `node_x/P` keys (PR #30).
A rule-based HELICS connector container (issue #2).

**Sources.** d8bba41, PR #30, 17af6b6, 27c7dc2, b107324, 87708e1, 7458430, issue #29, maintainer.

### D16: One federate per load, grouped by parent

**Decision.** Every federate placed on loads drives exactly one load, and is named after it: `placement: [4, 6]` becomes `house_4` and `house_6`.
Load players follow the same rule, one federate per load, run as threads in the container their siblings share (D27).
Not implemented yet for load players: today a load player replays one profile across all its indices as a single federate.

**Why.** A house simulates one building, so one federate publishing several load keys would multiply its energy (b107324).
Load players should follow the same strategy as every other class rather than be the exception.
The cost that once argued for one federate across many loads, a container and a process per federate, is gone since same-image siblings share a container as threads (D27, PR #72).

**Rejected.** One house federate publishing several load keys.
One load-player federate holding many loads, the current state, which is to be replaced.

**Sources.** b107324, issue #74, PR #72, maintainer.

### D17: Unclaimed load indices sit at 0 W and are reported

**Decision.** A load no federate claims stays at 0 W, and composegen names it in its report.

**Why.** pandapower solves the identical network either way, and a dummy would cost a container, a HELICS federate, a broker slot and two timeseries keys per step, making the federate count depend on grid size.
The report exists because an unclaimed index looks exactly like a placement typo (R6).

**Rejected.** Generated zero-power dummy federates.

**Sources.** 9564b28, archived punch list R6.

### D18: PV and battery live inside the house

**Decision.** PV and battery physics run inside the EnergySim house for now.
Under a house they are skipped with a note; directly under a grid `validate_tree` refuses them.
Two house models are meant to coexist, the EnergySim house and an OCHRE house (issue #64), and both are to move their energy components out into their own federates.

**Why.** The house's own simulator already contains that physics (b1ccb3d).
Generated on their own they would start a house container with no dataset that fails with an unrelated message.
Two house models give users a choice, and OCHRE adds an established simulator next to the newly built one.
Components in their own federates can be swapped for power-hardware-in-the-loop when needed.

**Rejected.** Standalone pv and battery federates today: not built yet, so they are refused rather than started broken.
A single house model.

**Sources.** b1ccb3d, 87708e1, e11cbd1, PR #77, issue #64, maintainer.

### D19: A HEMS or controller drives one house and reads that house's data

**Decision.** `hems` and `controller` are one federate, allowed only directly under a house.
It reads its house's own dataset (`--house_federate`), and a house with a HEMS needs a dataset covering the run plus the forecast horizon, checked in composegen.

**Why.** Anywhere else it fell through to generic wiring and exited on an argparse error (cd84031).
With a compiled-in dataset, the MPC silently optimised a building that was not the one being simulated (961b021), and padding a short dataset ran the simulation on invented forecast data without a warning (f1a628d).

**Rejected.** A dataset baked into the controller image; repeating the last sample to fill the horizon.

**Sources.** 961b021, 2c8e4b1, f1a628d, cd84031, PR #78.

### D20: Grids nest under grids like loads

**Decision.** A nested grid is placed on parent load indices; a list or `fill` clones it with its subtree, one per load, all running the template's net (`--net_key`).
`filename` instead makes one grid per `load_<idx>.json` file, each with its own net.
It publishes its `res_ext_grid` draw on the parent's `load_<idx>` keys and writes the parent's voltage into its single `ext_grid`.
composegen refuses an LV net without exactly one `ext_grid` or whose slack `vn_kv` differs from the parent load bus; `tools/grid_contract.py` copies that check on purpose.

**Why.** The coupling is generic between parent and child, so an HV level on top can later be added the same way, and the parent grid needs no change (#65).
Voltage travels in per unit, so mismatched base voltages would silently scale every LV voltage (6ac73c1).
The check is copied because composegen and the tools run in different images.
`filename` exists because hundreds of sub-grids had to be listed one by one, and it names no substation (0fc7ad8).

**Rejected.** Registering an LV grid twice under one name, which dropped its wiring.
Auto-placing InfDB LV grids under an MV grid, left for later.

**Sources.** b7add08, 6ac73c1, 0fc7ad8, issue #65, PR #66.

## Grid physics

### D21: pandapower power flow

**Decision.** The grid federate runs a pandapower AC power flow (`runpp`) each step, with `run_control=True` whenever the net carries controllers such as an OLTC.

**Why.** Grid elements are defined together in one file (issue #4).
Controllers survive the JSON layout and the infdb round trip, and each MV island must regulate its own busbar, since one shared HV bus cannot hold the three islands' different setpoints (PR #71).

**Rejected.** A plain sum of loads.
An optimal power flow in the grid federate, which PR #9 announced: it is not possible in this form, since the loads are set by other federates, and would rather come out of central control with perfect foresight.
PyPSA is a separate, unwired federate (D37).

**Sources.** 1dd4eb7, PR #9, issue #4, 4c7fc32, PR #71, maintainer.

### D22: Federates publish W and VAr; the grid converts

**Decision.** Federates publish power in W and VAr, and the grid divides by 1e6 before writing pandapower's MW and MVAr.

**Why.** CST defines each link between federates with a single unit, so HELICS unit conversion is not available through it.
The goal is to patch it back in later.

**Rejected.** Subscribing in MW and letting HELICS convert from kW (f1a6159).

**Sources.** f1a6159, 3cbd531, a67caf3, maintainer.

### D23: Only federates drive the grid, at constant power

**Decision.** At import the grid zeroes every static load and sgen, sets each load's `scaling` to 1 and all ZIP shares to 0.

**Why.** DSO exports carry static sgens (about 135 MW of PV in a real MV net) that would otherwise inject constant power while every load sits at 0 W (9e64bf6).
A scaling other than 1 silently rescales what a federate publishes.
Federates publish what they actually draw, so a ZIP share applies voltage dependency twice: at 0.98 pu, 100 % constant-Z drew 0.9604 of what the LV grid reported (7387157).

**Rejected.** Keeping the exported static injections, scaling and ZIP shares.

**Sources.** 9e64bf6, 7387157, PR #66.

### D24: Voltages are published for load buses only

**Decision.** The grid publishes voltages only for buses that carry a load.

**Why.** Load buses are the physical interfaces to other federates that need to react to the voltage; other buses are passive (issue #40).

**Rejected.** All bus voltages; a JSON measurement package is left for later.

**Sources.** issue #40.

### D25: Every grid publishes its slack draw

**Decision.** Each root grid gets source-only `<grid>/active_power` and `/reactive_power` keys, and a grid federate without exactly one of each fails at startup.

**Why.** The root grid has no parent, so it got no keys, published nothing, and its HV/MV draw had to be recomputed after every run (PR #70).

**Rejected.** Recomputing the draw after the run.

**Sources.** 9f9be43, PR #70.

## Timing

### D26: One time axis; only a single root grid waits for the current time

**Decision.** All federates share one period with no offset.
The root grid alone uses `wait_for_current_time_update`, and only when there is exactly one root grid.
Each grid level below the root adds one step of lag, accepted until HELICS iteration (#73).

**Why.** HELICS allows the slot on one federate only.
On the grid, every power flow uses the same step's loads; on the house, every power flow would run on the previous step's loads, which is the worse trade for a grid tool (#55).

**Rejected.** Offsets staggered by tree depth: no two federates shared a `sim_time`, every run overran `end_time`, and CST rejects float offsets (2c243be).
Shrinking `time_step`, which only mitigates the lag and does not remove it (#55).

**Sources.** 6ff54c2, 2c243be, 5f931e7, issue #55, issue #65, issue #73.

## Containers and runtime

### D27: Same-image federates under one parent share a container

**Decision.** Federates of an image in `GROUPED_IMAGES` (grid, house_player) that share a parent run in one Compose service, one thread each; a failing thread ends the container.
Every other image is one container per federate.

**Why.** One container per grid loads pandapower, numba and CST once per grid (about 166 MB): the full real federation of 509 federates needed about 66 GB and stopped at 236 containers.
Grouped, it runs as 258 services at about 51 MB per grid thread, with bit-identical outputs (PR #72).
Grouping per parent rather than per class still splits a run into several containers, which keeps it open to distributed compute (D01).
A per-class mode for single-machine runs may be worth adding.

**Rejected.** One container per federate, the CST default: the minimal way to get CST running, not a goal.
Separate processes inside one container, as CST's `define_sh` runs them, which still ran out of memory.
One container per class launching instances from `runner.json` (PR #26).

**Sources.** issue #8, PR #26, 04ee680, 58d51a7, PR #72, maintainer.

### D28: The compose file is shaped by monkeypatching CST

**Decision.** `composegen/monkeypatch.py` replaces CST's `DockerRunner._service`, `DockerRunner.define_yaml` and `FederateConfig.docker`; everything that changes the generated compose file lives there.
The file has no static IPs or subnet, and federates reach the broker by the service name `helics`.

**Why.** A fixed 10.5.0.0/16 meant two experiments could not run at once, could collide with a VPN, and past about 253 federates emitted 10.5.0.256 (189ff4c).
A monkeypatch is the minimal change while the needed changes are still settling; they are to go upstream into CST later.

**Rejected.** CST's per-container static IPs.
Changing CST upstream now, before the changes are settled.

**Sources.** 04ee680, 6238d6e, 189ff4c, maintainer.

### D29: Each image is built once

**Decision.** Only the first service of an image carries `build:`; the others use `pull_policy: never`, so a missing image fails instead of being pulled from Docker Hub.
This is interim: images are meant to be built once, hosted in a registry and pulled.

**Why.** With 194 services, stage 4 started 194 builds and BuildKit failed with a `no such job` error (a7fecd6).

**Rejected.** A `build:` section on every service.

**Sources.** a7fecd6, PR #69.

### D30: The broker and the run's failure handling

**Decision.** The broker is the plain `helics_broker` binary with `--global_disconnect`.
Stage 4 runs with `--abort-on-container-failure` and an optional `--timeout`, and images are built before it, outside the timeout.

**Why.** Without `--global_disconnect`, a federate that reaches its stop time disconnects at once and a sibling's last publish is dropped with an `unknown route` error (113bcac).
A dead federate used to hang its siblings until an external timeout; aborting on failure rather than on exit lets federates that exit 0 flush their final writes (2c51946).
Builds take longer than the run itself (e1c0e50).

**Rejected.** A Python broker wrapper; `--abort-on-container-exit`; building inside `up`.

**Sources.** 07627cf, 113bcac, 2c51946, e1c0e50.

### D31: One GridLock checkout per host

**Decision.** Images and the Compose project are not namespaced, so two checkouts on one host collide.

**Why.** The namespacing was not worth the container and image names it produced (1b8aee9).

**Rejected.** `gridlock-*` image tags and a `gridlock-<analysis>` project name, implemented and reverted.

**Sources.** cb07f38, 1b8aee9.

### D32: Stale nets are kept between runs

**Decision.** infdb does not clear `custom_metadata` between runs.

**Why.** A re-run can then skip the InfDB round trip (R12).

**Rejected.** Clearing per run, at the cost of a fetch every run.

**Sources.** archived punch list R12, issue #56.

### D33: generated/ belongs to the invoking user

**Decision.** infdb and composegen run as the host user, and `run.sh` checks the ownership of `generated/`.

**Why.** Output owned by root needed sudo to inspect, edit or delete (cd1fd00).

**Rejected.** Running as root.

**Sources.** cd1fd00.

## Policies

### D34: Fail loudly rather than default silently

**Decision.** A missing, unsupported or unwritable value raises where the problem is, and names it.

**Why.** A run that reports success on stale, zero or substituted data is the failure this project keeps running into (AGENTS.md).
A diverged power flow once republished stale voltages, and the co-simulation still ran to completion and reported success (45cd089).

**Rejected.** Warnings plus defaults, the earlier behaviour in every case.

**Sources.** 45cd089, e5e499c, 9564b28, 13e57c8, a3a2803, cbd0a3a, 672ca2e.

### D35: Every running dependency is pinned

**Decision.** Every image that runs pins exact versions, EnergySim to a commit, and host tools share one pinned `tools/requirements.txt`.
infdb is pinned through uv and a committed `uv.lock`.

**Why.** Otherwise the house's thermal, battery, PV and MPC models could change between two runs of the same experiment at the same GridLock commit, and the stored `git_commit` would misdescribe the run (d741713).

**Rejected.** EnergySim at `@main`; unpinned tool installs.

**Sources.** d741713, 5eac4c3, a61ede7, 0c79104.

### D36: A public repository, with real grids kept private

**Decision.** Tracked files carry no credentials or internal hosts.
Real DSO grids and their experiments live only in the git-ignored `data/input/private/` and `config/private/`; tests and CI use committed synthetic nets.

**Why.** Real DSO grids cannot go into a public repository (1842566), and data from external companies needs their written consent (issue #31).
The repository started private, but was always planned to go public, so the real grids were never committed: they are private data, not meant to be shared.

**Rejected.** Committing real grids while the repository was still private, which would have put them in the history of a repository meant to go public.

**Sources.** d6c787b, 1842566, a883cb9, issue #31, maintainer.

## Unwired federates

### D37: PyPSA and forecasting are kept, though not part of a run

**Decision.** `federates/grid-pypsa/` and `federates/forecasting/` stay in the repository.
They are not wired into tree mode and are not pinned yet; both must be wired and pinned before a run uses them.

**Why.** PyPSA was meant as a drop-in alternative to pandapower (issue #32); forecasting as a skeleton for a forecasting service (issue #18).
A federate that no current experiment uses can still be needed by a future one.

**Rejected.** Deleting them (#56 H1).

**Sources.** PR #42, PR #52, issue #32, PR #19, issue #18, issue #56 H1, maintainer.

## Testing, CI and tooling

### D38: Tests run inside each image, plus end-to-end and parity checks

**Decision.** Each component has a `test` build stage, so `docker build --target test` runs its tests in the image it ships in.
`e2e-test.sh` checks that a run's own scenario wrote rows, and parity checks compare published voltages with an independent pandapower solve.

**Why.** Components import sibling modules by plain name, as they run in their own image.
A run that succeeds but writes nothing is a silent failure (24bdd81), and the parity check catches wiring and unit-conversion bugs that an exit code or a row count cannot (0543b01).

**Rejected.** A compose-based suite, deleted because its runner referenced a file missing from the repo and could not report a failure (cc65fa7).

**Sources.** ad60c64, 24bdd81, 0543b01, cc65fa7, PR #60, issue #58.

### D39: CI on pull requests, lint first

**Decision.** CI runs lint, then unit, e2e and e2e-parity, each gating the next, on pull requests only.
Locally, pre-commit runs black, ruff and mypy at line length 88.

**Why.** A push to a branch with an open PR also fires `pull_request`, so both triggers ran every Docker job twice (b820151).
A CI that is always red gates nothing (f84ee29), so it was removed until the tests were rebuilt.

**Rejected.** Push plus pull_request triggers.

**Sources.** PR #20, f84ee29, f606705, b820151, PR #60.

### D40: Agent guidance lives in AGENTS.md

**Decision.** AGENTS.md is the single source for coding agents and CLAUDE.md only imports it; README is for humans, and `docs/experiment-reference.md` for experiment authors.

**Why.** The two files had drifted far enough apart to mislead; Claude Code does not read AGENTS.md natively, and a symlink checks out as a plain file on Windows without Developer Mode (5c6f5e5).

**Rejected.** A CLAUDE.md symlink.

**Sources.** 3144567, 5c6f5e5, PR #59.

### D41: Windows support through run.ps1

**Decision.** `run.ps1` behaves identically to `run.sh` for Docker Desktop on Windows, both change together, and it is to be tested.
Today it is not: it never runs in CI, and the two still differ on `--timeout` (#56 N19).

**Why.** Windows support (430a297); drift once broke `--cleanup-dbs` on Windows (2b06510).

**Rejected.** An untested `run.ps1` that is allowed to drift from `run.sh`.

**Sources.** 430a297, 2b06510, issue #56, maintainer.
