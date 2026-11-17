"""Reinforcing grid federate entry point.

The grid federate (``federates/grid/main.py``, copied into this image as
``grid_federate.py``) with one addition: after each step's power flow it
checks line and transformer loading and bus voltages, reinforces the net with
eDisGo's measures until every limit holds (``reinforce.py``), re-solves, and
publishes the reinforced state. Measures stay in the net for the rest of the
run. When the run ends, the reinforced net and the list of measures are
written to ``custom_metadata/<scenario>__<federate>__reinforced``.

Usage:
    python main.py --scenario TestGridScenario --federate_name lv-grid_0
        --net_key lv-grid_0
        --reinforcement max_loading_percent=100,voltage_band=split,earthwork=rural,max_iterations=20

Repeat the ``--federate_name``/``--net_key``/``--reinforcement`` triple to run
several grids in this one process, one thread each.
"""

import argparse
from dataclasses import dataclass
from functools import partial
from typing import Any

import pandapower as pp
from grid_federate import (
    GridFederate,
    get_db_backends_from_env,
    load_net_from_metadata,
    run_concurrently,
)
from reinforce import Settings, State, as_records, reinforce

# Suffixes of the source-only keys composegen registers for a reinforcing
# grid. None ends in /active_power, /reactive_power or /voltage, or contains
# /load_<n>/, so the grid's own key classification never sees them.
METRIC_SUFFIXES = (
    "max_line_loading",
    "max_trafo_loading",
    "min_vm",
    "max_vm",
    "reinforcement_cost",
)

REPORT_SUFFIX = "__reinforced"


def report_key(scenario_name: str, federate_name: str) -> str:
    """custom_metadata document holding one run's reinforced net and measures.

    Keyed by the run's scenario, as its timeseries are: keyed by the federate
    alone, every run of an experiment overwrote the last run's report, and no
    report could be matched to the timeseries it came from. Not the net key
    either: a root grid's net is stored under its own federate name.
    """
    return f"{scenario_name}__{federate_name}{REPORT_SUFFIX}"


def _max_loading(net: pp.pandapowerNet, element: str) -> float:
    """Highest loading of any in-service element, 0 for a net without one."""
    if net[element].empty:
        return 0.0
    in_service = net[element]["in_service"].astype(bool)
    loading = net[f"res_{element}"]["loading_percent"][in_service].dropna()
    return float(loading.max()) if not loading.empty else 0.0


@dataclass(frozen=True)
class _MetricKeys:
    """The publication key of each metric, found by suffix."""

    keys: dict[str, str]

    @classmethod
    def find(cls, federate_name: str, pub_keys: list[str]) -> "_MetricKeys":
        keys: dict[str, str] = {}
        for suffix in METRIC_SUFFIXES:
            matches = [k for k in pub_keys if k.endswith(f"/{suffix}")]
            if len(matches) != 1:
                raise ValueError(
                    f"Reinforcing grid '{federate_name}' has {len(matches)} "
                    f"'{suffix}' publications, expected exactly one: {matches}"
                )
            keys[suffix] = matches[0]
        return cls(keys)


