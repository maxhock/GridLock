# Broker

The HELICS broker every federation needs.
It is not a federate and has no class in an experiment YAML — composegen adds it to the generated compose file automatically.

## What it does

Nothing but provide the `helics_broker` binary.
The command is injected at runtime by the generated compose file, sized with the federate count composegen worked out:

```
helics_broker --federates=<n> --name=<name> --ipv4
```

Federates reach it by the Compose **service name** `helics`.
There are no static IPs and no reserved subnet in the generated compose file — that is what lets two experiments run side by side on one host, and it is done by `composegen/monkeypatch.py`, not by anything here.

## Files

| File | Purpose |
|---|---|
| `Dockerfile` | pinned: HELICS 3.6.1, CST 1.0.1, psycopg2-binary |

The pin is deliberate and matters more than it looks.
The broker version decides which timing flags exist — `--global_disconnect` needs HELICS 3.6+ — and how the federation coordinates time, so an unpinned rebuild can change the behaviour of every run.

## Debugging

`federates/house/start_broker.py` starts a standalone broker on the host, for running federates outside Docker.
