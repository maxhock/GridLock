"""Grid federate entry point.

Loads a pandapower net from the CST metadata store and runs a
HELICS co-simulation loop exchanging load power / bus voltage data.

Usage:
    python main.py --scenario TestGridScenario --federate_name lv-grid_91301_0
        --net_key lv-grid_91301_0

Repeat ``--federate_name``/``--net_key`` pairs to run several grids in this one
process, one thread each; composegen does that for the grids under one parent.
"""

import argparse
import os
import re
import threading
import traceback
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from functools import partial
from typing import Any, cast

import pandapower as pp
from cosim_toolbox.sims import Federate
from cosim_toolbox.dbms import create_metadata_manager

# ---------------------------------------------------------------------------
# GridFederate
# ---------------------------------------------------------------------------

# The load index is the segment before the quantity, never just the first
# ``/load_<n>/``: keys are ``<grid>/<child>/load_<idx>/<quantity>`` and child ids
# are free-form, so a load player with ``id: load_1`` on load 5 publishes
# ``.../load_1/load_5/active_power``. Matching the first segment put that
# player's power on load 1 while load 5 stayed at 0 W, and the run exited 0.
_LOAD_RE = re.compile(r"/load_(\d+)/[^/]+$")

# pandapower's ZIP split: the share of each load that scales with |V|^2 (Z) or
# |V| (I) instead of staying constant (P).
_ZIP_COLUMNS = (
    "const_z_p_percent",
    "const_i_p_percent",
    "const_z_q_percent",
    "const_i_q_percent",
)

_LOAD_COLUMN_DEFAULTS: dict[str, Any] = {
    "const_z_p_percent": 0.0,
    "const_i_p_percent": 0.0,
    "const_z_q_percent": 0.0,
    "const_i_q_percent": 0.0,
    "scaling": 1.0,
    "in_service": True,
}


def _parse_load_index(key: str) -> int | None:
    """The pandapower load index of a key ending in ``/load_<idx>/<quantity>``."""
    m = _LOAD_RE.search(key)
    return int(m.group(1)) if m else None


def _has_rows(table: Any) -> bool:
    """Whether a pandapower element table exists and holds at least one row."""
    return table is not None and not table.empty


def _zero_static_loads(net: pp.pandapowerNet) -> None:
    """Clear imported load powers and reset every load's ``scaling`` to 1."""
    added_columns: list[str] = []
    for column_name, default_value in _LOAD_COLUMN_DEFAULTS.items():
        if column_name in net.load.columns:
            continue
        net.load[column_name] = default_value
        added_columns.append(column_name)

    # A federate publishes the actual power of its load, and pandapower
    # multiplies p_mw by scaling. Anything but 1.0 would silently change the
    # published value (0.0 would swallow it entirely).
    rescaled = int((net.load["scaling"] != 1.0).sum())
    net.load["scaling"] = 1.0
    # Same reason for the ZIP split: what arrives is the power actually drawn
    # at the voltage the federate saw, so a voltage-dependent share would be
    # applied a second time. For a nested grid that is already resolved
    # through its own power flow: a 100 % constant-Z coupling load at 0.98 pu
    # would draw only 0.9604 of what the LV grid reported.
    voltage_dependent = int((net.load[list(_ZIP_COLUMNS)] != 0.0).any(axis=1).sum())
    net.load[list(_ZIP_COLUMNS)] = 0.0
    net.load["p_mw"] = 0.0
    net.load["q_mvar"] = 0.0

    if added_columns:
        print("Sanitized net.load by adding columns: " + ", ".join(added_columns))
    if rescaled:
        print(
            f"Reset net.load scaling to 1.0 on {rescaled} load(s) whose scaling "
            "differed from 1; this changes what the imported model meant."
        )
    if voltage_dependent:
        print(
            f"Reset the ZIP split to constant power on {voltage_dependent} "
            "load(s) with a voltage-dependent share; federates publish the "
            "power actually drawn."
        )
    print("Reset imported net.load active/reactive powers to zero for co-simulation.")


def _zero_static_sgens(net: pp.pandapowerNet) -> None:
    """Clear imported static generator powers, reporting what was removed."""
    in_service = net.sgen["in_service"].astype(bool)
    nominal_mw = float((net.sgen["p_mw"] * net.sgen["scaling"])[in_service].sum())
    net.sgen["p_mw"] = 0.0
    net.sgen["q_mvar"] = 0.0
    print(
        f"WARNING: zeroed {len(net.sgen)} static sgen(s) with {nominal_mw:.2f} MW "
        "of nominal feed-in; only HELICS federates may drive the grid."
    )


