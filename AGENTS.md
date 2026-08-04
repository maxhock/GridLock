# AGENTS.md

Instructions for coding agents working on GridLock. This is the single source of
truth for agent-facing guidance; `CLAUDE.md` imports this file. Do not modify it
unless explicitly asked to.

`README.md` is the human-facing overview — concept, tech stack, quick start. This
file covers what you need in order to change the code.

## Concept

GridLock is a HELICS co-simulation platform for power grids and the buildings on
them. Every federate class is its own Docker image, and federates communicate only
through HELICS publications and subscriptions. A single experiment YAML is turned
into a complete federation — HELICS wiring, CST metadata, and a
`docker-compose.yaml` — by a generator container, so no part of the topology is
written by hand.

## Commands

Everything runs in Docker; no host Python environment is required.

```bash
cp config/preflight.env.example config/preflight.env   # one-time; fill in InfDB creds
./run.sh                                   # runs config/experiment-LV.yml by default
./run.sh config/experiment-local-grid.yml  # a specific experiment
./run.sh --timeout 600 config/...yml       # abort a hung federation
./run.sh --cleanup-dbs config/...yml       # stop the CST databases on exit
docker compose -f generated/docker-compose.yaml down --remove-orphans   # teardown
```

`run.ps1` is the Windows equivalent and is kept deliberately in step with `run.sh` —
change both.

`run.sh` is four stages; run them individually when debugging:

```bash
DC="docker compose --env-file config/preflight.env -f docker-compose.preflight.yaml"
$DC up -d --wait database mongodb        # 1: timescale + mongo (CST stores)
$DC up --build --abort-on-container-exit --exit-code-from infdb --no-deps infdb        # 2
$DC up --build --abort-on-container-exit --exit-code-from composegen --no-deps composegen  # 3
docker compose -f generated/docker-compose.yaml up --build --abort-on-container-failure  # 4
```

Stages 2 and 3 must both run before stage 4: stage 3 reads what stage 2 wrote.
`CONFIG_PATH` (a container path, e.g. `/config/experiment-LV.yml`) selects the
experiment for stages 2–3; `run.sh` derives it from the CLI argument.

Optional DB inspection UIs (pgadmin, mongo-express, grafana) sit behind a profile:
`$DC --profile cst-tools up -d`.

Single federates can be debugged on the host against a JSON meta store — see
`.vscode/launch.json` for working argument sets (`--scenario`, `--federate_name`,
`CST_USE_META_DB=json`).

### Lint and format

`.pre-commit-config.yaml` runs mypy, ruff (`--fix`, line length 88) and black
(line length 88). Format before committing.

## Architecture

### The four stages

1. **CST databases** — Timescale/Postgres holds run timeseries, Mongo holds metadata
   (scenarios, federations, `custom_metadata`). Backends are chosen per experiment
   via `general.use_meta_db` / `use_data_db`; `json` writes into `generated/`
   instead of Mongo.
2. **infdb** (`databases/infdb/main.py`) — resolves the grid. Either fetches nets
   from an external InfDB by location query (`plz`/`kcid`/`bcid`) or loads a local
   pandapower workbook from `data/input/`. Writes each net as JSON into the CST
   metadata store under `custom_metadata/<grid federate name>`. Federates read it
   from there at runtime, so nothing but this stage needs InfDB access.
3. **composegen** (`composegen/`) — an ETL over the experiment YAML:
   `extract.py` → `transform.py` → `load.py`, orchestrated by `main.py` (see below).
   Produces the CST federation/scenario documents and `generated/docker-compose.yaml`.
4. **simulation** — the generated compose file, one container per federate plus a
   `helics` broker service.

### Inside composegen: the ETL

Turning an experiment description into a running federation is the only real
transformation in the project, so it is split into three phases, one module each,
run in order by `main.py`:

- `extract.py` — **read**. Dispatches on the config's top-level key, builds the
  `treelib.Tree` for tree mode, and checks that each grid names exactly one source
  (`layout` or `location`). Produces an `ExtractedConfig`.
- `transform.py` — **validate and normalise**. `validate_tree` collects every missing
  or unsupported field before raising, `validate_timeseries_coverage` rejects input
  CSVs that are too short, `process_general_config` normalises the times, and
  `wire_pub_sub` derives each node's publications/subscriptions from its parent/child
  pairs. Produces a `TransformedConfig`.
  `wire_pub_sub` is not where a working federation's keys come from, though — see
  below.
