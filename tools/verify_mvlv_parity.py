#!/usr/bin/env python3
"""Check an MV/LV co-simulation against ONE merged pandapower net.

The experiment (`config/experiment-e2e-mvlv-parity.yml`) drives every load with a
constant profile. The MV grid and the LV grids exchange voltage and power with a
lag of one step per grid level, so with constant loads they settle after a few
steps on the fixed point of the same network solved as one: the LV slacks fused
onto the MV load buses they hang from. This script builds that merged net
(`mvlv_merge.py`), solves it once, and compares it with what the federates
published at the LAST simulated step:

* every MV `load_<i>/voltage` against the MV bus of the merged net;
* every LV `load_<j>/voltage` against the LV bus of the merged net;
* every LV grid's `load_<mv_idx>/active_power` and `reactive_power` against what
  flows into the fused LV part (`mvlv_merge.power_into_part`).

A series that is missing at any step fails: a federate that stopped publishing
must not pass by omission. Likewise a run that has not reached steady state
(the last two steps differ) fails with that reason, so "add steps" is told apart
from a wrong reference. Runs on the host, like the rest of `tools/`; needs the
environment the repo's .venv provides.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import pandapower as pp
import psycopg2
import yaml

from mvlv_merge import MergedNet, build_merged_net, power_into_part
from parity_common import (
    REPO_ROOT,
    add_postgres_arguments,
    load_timeseries,
    lookup_power,
    postgres_dsn,
    sanitize_net_for_power_flow,
)

INPUT_DIR = REPO_ROOT / "data" / "input"
TOLERANCE_PU = 1e-6
# Relative, against the expected power, with a floor so a power that is
# legitimately ~0 is not compared against 0.
TOLERANCE_POWER_REL = 1e-3
POWER_FLOOR_W = 1.0
# The reference has to tell a working coupling from a broken one: without the
# coupling the voltages must differ by well over the tolerance.
MIN_SENSITIVITY_FACTOR = 100


@dataclass(frozen=True)
class LvGrid:
    """One LV grid federate of the experiment."""

    federate: str
    mv_load_idx: int
    layout: Path
    csv: Path


@dataclass(frozen=True)
class Experiment:
    """The parts of the experiment YAML the reference needs."""

    analysis: str
    start_time: int
    end_time: int
    time_step: int
    mv_federate: str
    mv_layout: Path
    mv_csv: Path
    lv_grids: list[LvGrid]

    @property
    def sim_times(self) -> list[float]:
        """Every simulated step, first to last."""
        return [
            float(t) for t in range(self.start_time, self.end_time + 1, self.time_step)
        ]


def key_prefix(federate: str) -> str:
    """HELICS key prefix of a federate: its tree name with `.` written as `/`."""
    return federate.replace(".", "/")


@dataclass(frozen=True)
class Check:
    """One compared quantity: what GridLock published against the reference."""

    key: str
    unit: str
    expected: float
    actual: float
    previous: float
    tolerance: float

    @property
    def diff(self) -> float:
        """Absolute deviation from the reference."""
        return abs(self.actual - self.expected)

    @property
    def settled(self) -> bool:
        """Whether the last two steps agree, i.e. steady state was reached."""
        return abs(self.actual - self.previous) <= self.tolerance


def _as_list(placement: object) -> list[int]:
    if isinstance(placement, int):
        return [placement]
    if isinstance(placement, list) and all(isinstance(i, int) for i in placement):
        return placement
    raise ValueError(f"Expected an int or a list of ints, got {placement!r}")


def _load_player_csv(grid_node: dict, what: str) -> Path:
    """The CSV of the single `fill` load player under a grid node."""
    players = [n for n in grid_node.get("sub_federates", []) if n["class"] == "load"]
    unsupported = [
        n["id"] for n in grid_node.get("sub_federates", []) if n["class"] != "load"
    ]
    if unsupported:
        raise ValueError(
            f"{what}: only load players are constant, cannot build a reference "
            f"for {unsupported}"
        )
    if len(players) != 1 or players[0]["config"]["placement"] != "fill":
        raise ValueError(f"{what}: expected exactly one load player with 'fill'")
    return INPUT_DIR / players[0]["config"]["electrical_load"]


def _lv_federate_name(root_id: str, child_id: str, mv_load_idx: int) -> str:
    """Mirror composegen's `_expand_child_instances` naming for a nested grid."""
    base_id = re.sub(r"_\d+$", "", child_id)
    return f"{root_id}.{base_id}_{mv_load_idx}"


