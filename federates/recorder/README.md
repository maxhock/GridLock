# Recorder — not usable

`helics_recorder` capture, left over from the legacy config format.
**Do not build on this.**

`recorder` is still accepted as a class name and still has an entry in `map_params_to_class`, but that entry is a bare `helics_recorder` with no arguments — no `--capture`, no `--output`, no `--broker`.
The arguments only ever came from `DEFAULT_COMMAND_TEMPLATES` on the deprecated legacy path, and the `Dockerfile`'s own `CMD` points at `/config/tmp/recorder_runner.json`, a runner file nothing in stage 4 writes.

Results do not come from here.
Every federate writes its published values into the CST timeseries database itself, tagged with the run's scenario name; `databases/db-access.txt` explains how to query them.

If you want a recorder in tree mode, it needs the same treatment as any other new class — see [`../template/README.md`](../template/README.md) — plus a real command and pinned dependencies. Its `requirements` are currently just an unpinned `helics[cli]` install in the `Dockerfile`.