def sanitize_net_for_power_flow(net: pp.pandapowerNet) -> pp.pandapowerNet:
    """Prepare an imported pandapower net for HELICS-driven co-simulation.

    Older or externally generated pandapower JSON payloads may omit ZIP-load
    columns that newer pandapower releases expect during ``runpp``; they are
    added, and every load's ZIP split is set to constant power, because each
    published power is already what the load draws at its voltage.

    Only HELICS federates may drive the grid, so every imported static power
    is cleared here: the federates write the active/reactive power values for
    their assigned load indices before every power-flow step. Static sgens are
    cleared for the same reason; left alone they would inject their constant
    feed-in while every load sits at 0 W. Load ``scaling`` is reset to 1.0
    because the published power is already the actual power.
    """
    if _has_rows(getattr(net, "load", None)):
        _zero_static_loads(net)
    if _has_rows(getattr(net, "sgen", None)):
        _zero_static_sgens(net)
    return net


_ACTIVE = "/active_power"
_REACTIVE = "/reactive_power"
_VOLTAGE = "/voltage"

# net.load column each incoming power key drives.
_POWER_COLUMNS = {_ACTIVE: "p_mw", _REACTIVE: "q_mvar"}


def _is_helics_sentinel(value: float | None) -> bool:
    """Whether HELICS has delivered nothing yet (None, or its huge placeholder)."""
    return value is None or abs(value) > 1e40


@dataclass(frozen=True)
class _GridKeys:
    """What each HELICS key of one grid federate means.

    The direction and suffix decide the role, so composegen needs no extra
    config to couple a grid to a parent grid. The parent's voltage key also
    contains ``/load_<idx>/``, so the load regex alone cannot tell it from a
    child's power key; this classification is the single source of truth.

    Every grid publishes what its slack draws, root or nested: a nested grid's
    slack keys are its parent's load inputs, a root's have no subscriber in
    the federation and exist to be recorded. Only the upstream voltage input
    is what sets a nested grid apart.
    """

    load_inputs: dict[str, tuple[int, str]]  # key -> (load index, net.load column)
    voltage_pubs: dict[str, int]  # key -> load index whose bus voltage it carries
    upstream_voltage: str | None
    slack_p: str
    slack_q: str

    @property
    def has_upstream(self) -> bool:
        """Whether this grid hangs off a parent grid's load."""
        return self.upstream_voltage is not None

    @classmethod
    def classify(
        cls, federate_name: str, input_keys: Iterable[str], pub_keys: Iterable[str]
    ) -> "_GridKeys":
        """Sort the keys into roles, raising on any wiring that is ambiguous."""
        input_keys, pub_keys = list(input_keys), list(pub_keys)
        voltage_inputs = [k for k in input_keys if k.endswith(_VOLTAGE)]
        p_pubs = [k for k in pub_keys if k.endswith(_ACTIVE)]
        q_pubs = [k for k in pub_keys if k.endswith(_REACTIVE)]
        if len(voltage_inputs) > 1:
            raise ValueError(
                f"Grid '{federate_name}' has {len(voltage_inputs)} upstream "
                f"voltage inputs, expected at most one: {voltage_inputs}"
            )
        # Without its slack keys a nested grid leaves the parent silently
        # without its demand, and a root grid's draw goes unrecorded.
        for what, keys in (
            ("slack active_power publication", p_pubs),
            ("slack reactive_power publication", q_pubs),
        ):
            if len(keys) != 1:
                raise ValueError(
                    f"Grid '{federate_name}' has {len(keys)} {what}s, expected "
                    f"exactly one: {keys}"
                )
        return cls(
            load_inputs=cls._load_inputs(input_keys),
            voltage_pubs=cls._voltage_pubs(pub_keys),
            upstream_voltage=voltage_inputs[0] if voltage_inputs else None,
            slack_p=p_pubs[0],
            slack_q=q_pubs[0],
        )

    @staticmethod
    def _load_inputs(input_keys: list[str]) -> dict[str, tuple[int, str]]:
        loads: dict[str, tuple[int, str]] = {}
        for key in input_keys:
            idx = _parse_load_index(key)
            for suffix, column in _POWER_COLUMNS.items():
                if idx is not None and key.endswith(suffix):
                    loads[key] = (idx, column)
        return loads

    @staticmethod
    def _voltage_pubs(pub_keys: list[str]) -> dict[str, int]:
        indices = {key: _parse_load_index(key) for key in pub_keys}
        return {
            key: idx
            for key, idx in indices.items()
            if idx is not None and key.endswith(_VOLTAGE)
        }