- `load.py` — **resolve and emit**. Reads the nets infdb wrote, resolves `placement`
  onto pandapower load indices, registers the CST groups, stores each house's
  exogenous CSV, and writes the federation, the scenario document and
  `generated/docker-compose.yaml`.

Each module exposes one public function named after its phase, which dispatches on the
config mode to a private per-mode implementation behind it. One function sits in the
wrong file: `transform.generate_run_metadata` is never called by `transform()` - both
branches of `load.py` import it and call it themselves.

**Extract and transform never write, and never touch the CST store.** Every output and
every database access is in `load.py`. That is what makes a bad experiment fail before
anything exists to clean up, and it is the rule to preserve: new validation belongs in
`transform.py`, new output in `load.py`.

Topic wiring is split across both phases, and the transform half is the *fallback*.
`wire_pub_sub` records topics on every tree node, but `load.py` registers the real CST
groups itself in `_wire_grid_child` — per `load_<idx>` keys for anything placed on a
load, plus the house `state` and HEMS `control` groups — and marks those nodes handled.
`_add_generic_tree_pubsub_groups` then registers `wire_pub_sub`'s topics for whatever is
left over. In both working experiments that is nothing, so changing `wire_pub_sub` alone
changes no key a federate actually sees.

Placement breaks the phase split, and looks misplaced until you need to change it:
resolving it needs the grid's load table, which exists only once `_read_load_list` has
read what the infdb stage wrote, so it cannot happen any earlier than `load.py`.
`transform.expand_grid_nodes` is the abandoned attempt to do it in the transform phase.

### Experiment config: two formats

`extract.py:extract` dispatches on the top-level key:

- `federation:` → **tree mode**, the current format. A recursive tree of federates
  (`id`, `name`, `class`, `config`, `sub_federates`) parsed into a `treelib.Tree`.
- `federates:` → **legacy mode**, a flat dict resolved with OmegaConf, emitting
  `runner.json` files into `config/tmp/`. Deprecated and known-broken
  (`config/experiment.yml` still routes here and produces federates that die on
  startup); do not build on it. It also writes its compose file to
  `config/tmp/docker-compose.yml`, which `run.sh` never reads — stage 4 only ever brings
  up `generated/docker-compose.yaml`.

Tree configs are read with `yaml.safe_load`, **not** OmegaConf — so `${...}`
interpolation does not resolve there and would be passed through as a literal
string. Interpolation only works in legacy configs.

`config/experiment-LV.yml` (InfDB location) and `config/experiment-local-grid.yml`
(local layout, houses + HEMS) are the two working tree-mode references.

`docs/experiment-reference.md` documents the tree-mode schema for users: every key, the
required CSV columns per class, and the HELICS keys a run emits. Keep it in step when
you change `validate_tree`, `_wire_grid_child`, or what a federate reads from a CSV.

### How placement and HELICS keys fit together

This is the core invariant of the project.

The grid federate identifies incoming power purely by the `load_<idx>` segment of
the HELICS key (`federates/grid/main.py:_LOAD_RE`). So anything that injects or
draws power — `load`, `house`, `pv`, `battery` (`LOAD_PLACED_CLASSES` in
`composegen/load.py`) — must be wired onto a **pandapower load index**, not a bare
bus. A federate that publishes a plain `active_power` key is silently dropped from
the power flow, which is why composegen raises rather than defaulting in these paths.

`placement` accepts an int, a list of ints, or `"fill"` (every load not claimed by
an explicit placement). At most one `"fill"` child per grid.

Expansion differs by class (`_expand_child_instances`):
- a **house** simulates one building, so `placement: [4, 6]` becomes two independent
  federates named after the load they drive — `house_4`, `house_6`, not `house_0_*`;
- a **load player** replays one profile across all its indices and stays a single
  federate holding all of them.

Unclaimed load indices are legal (the grid zeroes all static loads at import, so they
sit at 0 W) but are reported, since they look identical to a placement typo.

All of this resolution happens in `load.py` phase 1 (`_resolve_placement` /
`_resolve_load_placement`). `transform.expand_grid_nodes` looks like it does the same
job on buses, but it is unreachable: `extract` sets every entry of `grid_nodes` to
`None` — for local layouts as well as InfDB locations — and that function skips a
`None`. Its bus-based expansion, its own duplicate/`fill` checks, and
`extract._read_layout_buses` are all dead. Change placement behaviour in `load.py`, or
the change will have no effect.

### Timing

