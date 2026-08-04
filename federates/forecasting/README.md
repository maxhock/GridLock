# Forecasting federate — not wired in

Aggregates the houses' loads and publishes a predicted total for the grid.
**Not part of any run today.**

It has no entry in `composegen/load.py:map_params_to_class`, so an experiment naming it would fall through to the default image, and `forecasting` is not in `SUPPORTED_TREE_CLASSES` either — `validate_tree` rejects it by name before that can happen.

## What it would do

`forecasting.py` is a client for an external forecasting API: it POSTs the current house loads, the simulation time and the time step, and gets a predicted aggregate load back.
If the API is unreachable it falls back to a plain sum of the current loads.

`main.py` reads a pandapower workbook to find which house loads to subscribe to, then publishes the prediction.

## Before making it part of a run

- Add it to `SUPPORTED_TREE_CLASSES` and `map_params_to_class`, and wire its topics — see [`../template/README.md`](../template/README.md).
- **Pin `requirements.txt`.** It is currently unpinned, which the rest of the project deliberately is not: an unpinned rebuild changes behaviour with no commit on this side.
- Replace the workbook read. Every wired federate gets its grid data from the CST metadata store, and its HELICS keys by discovery from the federation config, rather than from a file path and a static config.
- Decide where the API lives, and what happens when it is down. The silent sum-of-loads fallback is exactly the kind of substituted result this project fails loudly over everywhere else.