class GridFederate(Federate):
    """Pandapower grid federate. Overrides only ``update_internal_model``."""

    def __init__(self, federate_name: str, net: pp.pandapowerNet) -> None:
        """Bind this federate to the net it solves."""
        super().__init__(federate_name)
        self.net = net
        self._grid_keys: _GridKeys | None = None

    @property
    def grid_keys(self) -> _GridKeys:
        """Key roles, classified on first use.

        CST fills the input/publication dicts in ``create_federate``, after
        ``__init__``, so they cannot be classified any earlier.
        """
        if self._grid_keys is None:
            self._grid_keys = _GridKeys.classify(
                self.federate_name,
                self.data_from_federation.get("inputs", {}),
                self.data_to_federation.get("publications", {}),
            )
            if self._grid_keys.has_upstream:
                self._require_single_slack()
        return self._grid_keys

    def _require_single_slack(self) -> None:
        count = len(self.net.ext_grid)
        if count != 1:
            raise ValueError(
                f"Grid '{self.federate_name}' is coupled to a parent grid and "
                f"must have exactly one ext_grid (the slack the parent's voltage "
                f"drives), but its net has {count}."
            )

    def update_internal_model(self) -> None:
        """Apply this step's inputs, solve the power flow, publish the results."""
        keys = self.grid_keys
        self._apply_inputs(keys)
        self._solve()
        self._publish(keys)

    def _apply_inputs(self, keys: _GridKeys) -> None:
        """Write received load powers and the parent's voltage into the net."""
        inputs = self.data_from_federation.get("inputs", {})
        for key, (idx, column) in keys.load_inputs.items():
            value = inputs[key]
            if not _is_helics_sentinel(value):
                self.net.load.at[idx, column] = float(value) / 1e6
        if keys.upstream_voltage is not None:
            voltage = inputs[keys.upstream_voltage]
            # Nothing is published yet at t=0; keep the net's own setpoint.
            if not _is_helics_sentinel(voltage):
                self.net.ext_grid["vm_pu"] = float(voltage)

    def _solve(self) -> None:
        # A diverged power flow must fail the run. Swallowing it leaves the
        # previous step's voltages in data_to_federation, which are then
        # published as if they were current - the co-simulation completes
        # and reports success while every downstream federate reacts to a
        # grid state that was never solved.
        #
        # Controllers stored with the net (e.g. a tap changer holding an MV
        # busbar at its setpoint) run inside the power flow, so a net that
        # carries them behaves as it does in a standalone pandapower study.
        try:
            pp.runpp(self.net, numba=True, run_control=not self.net.controller.empty)
        except Exception as exc:
            p_total_w = float(self.net.load["p_mw"].sum()) * 1e6
            q_total_var = float(self.net.load["q_mvar"].sum()) * 1e6
            raise RuntimeError(
                f"Power flow did not converge at simulation time "
                f"{self.granted_time}s for grid '{self.federate_name}' "
                f"(applied load: P={p_total_w:.1f} W, Q={q_total_var:.1f} VAr "
                f"across {len(self.net.load)} loads). pandapower reported: "
                f"{exc}"
            ) from exc

        print(
            f"Power flow converged at time {self.granted_time} "
            f"(applied P={float(self.net.load['p_mw'].sum()) * 1e6:.1f} W)."
        )

    def _publish(self, keys: _GridKeys) -> None:
        """Publish per-load bus voltages and the slack's draw."""
        publications = self.data_to_federation["publications"]
        for key, idx in keys.voltage_pubs.items():
            bus = int(self.net.load.at[idx, "bus"])
            publications[key] = float(self.net.res_bus.at[bus, "vm_pu"])
        # What the slack delivers is the parent's load: positive when drawn
        # from upstream (a load's sign convention), and it includes the trafo
        # and line losses inside this grid, not just the child loads. A nested
        # grid has one ext_grid; a root may have several, which together are
        # what it draws from upstream.
        publications[keys.slack_p] = float(self.net.res_ext_grid.p_mw.sum()) * 1e6
        publications[keys.slack_q] = float(self.net.res_ext_grid.q_mvar.sum()) * 1e6

    def on_enter_executing_mode(self) -> None:
        """Run initial power flow at t=0 with load values published by load players."""
        self.get_data_from_federation()
        self.update_internal_model()
        self.send_data_to_federation()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    """Read the scenario and federate name composegen put on the command line."""
    parser = argparse.ArgumentParser(description="Grid federate using CST")
    parser.add_argument(
        "--scenario",
        type=str,
        required=True,
        help="CST scenario name (e.g. TestGridScenario)",
    )
    parser.add_argument(
        "--federate_name",
        type=str,
        action="append",
        required=True,
        help=(
            "Federate name matching meta_store entry (e.g. lv-grid_91301_0); "
            "repeat it to run several grids"
        ),
    )
    parser.add_argument(
        "--net_key",
        type=str,
        action="append",
        required=True,
        help=(
            "custom_metadata document holding this grid's pandapower net, "
            "written by the infdb stage; one per --federate_name, in order"
        ),
    )
    args, _ = parser.parse_known_args()
    if len(args.federate_name) != len(args.net_key):
        parser.error(
            f"got {len(args.federate_name)} --federate_name but "
            f"{len(args.net_key)} --net_key; they pair up in order"
        )
    return args


