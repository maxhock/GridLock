# Writing a new federate

This directory is the starting point for a new federate class.
`main.py` is a working skeleton kept deliberately short; this file is the walkthrough.

For the invariants you have to respect while doing it, see [`AGENTS.md`](../../AGENTS.md).
For what an experiment YAML may then say about your class, see [`docs/experiment-reference.md`](../../docs/experiment-reference.md).

## What CST does, and what you write

The CoSim Toolbox `Federate` base class owns the HELICS federate lifecycle, the time loop, and the data exchange.
You write the physics.

`federate.run(scenario_name, use_meta_db=..., use_data_db=...)` does the whole run: it creates the federate, loops until stop time, and destroys it.
That is what `main.py` calls, and what every federate in this repo calls.
Reach for the three underlying calls (`create_federate` → `run_cosim_loop` → `destroy_federate`) only if you need to do something between them.

Each step, CST requests and is granted a time, fills `self.data_from_federation["inputs"]`, calls your `update_internal_model()`, and publishes whatever you left in `self.data_to_federation["publications"]`.
Both are dicts keyed by the full HELICS key — not flat value dicts, and not attributes on the federate.

## The three steps

### 1. Copy the directory

```bash
cp -r federates/template federates/<class>
```

A federate directory is self-contained: its own `Dockerfile`, its own `requirements.txt`, its own environment.
No packaging metadata is involved — the generated compose file invokes your entry point directly.

**Keep the dependencies pinned.**
`requirements.txt` here pins `cosim-toolbox` and `helics[cli]` to the versions the rest of the repo runs against; pin whatever you add the same way.
An unpinned rebuild changes the simulation without a commit on this side, which makes the `git_commit` stored with each run a lie.

The `Dockerfile` has a `test` stage that runs `pytest` during the build, so a failing test fails the build.
`test.sh` builds this directory along with every other component, which is the only thing standing between the template and silent rot — no experiment routes to it, so nothing else would notice it breaking against a new CST release.
Keep the stage when you copy the directory, and add your component to `test.sh`.

### 2. Implement the federate

Subclass `Federate` (from `cosim_toolbox.sims`).
Exactly one method is mandatory:

- **`update_internal_model()`** — advance one step. Read `self.data_from_federation["inputs"]`, update your state, write `self.data_to_federation["publications"]`. `self.granted_time` is the current simulation time in seconds, `self.period` the time step.
- **`create_federate()`** — optional. Call `super()` first, which reads the federation config from the CST metadata store and registers your publications and subscriptions, then do setup that needs those interfaces to exist. The grid does not override it at all; the template does, only to resolve its interface keys.
- **`on_enter_executing_mode()`** — optional, but usually not. Publish an initial state at `t=0`, so the rest of the federation does not start from nothing. An unpublished `double` input reads as `0.0`, which no subscriber can tell apart from a real zero — so without this, whoever depends on you spends the first step simulating against a value it invented. The grid and the load player both do this.

**Do not construct your HELICS keys.**
composegen derives key names from the experiment tree, not from a federate's own name, so a federate that builds `f"{self.federate_name}/active_power"` will register a key nobody publishes to.
Discover them instead.
`main.py:_find_interface_key` is the one-interface version of this, the same pattern as `federates/house/main.py`'s `_find_pub_key` / `_find_sub_key`; `house_player` scans for a whole family of keys at once (`_build_pub_keys`), and the grid matches the `load_<idx>` segment of every key it was given (`_parse_load_index`).

**Fail loudly.**
If a value you need is missing, raise and name it.
`_find_interface_key` and `get_db_backends_from_env` in `main.py` are both written this way, and the first names what *was* registered in the error.
Most of the validation in this project exists because a run that "succeeded" with stale, zero, or substituted data is the failure mode it keeps hitting.

### 3. Register the class in composegen

A new federate directory on its own does nothing — nothing routes an experiment to it.
Four places, in the order you will hit them:

| Where | What to add |
|---|---|
| `composegen/transform.py` → `SUPPORTED_TREE_CLASSES` | your class name, or `validate_tree` rejects it |
| `composegen/transform.py` → `validate_tree` | a branch naming the `config` keys your class requires |
| `composegen/load.py` → `map_params_to_class` | `{"image": "<dir name>", "command": "python3 main.py"}` |
| `composegen/load.py` → `_wire_grid_child` or `_wire_house_subcomponents` | the HELICS groups between your federate and its parent |

Missing the `map_params_to_class` entry is the quiet one: the lookup falls through to a default image (`cosim-cst:latest`) instead of raising, so you get a container that starts and is not yours.

If your federate injects or draws power, add it to `LOAD_PLACED_CLASSES` in `composegen/load.py` as well.
The grid identifies incoming power purely by the `load_<idx>` segment of the HELICS key, so a federate wired onto a bare `active_power` key is silently dropped from the power flow — it will run, publish, and move no power.

New validation belongs in `transform.py`, new output in `load.py`.
Extract and transform never write and never touch the CST store, which is what makes a bad experiment fail before there is anything to clean up.

## What the generated container gives you

The compose file composegen writes builds your image from `federates/<image>/Dockerfile` with that directory as the build context.
(`house` and `controller` are the exception — they build from the repo root, because the controller reuses the `house/` package.)

Every federate container gets:

- `CST_USE_META_DB` and `CST_USE_DATA_DB`, from the experiment's `general:` block. Read them; do not default them.
- `CST_HOST`, `POSTGRES_HOST`, `POSTGRES_PORT`, `MONGO_HOST`, `MONGO_PORT`.
- `data/` mounted at `/data`, and `generated/` at `/app/meta_store`.
- The broker reachable as the Compose service name `helics`.

### Which arguments you actually get

Your entry point is invoked with whatever `map_params_to_class` puts in `command`, plus arguments composegen appends — but *which* ones depends on where your federate sits in the tree, and this is worth checking before you debug a federate that dies on startup.

A federate declared **under a grid** is built in `load.py` phase 1, which appends `--scenario` and `--federate_name` for every class, whatever its image.
That is the normal case, and it is what `main.py` here expects.

Any **other** tree node falls through to the generic phase 2, which appends those two arguments only for images on a hardcoded list (`grid`, `house_player`, `house`).
A new image is not on that list and gets **no arguments at all** — so `parse_args` raises on the missing `--federate_name`.
If your class is not a grid child, add your image to that branch.

## Running one federate by hand

You do not need the whole federation to debug a federate.
Point it at a JSON metadata store from a previous `composegen` run:

```bash
CST_USE_META_DB=json CST_USE_DATA_DB=json \
  python3 main.py --scenario <name>_<timestamp> --federate_name <dotted.federate.name>
```

Both environment variables are required — `get_db_backends_from_env` raises rather than picking a store for you.
`.vscode/launch.json` has working argument sets for the existing federates.

## Where to look when the template is not enough

`federates/grid/main.py` is the closest thing to this file at full size, and the shape here follows it: module docstring with a `Usage:` line, a private key helper and the federate class, then `parse_args` / `get_db_backends_from_env` / `run_<x>_federate` / `main` under a `Helpers` banner.
`get_db_backends_from_env` is byte-for-byte the same function in grid, house, house_player and controller — copy it, do not reinvent it.
`house_player` is the smallest complete federate; `controller` shows a federate that reads another federate's data out of the CST store.
