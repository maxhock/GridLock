# PyPSA grid federate — not wired in

The same job as `federates/grid`, solved with [PyPSA](https://pypsa.org/) instead of pandapower.
**Not part of any run today.**

It has no entry in `composegen/load.py:map_params_to_class`, and `grid-pypsa` is not a class name `validate_tree` accepts — the class in an experiment YAML is `grid`, which maps to the pandapower image.

## What it would do

`utils.py` (`PyPSANetworkBuilder`) converts a pandapower workbook into a PyPSA network; `main.py` runs it as a HELICS federate, exchanging load power and bus voltages the same way the pandapower grid does.

## Before making it part of a run

- Decide how a run picks a solver. Two grid implementations answering to the same `grid` class needs a config key and a second entry in `map_params_to_class`; the alternative is replacing the pandapower federate rather than adding to it.
- **Pin `requirements.txt`.** It is currently unpinned. For a grid federate this matters more than most: the solver version decides what "converged" means, and the `git_commit` stored with every run is supposed to describe what actually ran.
- Take the net from the CST metadata store instead of a workbook path. The infdb stage already writes it there, which is what lets a location-resolved grid work at all.
- Match the pandapower federate's two hard-won behaviours: identify incoming power by the `load_<idx>` key segment, and raise on divergence instead of republishing the previous step's voltages.

See [`../template/README.md`](../template/README.md) for the registration checklist, and [`../grid/README.md`](../grid/README.md) for what the wired grid federate does.
