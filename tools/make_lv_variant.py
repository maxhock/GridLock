#!/usr/bin/env python3
"""Derive an LV grid layout whose slack sits at a different voltage than the stock one.

The stock Kerber LV nets in `data/input/` have their `ext_grid` on a 10 kV bus
behind a 10/0.4 kV trafo. Real MV nets here are 20 kV, and an LV grid attached
to an MV load must have the same voltage base as that load's bus (see
`grid_contract.py`), so this rebuilds the same net with a different slack
voltage. Buses, lines, loads and every index stay identical; only the slack bus
`vn_kv` and the trafo's HV winding change.

Usage, in the pinned image (see make_synthetic_mv.py for the docker line):
    python tools/make_lv_variant.py \
        --input data/input/kerber_landnetz_freileitung_1.xlsx \
        --output data/input/kerber_landnetz_freileitung_1_20kv.xlsx \
        --slack-kv 20
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandapower as pp

sys.path.insert(0, str(Path(__file__).resolve().parent))
from grid_contract import ContractViolation, check_attach_all  # noqa: E402
from make_synthetic_mv import build_net as build_synthetic_mv  # noqa: E402

SMOKE_TEST_LOAD_MW = 0.002  # 2 kW per load: enough to move the solver, far from limits
# A tap changer's typical range; beyond it the std type is for another voltage.
MAX_WINDING_DEVIATION = 0.1


def _find_slack_trafo(net: pp.pandapowerNet) -> int:
    """Index of the one trafo fed from the one ext_grid bus, or raise."""
    if len(net.ext_grid) != 1:
        raise ValueError(f"expected exactly one ext_grid, found {len(net.ext_grid)}")
    slack_bus = net.ext_grid.bus.iloc[0]
    fed = net.trafo.index[net.trafo.hv_bus == slack_bus]
    if len(fed) != 1:
        raise ValueError(
            f"expected exactly one trafo with hv_bus on the ext_grid bus "
            f"{slack_bus}, found {len(fed)}"
        )
    return int(fed[0])


def _replace_with_std_type(
    net: pp.pandapowerNet, trafo_idx: int, std_type: str
) -> None:
    """Swap one trafo for a std type, keeping its index, name and buses."""
    row = net.trafo.loc[trafo_idx]
    net.trafo.drop(trafo_idx, inplace=True)
    pp.create_transformer(
        net,
        int(row.hv_bus),
        int(row.lv_bus),
        std_type,
        name=row["name"],
        index=trafo_idx,
    )


def make_variant(
    net: pp.pandapowerNet, slack_kv: float, trafo_std_type: str | None = None
) -> None:
    """Move the slack bus to `slack_kv` and re-rate the trafo to match, in place.

    pandapower has no std type for the stock Kerber trafo ratings (0.1 and 0.16
    MVA) at 20 kV, so by default the trafo keeps its own electrical data and only
    its HV winding is re-rated to `slack_kv`. Pass `trafo_std_type` to use a
    pandapower std type instead; its own `vn_hv_kv` is kept if it is within
    `MAX_WINDING_DEVIATION` of `slack_kv` (a tap changer's range, e.g. a 20 kV
    winding on a 20.6 kV DSO bus) and refused otherwise: a 10 kV winding on a
    20 kV bus still converges, with every LV bus at about 2 pu.
    """
    trafo_idx = _find_slack_trafo(net)
    net.bus.loc[net.ext_grid.bus.iloc[0], "vn_kv"] = slack_kv
    if trafo_std_type is None:
        net.trafo.at[trafo_idx, "vn_hv_kv"] = slack_kv
        net.trafo.at[trafo_idx, "std_type"] = None  # no longer a std type's data
        return
    if trafo_std_type not in pp.available_std_types(net, "trafo").index:
        raise ValueError(f"unknown pandapower trafo std type {trafo_std_type!r}")
    vn_hv_kv = float(pp.load_std_type(net, trafo_std_type, "trafo")["vn_hv_kv"])
    if abs(vn_hv_kv / slack_kv - 1) > MAX_WINDING_DEVIATION:
        raise ValueError(
            f"std type {trafo_std_type!r} has a {vn_hv_kv:g} kV HV winding, more "
            f"than {MAX_WINDING_DEVIATION:.0%} off the {slack_kv:g} kV slack bus"
        )
    _replace_with_std_type(net, trafo_idx, trafo_std_type)


def smoke_test(net: pp.pandapowerNet) -> None:
    """Run a power flow with all loads at 2 kW; raises if it does not converge."""
    probe = pp.copy.deepcopy(net)
    probe.load["p_mw"] = SMOKE_TEST_LOAD_MW
    probe.load["q_mvar"] = 0.0
    probe.load["scaling"] = 1.0
    pp.runpp(probe)


def _write(net: pp.pandapowerNet, output: Path) -> None:
    if output.suffix == ".xlsx":
        pp.to_excel(net, str(output))
    elif output.suffix == ".json":
        pp.to_json(net, str(output))
    else:
        raise ValueError(f"--output must end in .xlsx or .json, got {output.suffix!r}")


def _report_attachment(net: pp.pandapowerNet) -> None:
    """Say whether the variant fits the synthetic MV net; a mismatch is not fatal.

    A variant made for another voltage (e.g. a 20.6 kV DSO net) is legitimate,
    it just cannot attach to the 20 kV synthetic net.
    """
    try:
        count = check_attach_all(build_synthetic_mv(), net)
    except ContractViolation as err:
        print(f"Does not attach to the synthetic 20 kV MV net: {err}")
    else:
        print(f"Contract holds: attaches to all {count} synthetic MV loads")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--slack-kv", type=float, required=True)
    parser.add_argument("--trafo-std-type", default=None)
    args = parser.parse_args()

    net = pp.from_excel(str(args.input))
    make_variant(net, args.slack_kv, args.trafo_std_type)
    smoke_test(net)
    _write(net, args.output)
    trafo = net.trafo.iloc[0]
    print(
        f"Wrote {args.output}: slack {args.slack_kv} kV, trafo "
        f"{trafo.sn_mva} MVA {trafo.vn_hv_kv}/{trafo.vn_lv_kv} kV, "
        f"{len(net.bus)} buses, {len(net.load)} loads, power flow converged"
    )
    _report_attachment(net)


if __name__ == "__main__":
    main()
