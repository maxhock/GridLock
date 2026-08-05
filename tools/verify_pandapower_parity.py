#!/usr/bin/env python3
"""Cross-check the grid federate's HELICS-driven power flow against an
independent pure-pandapower replica of the same network and load timeseries.

Runs on the host, not in Docker (like the rest of `tools/`): it reuses
federates/house_player's CSV loader and federates/grid's net-sanitizing
helper directly rather than reimplementing them, so the comparison is
against GridLock's actual behavior, not a guess at it. Needs the same
environment the repo's .venv already provides (pandapower, psycopg2).
"""

from __future__ import annotations

import argparse
import glob
import sys
from pathlib import Path

import pandapower as pp
import psycopg2
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "federates" / "house_player"))
sys.path.insert(0, str(REPO_ROOT / "federates" / "grid"))

from src.load_player import load_timeseries, lookup_power  # noqa: E402
from main import sanitize_net_for_power_flow  # noqa: E402


TOLERANCE_PU = 1e-6


def _load_experiment(config_path: Path) -> dict:
    with open(config_path) as f:
        return yaml.safe_load(f)


def _latest_scenario_file(analysis_name: str) -> Path:
    """Find the scenario JSON the run just wrote (use_meta_db: "json" for this
    experiment keeps this a local file read instead of a Mongo query)."""
    pattern = str(REPO_ROOT / "generated" / "scenarios" / f"{analysis_name}_*.json")
    matches = sorted(glob.glob(pattern))
    if not matches:
        raise FileNotFoundError(f"No scenario files found matching {pattern}")
    return Path(matches[-1])


def _independent_pandapower_run(
    layout_path: Path,
    csv_path: Path,
    load_indices: list[int],
    time_step: int,
    duration: int,
) -> dict[float, dict[int, float]]:
    """Replay the same timeseries directly against pandapower, bypassing HELICS/CST."""
    raw_net = pp.from_excel(layout_path)
    # Round-trip through JSON exactly like infdb -> grid does at runtime
    # (databases/infdb/main.py:load_local_layout, federates/grid/main.py
    # :load_net_from_metadata), so this exercises the same net the real
    # federate solves, not just an equivalent one.
    net = pp.from_json_string(pp.to_json(raw_net))
    sanitize_net_for_power_flow(net)

    timeseries = load_timeseries(str(csv_path))

    voltages: dict[float, dict[int, float]] = {}
    for sim_time in range(0, duration + time_step, time_step):
        p_w, q_var = lookup_power(timeseries, float(sim_time))

        # Mirrors grid/main.py:update_internal_model's unit conversion exactly.
        for idx in load_indices:
            net.load.at[idx, "p_mw"] = p_w / 1e6
            net.load.at[idx, "q_mvar"] = q_var / 1e6

        pp.runpp(net, numba=True)

        step_voltages: dict[int, float] = {}
        for idx in load_indices:
            bus = int(net.load.at[idx, "bus"])
            step_voltages[idx] = float(net.res_bus.at[bus, "vm_pu"])
        voltages[float(sim_time)] = step_voltages

    return voltages


def _recorded_voltages(
    scenario: str, analysis_name: str, grid_fed_name: str, dsn: dict
) -> dict[float, dict[int, float]]:
    """Read the grid federate's own published voltages back out of Postgres."""
    conn = psycopg2.connect(**dsn)
    try:
        with conn.cursor() as cur:
            cur.execute(
                f'SELECT sim_time, data_name, data_value '
                f'FROM "{analysis_name}Analysis".hdt_double '
                f"WHERE scenario = %s AND federate = %s AND data_name LIKE %s",
                (scenario, grid_fed_name, f"{grid_fed_name}/load_%/voltage"),
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    voltages: dict[float, dict[int, float]] = {}
    for sim_time, data_name, data_value in rows:
        idx = int(data_name.split("/load_")[1].split("/")[0])
        voltages.setdefault(float(sim_time), {})[idx] = float(data_value)
    return voltages


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--experiment",
        default=str(REPO_ROOT / "config" / "experiment-e2e-kerber-parity.yml"),
    )
    parser.add_argument("--pg-host", default="localhost")
    parser.add_argument("--pg-port", type=int, default=5432)
    parser.add_argument("--pg-db", required=True)
    parser.add_argument("--pg-user", required=True)
    parser.add_argument("--pg-password", required=True)
    args = parser.parse_args()

    experiment = _load_experiment(Path(args.experiment))
    general = experiment["general"]
    federation = experiment["federation"]

    analysis_name = general["name"]
    time_step = int(general["time_step"])
    duration = int(general["duration"])
    grid_fed_name = federation["id"]
    layout_path = REPO_ROOT / "data" / "input" / federation["config"]["layout"]

    load_child = federation["sub_federates"][0]
    csv_path = REPO_ROOT / "data" / "input" / load_child["config"]["electrical_load"]

    raw_net = pp.from_excel(layout_path)
    load_indices = list(raw_net.load.index)

    print(f"Replaying {csv_path.name} against {layout_path.name} in pure pandapower...")
    expected = _independent_pandapower_run(
        layout_path, csv_path, load_indices, time_step, duration
    )

    scenario_file = _latest_scenario_file(analysis_name)
    scenario = scenario_file.stem
    print(f"Comparing against scenario '{scenario}' recorded by the real run...")

    dsn = {
        "host": args.pg_host,
        "port": args.pg_port,
        "dbname": args.pg_db,
        "user": args.pg_user,
        "password": args.pg_password,
    }
    actual = _recorded_voltages(scenario, analysis_name, grid_fed_name, dsn)

    mismatches = []
    for sim_time, expected_by_idx in expected.items():
        actual_by_idx = actual.get(sim_time)
        if actual_by_idx is None:
            mismatches.append(f"t={sim_time}: no recorded voltages found")
            continue
        for idx, expected_v in expected_by_idx.items():
            actual_v = actual_by_idx.get(idx)
            if actual_v is None:
                mismatches.append(f"t={sim_time} load_{idx}: no recorded voltage")
                continue
            diff = abs(actual_v - expected_v)
            if diff > TOLERANCE_PU:
                mismatches.append(
                    f"t={sim_time} load_{idx}: expected {expected_v:.8f} pu, "
                    f"got {actual_v:.8f} pu (diff {diff:.2e})"
                )

    if mismatches:
        print(f"FAILED: {len(mismatches)} mismatch(es):")
        for m in mismatches[:20]:
            print(f"  - {m}")
        return 1

    total_checks = sum(len(v) for v in expected.values())
    print(
        f"PASSED: {total_checks} voltage(s) across {len(expected)} step(s) "
        f"matched within {TOLERANCE_PU} pu."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