class ReinforcingGridFederate(GridFederate):
    """Grid federate that reinforces its net whenever a limit is violated."""

    def __init__(
        self, federate_name: str, net: pp.pandapowerNet, settings: Settings
    ) -> None:
        """Bind this federate to its net and the experiment's reinforcement rules."""
        super().__init__(federate_name, net)
        self.settings = settings
        self.state = State()
        self._metric_keys: _MetricKeys | None = None

    @property
    def metric_keys(self) -> _MetricKeys:
        """Metric keys, found on first use, once CST has filled the publications."""
        if self._metric_keys is None:
            self._metric_keys = _MetricKeys.find(
                self.federate_name,
                list(self.data_to_federation.get("publications", {})),
            )
        return self._metric_keys

    def update_internal_model(self) -> None:
        """Solve, reinforce until the limits hold, then publish what was solved last."""
        keys = self.grid_keys
        self._apply_inputs(keys)
        self._solve()
        # The metrics describe what this step's load did to the grid as it
        # stood; measured afterwards they would sit within the limits by
        # construction and the timeseries could never show a violation.
        metrics = self._metrics()
        # Publishing the unreinforced state would hand children voltages from
        # a grid that no longer exists: the measures are permanent.
        for measure in reinforce(
            self.net, self.settings, self.state, self._solve, self.granted_time
        ):
            print(
                f"Reinforced {measure.element} {measure.index} at "
                f"{measure.time}s ({measure.reason}, {measure.severity:.3f}): "
                f"{measure.action} +{measure.added} {measure.std_type}, "
                f"{measure.cost_keur:.1f} kEUR"
            )
        self._publish(keys)
        metrics["reinforcement_cost"] = self.state.cost_keur
        publications = self.data_to_federation["publications"]
        for suffix, key in self.metric_keys.keys.items():
            publications[key] = metrics[suffix]

    def _metrics(self) -> dict[str, float]:
        """Worst loading and voltages of the net as just solved."""
        vm = self.net.res_bus["vm_pu"].dropna()
        return {
            "max_line_loading": _max_loading(self.net, "line"),
            "max_trafo_loading": _max_loading(self.net, "trafo"),
            "min_vm": float(vm.min()),
            "max_vm": float(vm.max()),
        }

    def report(self) -> dict[str, Any]:
        """The reinforced net and everything found and done, as stored after the run."""
        return {
            "scenario": self.scenario_name,
            "federate_name": self.federate_name,
            "settings": self.settings.__dict__,
            "total_cost_keur": self.state.cost_keur,
            "violations": as_records(self.state.violations),
            "measures": as_records(self.state.measures),
            "net_json": pp.to_json(self.net),
        }

    def destroy_federate(self) -> None:
        """Store the report, then leave the federation.

        Written here because CST disconnects the metadata manager in its own
        ``destroy_federate``.
        """
        if self.metadata_manager is None:
            raise RuntimeError(
                f"Grid '{self.federate_name}' has no metadata manager to store its "
                f"reinforcement report in."
            )
        key = report_key(self.scenario_name, self.federate_name)
        # CST reports a failed write by returning False, not by raising; a run
        # whose report is lost would otherwise end as if it had succeeded.
        # No overwrite: the key is unique per run, so an existing document
        # means two federates claim one report.
        if not self.metadata_manager.write(
            "custom_metadata", key, self.report(), overwrite=False
        ):
            raise RuntimeError(
                f"Grid '{self.federate_name}' could not store its reinforcement "
                f"report under custom_metadata/{key}."
            )
        print(
            f"Stored {len(self.state.measures)} reinforcement measure(s), "
            f"{self.state.cost_keur:.1f} kEUR, under custom_metadata/{key}"
        )
        super().destroy_federate()


def parse_args() -> argparse.Namespace:
    """Read the grid triples composegen put on the command line."""
    parser = argparse.ArgumentParser(description="Reinforcing grid federate")
    parser.add_argument("--scenario", type=str, required=True)
    for flag, what in (
        ("--federate_name", "federate name; repeat it to run several grids"),
        ("--net_key", "custom_metadata document of the net, one per grid"),
        ("--reinforcement", "key=value,... settings, one per grid"),
    ):
        parser.add_argument(flag, type=str, action="append", required=True, help=what)
    args, _ = parser.parse_known_args()
    counts = {
        len(args.federate_name),
        len(args.net_key),
        len(args.reinforcement),
    }
    if len(counts) != 1:
        parser.error(
            f"got {len(args.federate_name)} --federate_name, {len(args.net_key)} "
            f"--net_key and {len(args.reinforcement)} --reinforcement; they pair "
            f"up in order"
        )
    return args


def run_reinforcing_grid_federate(
    federate_name: str,
    net: pp.pandapowerNet,
    settings: Settings,
    scenario_name: str,
    use_meta_db: str,
    use_data_db: str,
) -> None:
    """Run one reinforcing grid federate: CST's ``run()`` does create, loop, destroy."""
    federate = ReinforcingGridFederate(federate_name, net, settings)
    federate.run(scenario_name, use_meta_db=use_meta_db, use_data_db=use_data_db)


def main() -> None:
    """Fetch each grid's net and run them all, one thread per grid."""
    args = parse_args()
    use_meta_db, use_data_db = get_db_backends_from_env()
    grids = zip(args.federate_name, args.net_key, args.reinforcement)
    runs = {
        federate_name: partial(
            run_reinforcing_grid_federate,
            federate_name,
            load_net_from_metadata(net_key, use_meta_db),
            Settings.parse(spec),
            args.scenario,
            use_meta_db,
            use_data_db,
        )
        for federate_name, net_key, spec in grids
    }
    run_concurrently(runs)


if __name__ == "__main__":
    main()