def parse_experiment(path: Path) -> Experiment:
    """Read the experiment YAML into what the reference needs.

    Raises on anything but a root grid with a `fill` load player and nested LV
    grids that each carry one: those are the only shapes with constant loads.
    """
    with open(path) as f:
        config = yaml.safe_load(f)
    general, root = config["general"], config["federation"]
    mv_csv = _load_player_csv(
        {"sub_federates": [n for n in root["sub_federates"] if n["class"] == "load"]},
        "MV grid",
    )
    lv_grids = [
        LvGrid(
            federate=_lv_federate_name(root["id"], node["id"], idx),
            mv_load_idx=idx,
            layout=INPUT_DIR / node["config"]["layout"],
            csv=_load_player_csv(node, f"LV grid '{node['id']}'"),
        )
        for node in root["sub_federates"]
        if node["class"] == "grid"
        for idx in _as_list(node["config"]["placement"])
    ]
    unsupported = [
        n["id"] for n in root["sub_federates"] if n["class"] not in ("load", "grid")
    ]
    if unsupported:
        raise ValueError(f"Only load players and LV grids are supported: {unsupported}")
    return Experiment(
        analysis=general["name"],
        start_time=int(general.get("start_time") or 0),
        end_time=int(general["end_time"]),
        time_step=int(general["time_step"]),
        mv_federate=root["id"],
        mv_layout=INPUT_DIR / root["config"]["layout"],
        mv_csv=mv_csv,
        lv_grids=lv_grids,
    )


def _roundtripped(layout: Path) -> pp.pandapowerNet:
    """Load a net the way infdb stores it and the grid federate reads it back.

    Through JSON, then sanitised, exactly like `databases/infdb/main.py` ->
    `federates/grid/main.py:load_net_from_metadata`.
    """
    # infdb and extract accept the suffix in any case (`NET.JSON`).
    is_json = layout.suffix.lower() == ".json"
    raw = pp.from_json(layout) if is_json else pp.from_excel(layout)
    net = pp.from_json_string(pp.to_json(raw))
    sanitize_net_for_power_flow(net)
    return net


def _apply_constant(
    net: pp.pandapowerNet, loads: list[int], csv: Path, t: float
) -> None:
    """Write a profile's power onto `loads` as grid/main.py does (W -> MW)."""
    p_w, q_var = lookup_power(load_timeseries(str(csv)), t)
    for idx in loads:
        net.load.at[idx, "p_mw"] = p_w / 1e6
        net.load.at[idx, "q_mvar"] = q_var / 1e6


def build_reference(experiment: Experiment, sim_time: float) -> MergedNet:
    """The merged net with every load at its constant value, solved once."""
    attachments = {g.mv_load_idx: _roundtripped(g.layout) for g in experiment.lv_grids}
    merged = build_merged_net(_roundtripped(experiment.mv_layout), attachments)
    _apply_constant(merged.net, merged.mv_load_ids, experiment.mv_csv, sim_time)
    for grid in experiment.lv_grids:
        part = merged.parts[grid.mv_load_idx]
        _apply_constant(merged.net, list(part.load_ids.values()), grid.csv, sim_time)
    pp.runpp(merged.net, numba=True)
    return merged


