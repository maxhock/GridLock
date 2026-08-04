# Writing a new federate

This directory is the starting point for a new federate class.
`main.py` is a working skeleton kept deliberately short; this file is the walkthrough.

For the invariants you have to respect while doing it, see [`AGENTS.md`](../../AGENTS.md).
For what an experiment YAML may then say about your class, see [`docs/experiment-reference.md`](../../docs/experiment-reference.md).

## What CST does, and what you write

The CoSim Toolbox `Federate` base class owns the HELICS federate lifecycle, the time loop, and the data exchange.
You write the physics.

`federate.run(scenario_name, use_meta_db=..., use_data_db=...)` does the whole run: it creates the federate, loops until stop time, and destroys it.
`main.py` spells the three calls out separately (`create_federate` → `run_cosim_loop` → `destroy_federate`) because seeing them once is useful; the federates in this repo call `run()` and let CST sequence them.

Each step, CST fills `self.data_from_federation`, calls your `update_internal_model()`, and publishes whatever you left in `self.data_to_federation`.

## The three steps

### 1. Copy the directory

```bash
cp -r federates/template federates/<class>
```

A federate directory is self-contained: its own `Dockerfile`, its own `requirements.txt`, its own environment.
No packaging metadata is involved — the generated compose file invokes your entry point directly.

**Pin your dependencies.** Everything that runs today is pinned, including EnergySim to a commit, because an unpinned rebuild changes the simulation without a commit on this side and makes the `git_commit` stored with each run a lie.
This template is not pinned, and its `Dockerfile` installs only `helics[cli]` — it never installs `requirements.txt`, so `cosim_toolbox` is missing from the image as it stands.
Fix both before your class is part of a run.

`config.json` and the `helics run --path=runner.json` command in the `Dockerfile` are leftovers of the legacy config format.
There is no `runner.json` here, the generated compose file overrides the command anyway, and a tree-mode federate gets its HELICS configuration from the CST metadata store rather than a static file.
Delete them.

### 2. Implement the federate

Subclass `Federate`.
Exactly one method is mandatory:

- **`update_internal_model()`** — advance one step. Read `self.data_from_federation`, update your state, write `self.data_to_federation`. `self.granted_time` is the current simulation time in seconds.
- **`create_federate()`** — optional. Call `super()` first, which reads the federation config and registers your publications and subscriptions, then do setup that needs those interfaces to exist.
- **`on_enter_executing_mode()`** — optional. Publish an initial state at `t=0`, so the rest of the federation does not start from nothing. The grid and the load player both use it.

**Do not construct your HELICS keys.**
composegen derives key names from the experiment tree, not from a federate's own name, so a federate that builds `f"{self.federate_name}/active_power"` will register a key nobody publishes to.
Discover them instead: `federates/house/main.py` looks up single interfaces by suffix (`_find_pub_key` / `_find_sub_key`), `house_player` scans for a whole family of them (`_build_pub_keys`), and the grid matches the `load_<idx>` segment of every key it was given.

**Fail loudly.**
If a value you need is missing, raise and name it.
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

Your entry point is invoked with whatever `map_params_to_class` puts in `command`, plus the arguments composegen appends — at minimum `--scenario` and `--federate_name`.

## Running one federate by hand

You do not need the whole federation to debug a federate.
Point it at a JSON metadata store from a previous `composegen` run:

```bash
CST_USE_META_DB=json python3 main.py --scenario <name>_<timestamp> --federate_name <dotted.federate.name>
```

`.vscode/launch.json` has working argument sets for the existing federates.
