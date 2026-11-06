#!/usr/bin/env python3
"""Build the small synthetic 20 kV MV net committed as `data/input/mv_synthetic_20kv.json`.

This is the committed stand-in for real MV nets in tests and CI. Real DSO grids
are private and stay out of this public repo (see `data/input/private/` in
`.gitignore`); this net has the same shape - a 110 kV infeed, a HV/MV trafo and
radial 20 kV cable feeders with secondary substations on loads - without any
real data. It is deterministic and needs nothing but pandapower.

It is written as JSON on purpose: the committed Kerber layouts are xlsx, so this
file is what exercises the JSON layout path.

The 6 loads (indices 0..5, named `ONS_<n>`) are where LV grids attach. Their
nominal values and the one static sgen are zeroed by the grid federate at import
(`sanitize_net_for_power_flow`); they exist so those paths are exercised.

Run in the pinned image so the file matches the pandapower the federates run:
    docker run --rm --user "$(id -u):$(id -g)" -v "$PWD":/repo -w /repo grid-test \
        python tools/make_synthetic_mv.py
It cross-checks the result against the committed 20 kV Kerber variant, so run
`tools/make_lv_variant.py` first if that file does not exist yet.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandapower as pp

sys.path.insert(0, str(Path(__file__).resolve().parent))
from grid_contract import check_attach_all  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT = REPO_ROOT / "data" / "input" / "mv_synthetic_20kv.json"
LV_VARIANT = REPO_ROOT / "data" / "input" / "kerber_landnetz_freileitung_1_20kv.xlsx"

MV_KV = 20.0
HV_KV = 110.0
TRAFO_STD_TYPE = "25 MVA 110/20 kV"
LINE_STD_TYPE = "NA2XS2Y 1x185 RM/25 12/20 kV"

# Segment lengths in km, busbar outwards: one tuple per feeder, one entry per
# secondary substation on it. Different lengths so the feeders do not look
# identical in a voltage plot.
FEEDER_SEGMENTS_KM = ((0.4, 0.9, 1.6), (0.6, 1.2, 2.0))
# Nominal load per substation in MW, in load-index order.
LOAD_P_MW = (0.1, 0.2, 0.3, 0.15, 0.25, 0.1)
POWER_FACTOR_TAN = 0.33  # q = p * tan(phi), about cos(phi) 0.95
SGEN_P_MW = 0.2


def _add_feeder(
    net: pp.pandapowerNet, busbar: int, feeder: int, segments_km: tuple[float, ...]
) -> list[int]:
    """Add one radial cable feeder; return its substation buses, busbar outwards."""
    buses: list[int] = []
    previous = busbar
    for segment, length_km in enumerate(segments_km, start=1):
        bus = pp.create_bus(net, MV_KV, name=f"F{feeder}_{segment}")
        pp.create_line(
            net,
            previous,
            bus,
            length_km,
            LINE_STD_TYPE,
            name=f"F{feeder}_L{segment}",
        )
        buses.append(bus)
        previous = bus
    return buses


def build_net() -> pp.pandapowerNet:
    """The synthetic MV net: 110 kV slack, one trafo, two feeders, 6 loads."""
    net = pp.create_empty_network(name="mv_synthetic_20kv")
    hv_bus = pp.create_bus(net, HV_KV, name="HV")
    busbar = pp.create_bus(net, MV_KV, name="MV busbar")
    pp.create_ext_grid(net, hv_bus, vm_pu=1.0)
    pp.create_transformer(net, hv_bus, busbar, TRAFO_STD_TYPE, name="HV/MV")

    substation_buses = [
        bus
        for feeder, segments in enumerate(FEEDER_SEGMENTS_KM, start=1)
        for bus in _add_feeder(net, busbar, feeder, segments)
    ]
    for number, (bus, p_mw) in enumerate(zip(substation_buses, LOAD_P_MW), start=1):
        pp.create_load(net, bus, p_mw, p_mw * POWER_FACTOR_TAN, name=f"ONS_{number}")
    pp.create_sgen(net, substation_buses[-1], SGEN_P_MW, name="PV_1")
    return net


def main() -> None:
    net = build_net()
    pp.runpp(net)  # raises LoadflowNotConverged: a net that does not solve is useless
    pp.to_json(net, str(OUTPUT))
    print(
        f"Wrote {OUTPUT.relative_to(REPO_ROOT)}: {len(net.bus)} buses, "
        f"{len(net.load)} loads, {len(net.sgen)} sgen, power flow converged"
    )
    if not LV_VARIANT.exists():
        raise SystemExit(f"{LV_VARIANT} is missing; run tools/make_lv_variant.py first")
    checked = check_attach_all(net, pp.from_excel(str(LV_VARIANT)))
    print(f"Contract holds: {LV_VARIANT.name} attaches to all {checked} MV loads")


if __name__ == "__main__":
    main()