def _decoupled_deviation(
    experiment: Experiment, merged: MergedNet, sim_time: float
) -> float:
    """Largest voltage change the coupling causes, to show the check can see it.

    MV side: the MV net alone with the LV-attached loads at 0 W. LV side: each LV
    net alone with its slack at its own 1.0 pu setpoint.
    """
    vm = merged.net.res_bus["vm_pu"]
    deviation = 0.0
    mv_only = _roundtripped(experiment.mv_layout)
    _apply_constant(mv_only, merged.mv_load_ids, experiment.mv_csv, sim_time)
    pp.runpp(mv_only, numba=True)
    for idx in mv_only.load.index:
        alone = mv_only.res_bus.at[mv_only.load.at[idx, "bus"], "vm_pu"]
        deviation = max(deviation, abs(alone - vm[merged.mv_bus_of_load(int(idx))]))
    for grid in experiment.lv_grids:
        lv_only = _roundtripped(grid.layout)
        _apply_constant(lv_only, list(lv_only.load.index), grid.csv, sim_time)
        pp.runpp(lv_only, numba=True)
        for idx in lv_only.load.index:
            alone = lv_only.res_bus.at[lv_only.load.at[idx, "bus"], "vm_pu"]
            coupled = vm[merged.lv_bus_of_load(grid.mv_load_idx, int(idx))]
            deviation = max(deviation, abs(alone - coupled))
    return deviation


def recorded_series(
    experiment: Experiment, scenario: str, dsn: dict
) -> dict[str, dict[float, float]]:
    """Every value the grid federates published, by key, then simulation time."""
    federates = [experiment.mv_federate] + [g.federate for g in experiment.lv_grids]
    conn = psycopg2.connect(**dsn)
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT sim_time, data_name, data_value "
                f'FROM "{experiment.analysis}Analysis".hdt_double '
                f"WHERE scenario = %s AND federate = ANY(%s)",
                (scenario, federates),
            )
            rows = cur.fetchall()
    finally:
        conn.close()
    series: dict[str, dict[float, float]] = defaultdict(dict)
    for sim_time, data_name, data_value in rows:
        if float(sim_time) in series[data_name]:
            raise ValueError(f"{data_name} recorded twice at t={sim_time}")
        series[data_name][float(sim_time)] = float(data_value)
    return series


def _expected_values(
    experiment: Experiment, merged: MergedNet
) -> list[tuple[str, str, float, float]]:
    """(key, unit, reference value, tolerance) for everything that is compared."""
    net = merged.net
    expected: list[tuple[str, str, float, float]] = []
    for idx in sorted(merged.mv_load_ids + list(merged.parts)):
        bus = merged.mv_bus_of_load(idx)
        expected.append(
            (
                f"{key_prefix(experiment.mv_federate)}/load_{idx}/voltage",
                "pu",
                float(net.res_bus.at[bus, "vm_pu"]),
                TOLERANCE_PU,
            )
        )
    for grid in experiment.lv_grids:
        part = merged.parts[grid.mv_load_idx]
        for lv_idx in part.load_ids:
            bus = merged.lv_bus_of_load(grid.mv_load_idx, lv_idx)
            expected.append(
                (
                    f"{key_prefix(grid.federate)}/load_{lv_idx}/voltage",
                    "pu",
                    float(net.res_bus.at[bus, "vm_pu"]),
                    TOLERANCE_PU,
                )
            )
        p_w, q_var = power_into_part(merged, grid.mv_load_idx)
        for suffix, unit, value in (
            ("active_power", "W", p_w),
            ("reactive_power", "VAr", q_var),
        ):
            expected.append(
                (
                    f"{key_prefix(grid.federate)}/load_{grid.mv_load_idx}/{suffix}",
                    unit,
                    value,
                    TOLERANCE_POWER_REL * max(abs(value), POWER_FLOOR_W),
                )
            )
    return expected