def get_db_backends_from_env() -> tuple[str, str]:
    """Read DB backends from environment variables set by composegen."""
    use_meta_db = os.getenv("CST_USE_META_DB")
    use_data_db = os.getenv("CST_USE_DATA_DB")

    if not use_meta_db or not use_data_db:
        raise ValueError(
            "Missing DB backend env vars. Expected CST_USE_META_DB and "
            "CST_USE_DATA_DB from experiment general config."
        )

    return use_meta_db, use_data_db


def load_net_from_metadata(
    net_key: str,
    use_meta_db: str = "json",
) -> pp.pandapowerNet:
    """Read the pandapower net named `net_key` out of `custom_metadata`.

    The key is separate from the federate name so that several grid federates
    can be built from one template net.
    """
    md_kwargs = {"backend": use_meta_db}
    if use_meta_db == "json":
        md_kwargs["location"] = "meta_store"

    md_mgr = create_metadata_manager(**md_kwargs)
    md_mgr.connect()
    try:
        data = md_mgr.read("custom_metadata", net_key)
    finally:
        md_mgr.disconnect()

    if not data:
        raise FileNotFoundError(
            f"No grid data found in metadata store for net key '{net_key}'. "
            f"Ensure infdb data setup has run."
        )

    net_json = data.get("net_json")
    if not net_json:
        raise ValueError(f"Grid metadata for net key '{net_key}' has no net_json.")
    net = cast(pp.pandapowerNet, pp.from_json_string(net_json))
    sanitize_net_for_power_flow(net)
    print(f"Loaded net '{net_key}': {len(net.bus)} buses, {len(net.load)} loads")
    return net


def run_grid_federate(
    federate_name: str,
    net: pp.pandapowerNet,
    scenario_name: str,
    use_meta_db: str,
    use_data_db: str,
) -> None:
    """Run one grid federate: CST's ``run()`` does create, loop, destroy."""
    federate = GridFederate(federate_name, net)
    federate.run(
        scenario_name,
        use_meta_db=use_meta_db,
        use_data_db=use_data_db,
    )


def _exit_on_federate_error(args: threading.ExceptHookArgs) -> None:
    """End the whole process when one federate thread fails.

    Its siblings would otherwise block forever on a time grant that waits for
    the dead federate, and the container would hang instead of failing the run.
    """
    print(f"Federate thread '{args.thread.name if args.thread else '?'}' failed:")
    traceback.print_exception(args.exc_type, args.exc_value, args.exc_traceback)
    os._exit(1)


def run_concurrently(runs: dict[str, Callable[[], None]]) -> None:
    """Run each federate on a thread of its own, named after it, and wait for all.

    One process per grid loaded pandapower, numba and CST once per grid,
    about 166 MB each: the 256 grids of the full ÜZW federation needed ~42 GB
    before simulating anything. Threads share those imports. HELICS releases
    the GIL while a federate waits for its time grant, so the others proceed.
    """
    threading.excepthook = _exit_on_federate_error
    threads = [threading.Thread(target=run, name=name) for name, run in runs.items()]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()


def main(
    scenario_name: str | None = None,
    grids: dict[str, str] | None = None,
) -> None:
    """Fetch each grid's net and run them all, taking the names from the CLI unless given.

    ``grids`` maps each federate name to the net key it runs on.
    """
    if scenario_name is None:
        args = parse_args()
        scenario_name = args.scenario
        grids = dict(zip(args.federate_name, args.net_key))

    if scenario_name is None or not grids:
        raise ValueError("scenario_name and at least one grid are required")

    use_meta_db, use_data_db = get_db_backends_from_env()

    nets = {
        federate_name: load_net_from_metadata(net_key, use_meta_db)
        for federate_name, net_key in grids.items()
    }
    run_concurrently(
        {
            federate_name: partial(
                run_grid_federate,
                federate_name,
                net,
                scenario_name,
                use_meta_db,
                use_data_db,
            )
            for federate_name, net in nets.items()
        }
    )


if __name__ == "__main__":
    main()
