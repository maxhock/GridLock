"""What the pandapower parity checks share: GridLock's own helpers and Timescale.

`verify_pandapower_parity.py` and `verify_mvlv_parity.py` both compare a run
against pure pandapower, and both do it with GridLock's own sanitiser and CSV
reader loaded straight from the federates, so the reference follows what the
federates really do. They also read the same Timescale tables back.

Needs the environment the repo's .venv provides (pandapower, psycopg2,
cosim-toolbox: `federates/grid/main.py` imports it at import time).
"""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parent.parent


def load_module(name: str, path: Path) -> ModuleType:
    """Load a component's script-style module by exact path.

    Every federate directory is self-contained and unpackaged (see AGENTS.md),
    so `federates/grid/main.py` and `federates/house_player/src/load_player.py`
    can't both be reached with a plain `import` without their bare module
    names ("main", "src") colliding with the same-named modules other
    components already carry. Loading by path sidesteps that entirely.
    """
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_grid_main = load_module(
    "gridlock_tools_grid_main", REPO_ROOT / "federates" / "grid" / "main.py"
)
_load_player = load_module(
    "gridlock_tools_load_player",
    REPO_ROOT / "federates" / "house_player" / "src" / "load_player.py",
)
sanitize_net_for_power_flow = _grid_main.sanitize_net_for_power_flow
load_timeseries = _load_player.load_timeseries
lookup_power = _load_player.lookup_power


def add_postgres_arguments(parser: argparse.ArgumentParser) -> None:
    """The Timescale connection options both verifiers take."""
    parser.add_argument("--pg-host", default="localhost")
    parser.add_argument("--pg-port", type=int, default=5432)
    parser.add_argument("--pg-db", required=True)
    parser.add_argument("--pg-user", required=True)
    parser.add_argument("--pg-password", required=True)


def postgres_dsn(args: argparse.Namespace) -> dict:
    """psycopg2 connection arguments from `add_postgres_arguments`' options."""
    return {
        "host": args.pg_host,
        "port": args.pg_port,
        "dbname": args.pg_db,
        "user": args.pg_user,
        "password": args.pg_password,
    }