def compare(
    experiment: Experiment,
    expected: list[tuple[str, str, float, float]],
    series: dict[str, dict[float, float]],
) -> tuple[list[Check], list[str]]:
    """Pair every expected key with what was recorded; collect missing series."""
    steps = experiment.sim_times
    checks: list[Check] = []
    missing: list[str] = []
    for key, unit, value, tolerance in expected:
        recorded = series.get(key, {})
        absent = [t for t in steps if t not in recorded]
        if absent:
            missing.append(
                f"{key}: no value at {len(absent)} of {len(steps)} step(s), "
                f"first missing t={absent[0]:.0f}"
            )
            continue
        checks.append(
            Check(key, unit, value, recorded[steps[-1]], recorded[steps[-2]], tolerance)
        )
    return checks, missing


def print_table(checks: list[Check]) -> None:
    """One row per compared key."""
    width = max(len(c.key) for c in checks)
    print(f"{'key':<{width}}  {'reference':>16}  {'recorded':>16}  {'|diff|':>10}  ")
    for c in checks:
        status = "ok" if c.diff <= c.tolerance and c.settled else "FAIL"
        print(
            f"{c.key:<{width}}  {c.expected:>12.8f} {c.unit:<3}"
            f"  {c.actual:>12.8f} {c.unit:<3}  {c.diff:>10.2e}  {status}"
        )


def violations(checks: list[Check]) -> list[str]:
    """What is wrong with the comparison, each with its reason."""
    problems: list[str] = []
    for c in checks:
        if not c.settled:
            problems.append(
                f"{c.key}: not at steady state, last two steps differ by "
                f"{abs(c.actual - c.previous):.2e} {c.unit}; run more steps"
            )
        if c.diff > c.tolerance:
            problems.append(
                f"{c.key}: recorded {c.actual:.8f} {c.unit}, merged net "
                f"{c.expected:.8f} {c.unit} (diff {c.diff:.2e} > {c.tolerance:.2e})"
            )
    return problems


def main() -> int:
    """Build the reference, compare it with the run, exit non-zero on a failure."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--experiment",
        default=str(REPO_ROOT / "config" / "experiment-e2e-mvlv-parity.yml"),
    )
    parser.add_argument(
        "--scenario",
        required=True,
        help=(
            "Exact scenario name the run wrote, not guessed: two runs can both "
            "leave a MvLvParity_* scenario behind."
        ),
    )
    add_postgres_arguments(parser)
    args = parser.parse_args()

    experiment = parse_experiment(Path(args.experiment))
    last = experiment.sim_times[-1]
    print(
        f"Solving the merged net: {experiment.mv_layout.name} + "
        f"{len(experiment.lv_grids)} LV grid(s), constant loads at t={last:.0f}s..."
    )
    merged = build_reference(experiment, last)
    expected = _expected_values(experiment, merged)

    sensitivity = _decoupled_deviation(experiment, merged, last)
    needed = MIN_SENSITIVITY_FACTOR * TOLERANCE_PU
    print(
        f"Coupling moves voltages by up to {sensitivity:.2e} pu "
        f"(needs > {needed:.0e} for this check to see a broken coupling)."
    )
    if sensitivity <= needed:
        print("FAILED: the experiment is too weakly loaded to test the coupling.")
        return 1

    print(f"Comparing against scenario '{args.scenario}' (last step t={last:.0f}s)...")
    series = recorded_series(experiment, args.scenario, postgres_dsn(args))
    checks, missing = compare(experiment, expected, series)
    if checks:
        print_table(checks)
    problems = missing + violations(checks)
    if problems:
        print(f"FAILED: {len(problems)} problem(s):")
        for problem in problems[:30]:
            print(f"  - {problem}")
        return 1
    print(
        f"PASSED: {len(checks)} value(s) matched the merged net "
        f"(voltage within {TOLERANCE_PU} pu, power within "
        f"{TOLERANCE_POWER_REL:.1%}), series complete over "
        f"{len(experiment.sim_times)} step(s)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