Every federate shares one period and no offset, so a simulation step has the same
`sim_time` everywhere. Step ordering uses HELICS `wait_for_current_time_update`,
which HELICS permits on exactly one federate in the federation; composegen gives that
slot to the grid, and only when there is exactly one grid federate
(`_grid_waits_for_current_time`). With several grids nobody gets it and every grid
uniformly lags one step. The long comment above that function records what was
measured and why depth-staggered offsets were removed — read it before touching
timing.

`time_step` must be a whole number of seconds: CST's `HelicsMsg.verify` type-checks
`period` against an int default.

### CST monkeypatches

`composegen/monkeypatch.py` replaces `DockerRunner._service`, `DockerRunner.define_yaml`
and `FederateConfig.docker` from the upstream CoSim Toolbox. The generated compose file
therefore carries **no static IPs and no reserved subnet** — federates reach the broker
by the Compose service name `helics`, which is what lets two experiments run at once.
Anything that changes the shape of the generated compose file lives here, not in CST.

### Federate images

`map_params_to_class` in `composegen/load.py` maps a config `class` to an image and
command. Note the classes without their own federate yet: `pv` and `battery` map to the
`house` image because that physics still lives inside the house simulator — declaring
them directly under a grid raises `NotImplementedError` rather than starting a broken
container. `hems`/`controller` are real standalone federates.

`federates/template/` is the starting point for a new federate.
`federates/grid-pypsa/` and `federates/forecasting/` are not wired into tree mode —
they have no entry in `map_params_to_class` and would fall through to the default image.

Federates never construct their HELICS keys; they discover them from the CST federation
config at startup (`_find_pub_key` / `_find_sub_key`, or the key scan in
`house_player/src/load_player.py`), because composegen derives key names from the tree,
not from a federate's own name.

### Data flow at runtime

- Grid nets: infdb → `custom_metadata` → `federates/grid/main.py:load_net_from_metadata`.
- House exogenous datasets: composegen reads the CSV and stores its text in
  `custom_metadata/<house federate>`; the house materializes it at startup. Load-player
  CSVs are the exception — they are passed as a `--timeseries` path.
- Results: written by CST into the timeseries DB, tagged with the run's own timestamped
  scenario name (`<name>_YYYYMMDD_HHMMSS`). There is no `data/output` export path any more.
- Run provenance (git commit, experiment path, full experiment YAML) is merged into the
  scenario document *after* `federation.write_config`, which overwrites it.

## Conventions

### Code

- **Each federate directory is self-contained**: its own Dockerfile, its own
  `requirements.txt`, its own environment. The entry point is invoked by the generated
  compose command, so no packaging metadata is involved — `federates/grid/pyproject.toml`
  is a leftover no build reads, and it declares a different Python version than the image
  it supposedly describes. Exception: `controller` builds from the repo root and reuses
  `federates/house/requirements.txt` and the `house/` package.
- **Dependencies are pinned on purpose** in everything that runs today — `grid`,
  `house`, `house_player`, `broker`, composegen — including EnergySim to a commit. An
  unpinned rebuild would change the simulation without a commit on this side, which makes
  the `git_commit` stored with each run a lie. Bump deliberately. `recorder`, `template`,
  `forecasting` and `grid-pypsa` are still unpinned; pin them before making any of them
  part of a run.
- Code is typed, formatted with black, broken into small functions, and kept free of
  repetition. Prefer library functions over hand-rolled logic.
- **Comments explain why, not what.** Non-obvious decisions carry the incident or
  measurement that motivated them; keep that when editing nearby code.

### Failure behaviour

**Fail loudly over silently defaulting.** Most of the validation in `transform.py` and
`load.py` exists because a run that "succeeded" with stale, zero, or substituted data is
the failure mode this project keeps hitting. Never paper over a missing value with a
fallback that lets the federation run — raise where the problem is, and name it.

### Configuration

Anything that changes per experiment belongs in the experiment YAML, stated once;
anything static belongs in the federate's own folder or in code. In legacy configs,
reference a value with OmegaConf interpolation (`${..path.to.value}`) rather than
repeating it — but see the note above: this does not work in tree configs.

### Git

- Branch off `main` as `<issue-number>-<slug>` (e.g. `54-fix-small-issues-for-mvp`);
  never commit to `main` directly.
- Merge through pull requests. Pull before pushing.
- Run black and ruff before committing.
- Open work is tracked in GitHub issue #56. `docs/archive/issues-to-mvp.md` is a closed,
  archived punch list — read it for context on past fixes, do not add to it.
- `generated/` must stay owned by the invoking user; `run.sh` checks this and prints the
  fix, because containers that once ran as root left files nothing could rewrite.
